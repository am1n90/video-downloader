"""Этап 0.2, замеры 5-6: mpv / VLC / ffprobe через HTTP Range-сервер,
пока раздача ещё качается (локальный сид с ограничением отдачи).

Каждый сценарий — свежая папка сохранения (ничего не скачано заранее).
  mpv:     JSON IPC (named pipe, два соединения: команды и события)
  VLC:     HTTP-интерфейс (status.json), config — в папке portable рядом
           с vlc.exe (не в %APPDATA%)
  ffprobe: -show_format -show_streams по URL, сверка с исходным файлом
Плееры — portable в %TEMP%\\vd-torrent-proto\\players; видео/звук на
null-выходы (окна на экране владельца не появляются).

Метрики: до первого кадра, перемотка на 80%, подвисания (paused-for-cache
у mpv; у VLC — позиция не растёт > 1 с), дорожки/субтитры MKV, число
HTTP-запросов/байт, ожидание кусков.
"""
import argparse
import base64
import json
import os
import queue
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from common import MB, PROTO_TMP, Report
import stream_server as ss

PLAYERS = os.path.join(PROTO_TMP, "players")
MPV = os.path.join(PLAYERS, "mpv", "mpv.exe")
VLC_DIR = os.path.join(PLAYERS, "vlc-3.0.23")
VLC = os.path.join(VLC_DIR, "vlc.exe")
FFPROBE = os.path.join(PROTO_TMP, "bin", "ffprobe.exe")
SEED_ROOT = os.path.join(PROTO_TMP, "seed")
LOG_DIR = os.path.join(PROTO_TMP, "player-logs")
SCENARIOS = ["ffprobe:.mkv", "ffprobe:.mp4", "mpv:.mkv", "mpv:.mp4",
             "vlc:.mkv", "vlc:.mp4"]


def rounded(x, nd=2):
    return None if x is None else round(x, nd)


def stall_summary(intervals, t_first, seek_window):
    """Подвисания после первого кадра, вне окна перемотки."""
    kept = []
    for start, end in intervals:
        if t_first is None or start < t_first + 0.5:
            continue
        if seek_window and start < seek_window[1] and end > seek_window[0]:
            continue
        kept.append(end - start)
    return {"stalls": len(kept), "stall_total_s": round(sum(kept), 2),
            "stall_max_s": round(max(kept), 2) if kept else 0.0}


# ------------------------------------------------------------------ mpv

class MpvIpc:
    def __init__(self, pipe_name, timeout=20):
        path = "\\\\.\\pipe\\" + pipe_name
        self.cmd = self._open(path, timeout)
        self.evt = self._open(path, timeout)
        self.events = queue.Queue()
        self._rid = 0

    @staticmethod
    def _open(path, timeout):
        deadline = time.monotonic() + timeout
        while True:
            try:
                return open(path, "r+b", buffering=0)
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)

    @staticmethod
    def _write(f, obj):
        f.write((json.dumps(obj) + "\n").encode("utf-8"))

    def start_events(self, props):
        # observe — ДО запуска читающего потока: синхронный pipe не даёт
        # писать в handle, пока другой поток блокирован на чтении
        for i, name in enumerate(props, 1):
            self._write(self.evt, {"command": ["observe_property", i, name]})
        threading.Thread(target=self._read_events, daemon=True).start()

    def _read_events(self):
        buf = b""
        while True:
            try:
                chunk = self.evt.read(4096)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if "event" in msg:
                    self.events.put((time.monotonic(), msg))

    def command(self, *args):
        self._rid += 1
        rid = self._rid
        self._write(self.cmd, {"command": list(args), "request_id": rid})
        buf = b""
        while True:
            chunk = self.cmd.read(4096)
            if not chunk:
                raise EOFError("mpv closed IPC")
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("request_id") == rid:
                    return msg

    def get(self, prop):
        return self.command("get_property", prop).get("data")


def run_mpv(url, is_mkv, watch_s, after_seek_s, log_path, n):
    pipe = f"vd-mpv-{os.getpid()}-{n}"
    cmd = [MPV, "--no-config", "--vo=null", "--ao=null", "--keep-open=yes",
           "--idle=no", f"--input-ipc-server=\\\\.\\pipe\\{pipe}",
           f"--log-file={log_path}", "--msg-level=all=v", url]
    t0 = time.monotonic()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    res = {}
    intervals = []
    stall_start = [None]
    try:
        ipc = MpvIpc(pipe)
        ipc.start_events(["paused-for-cache"])

        def consume(until, pred=None):
            while True:
                left = until - time.monotonic()
                if left <= 0:
                    return None
                try:
                    t, msg = ipc.events.get(timeout=min(left, 0.5))
                except queue.Empty:
                    continue
                if msg.get("event") == "property-change" and \
                        msg.get("name") == "paused-for-cache":
                    if msg.get("data") is True and stall_start[0] is None:
                        stall_start[0] = t
                    elif msg.get("data") is False and stall_start[0] is not None:
                        intervals.append((stall_start[0], t))
                        stall_start[0] = None
                if msg.get("event") == "end-file":
                    res["end_file"] = msg
                if pred and pred(msg):
                    return t

        t_loaded = consume(t0 + 90, lambda m: m.get("event") == "file-loaded")
        t_first = consume(t0 + 180,
                          lambda m: m.get("event") == "playback-restart")
        res["file_loaded_s"] = rounded(t_loaded and t_loaded - t0)
        res["first_frame_s"] = rounded(t_first and t_first - t0)
        if t_first is None:
            res["error"] = "no playback-restart"
            return res
        duration = ipc.get("duration")
        res["duration_s"] = rounded(duration, 1)
        consume(time.monotonic() + watch_s)
        res["pos_before_seek_s"] = rounded(ipc.get("time-pos"), 1)

        t_seek_sent = time.monotonic()
        ipc.command("seek", 80, "absolute-percent")
        t_seek = consume(t_seek_sent + 180,
                         lambda m: m.get("event") == "playback-restart")
        res["seek_80_s"] = rounded(t_seek and t_seek - t_seek_sent)
        consume(time.monotonic() + after_seek_s)
        res["pos_after_seek_s"] = rounded(ipc.get("time-pos"), 1)

        if is_mkv:
            tracks = ipc.get("track-list") or []
            res["tracks"] = [(t.get("type"), t.get("id"), t.get("lang"))
                             for t in tracks]
            ipc.command("set_property", "aid", 2)
            ipc.command("set_property", "sid", 2)
            consume(time.monotonic() + 3)
            audio = ipc.get("current-tracks/audio") or {}
            sub = ipc.get("current-tracks/sub") or {}
            p1 = ipc.get("time-pos")
            consume(time.monotonic() + 3)
            p2 = ipc.get("time-pos")
            res["switched_audio_lang"] = audio.get("lang")
            res["switched_sub_lang"] = sub.get("lang")
            res["plays_after_switch"] = bool(p1 is not None and p2 is not None
                                             and p2 > p1)
        if stall_start[0] is not None:
            intervals.append((stall_start[0], time.monotonic()))
        res.update(stall_summary(intervals, t_first,
                                 (t_seek_sent, t_seek or time.monotonic())))
        try:
            ipc.command("quit")
        except (OSError, EOFError):
            pass
        return res
    finally:
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


# ------------------------------------------------------------------ VLC

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_vlc(url, is_mkv, watch_s, after_seek_s, log_path):
    os.makedirs(os.path.join(VLC_DIR, "portable"), exist_ok=True)
    port = free_port()
    pwd = secrets.token_hex(8)
    auth = "Basic " + base64.b64encode(f":{pwd}".encode()).decode()
    cmd = [VLC, "-I", "dummy", "--dummy-quiet", "--extraintf=http",
           "--http-host=127.0.0.1", f"--http-port={port}",
           f"--http-password={pwd}", "--vout=dummy", "--aout=dummy",
           "--no-video-title-show", "--file-logging",
           f"--logfile={log_path}", "--verbose=2", url]

    def status(query=""):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/requests/status.json"
            + (("?" + query) if query else ""),
            headers={"Authorization": auth})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)

    t0 = time.monotonic()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    res = {}
    samples = []                       # (t, state, position)
    try:
        def poll(until, pred=None):
            while time.monotonic() < until:
                try:
                    s = status()
                    samples.append((time.monotonic(), s.get("state"),
                                    float(s.get("position") or 0.0), s))
                    if pred and pred(samples):
                        return samples[-1][0]
                except OSError:
                    pass
                time.sleep(0.2)
            return None

        def started(sm):
            if len(sm) < 2:
                return False
            (_, st1, p1, _), (_, st2, p2, _) = sm[-2], sm[-1]
            return st2 == "playing" and p2 > p1 > 0 or \
                (st2 == "playing" and p2 > 0 and p1 == 0 and p2 > p1)

        t_first = poll(t0 + 180, started)
        res["first_frame_s"] = rounded(t_first and t_first - t0)
        if t_first is None:
            res["error"] = "position never advanced"
            return res
        last = samples[-1][3]
        res["duration_s"] = last.get("length")
        poll(time.monotonic() + watch_s)

        t_seek_sent = time.monotonic()
        status("command=seek&val=80%25")
        seek_start_index = len(samples)

        def after_seek(sm):
            tail = sm[seek_start_index:]
            return len(tail) >= 2 and tail[-2][2] >= 0.79 and \
                tail[-1][2] > tail[-2][2]

        t_seek = poll(t_seek_sent + 180, after_seek)
        res["seek_80_s"] = rounded(t_seek and t_seek - t_seek_sent)
        poll(time.monotonic() + after_seek_s)
        res["pos_after_seek"] = round(samples[-1][2], 3)

        if is_mkv:
            info = (samples[-1][3].get("information") or {}).get("category") \
                or {}
            res["streams_info"] = {k: v for k, v in info.items()
                                   if k != "meta"}

        # подвисание: позиция не растёт > 1 с при state == playing
        intervals = []
        frozen_since = None
        for i in range(1, len(samples)):
            t, st, pos, _ = samples[i]
            if st == "playing" and pos <= samples[i - 1][2]:
                if frozen_since is None:
                    frozen_since = samples[i - 1][0]
            else:
                if frozen_since is not None and t - frozen_since > 1.0:
                    intervals.append((frozen_since, t))
                frozen_since = None
        res.update(stall_summary(intervals, t_first,
                                 (t_seek_sent, t_seek or time.monotonic())))
        try:
            status("command=pl_stop")
        except OSError:
            pass
        return res
    finally:
        proc.kill()
        proc.wait(10)


# -------------------------------------------------------------- ffprobe

def probe_summary(data):
    return {"streams": [[s.get("codec_type"), s.get("codec_name"),
                         (s.get("tags") or {}).get("language")]
                        for s in data.get("streams", [])],
            "duration": round(float(data["format"]["duration"]), 1)}


def run_ffprobe(url, src_path):
    base = [FFPROBE, "-v", "error", "-show_format", "-show_streams",
            "-of", "json"]
    t0 = time.monotonic()
    r = subprocess.run(base + [url], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    dt = time.monotonic() - t0
    ref = subprocess.run(base + [src_path], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=120)
    res = {"seconds": round(dt, 2), "exit": r.returncode}
    if r.returncode != 0:
        res["stderr"] = r.stderr[-400:]
        return res
    got = probe_summary(json.loads(r.stdout))
    exp = probe_summary(json.loads(ref.stdout))
    res.update(same_as_source=got == exp, streams=got["streams"],
               duration=got["duration"])
    return res


# ----------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--torrent", default=ss.DEFAULT_TORRENT)
    ap.add_argument("--seed-port", type=int, default=6890)
    ap.add_argument("--only", default=",".join(SCENARIOS))
    ap.add_argument("--watch-seconds", type=int, default=60)
    ap.add_argument("--after-seek-seconds", type=int, default=20)
    ap.add_argument("--readahead", type=int, default=16)
    ap.add_argument("--head-tail-mb", type=int, default=8)
    args = ap.parse_args()

    report = Report("probe_players")
    os.makedirs(LOG_DIR, exist_ok=True)
    appdata_vlc = os.path.join(os.environ.get("APPDATA", ""), "vlc")
    report.record(event="start", watch_s=args.watch_seconds,
                  readahead=args.readahead, head_tail_mb=args.head_tail_mb,
                  appdata_vlc_before=os.path.isdir(appdata_vlc))

    for n, scenario in enumerate(args.only.split(",")):
        player, ext = scenario.split(":")
        save = ss.new_save_dir(f"{player}{ext.strip('.')}")
        streamer = ss.open_local(report, args.torrent, args.seed_port, save,
                                 args.readahead)
        srv = None
        try:
            streamer.wait_metadata()
            idx = streamer.choose_file(ext)
            size = streamer.prepare_file(idx, args.head_tail_mb)
            rel = streamer.ti.files().file_path(idx)
            srv, token = ss.start_server(streamer, report)
            url = ss.url_for(srv, token, streamer.ti, idx)
            report.log(f"=== {scenario}: {rel} ({size // MB} MB)")
            stamp = time.strftime("%H%M%S")
            log_path = os.path.join(LOG_DIR, f"{player}-{ext.strip('.')}-"
                                             f"{stamp}.log")
            t = time.monotonic()
            if player == "mpv":
                res = run_mpv(url, ext == ".mkv", args.watch_seconds,
                              args.after_seek_seconds, log_path, n)
            elif player == "vlc":
                res = run_vlc(url, ext == ".mkv", args.watch_seconds,
                              args.after_seek_seconds, log_path)
            else:
                res = run_ffprobe(url, os.path.join(SEED_ROOT, rel))
            st = streamer.h.status()
            size_done = streamer.h.file_progress()[idx]
            report.record(event="scenario", scenario=scenario, file=rel,
                          wall_s=round(time.monotonic() - t, 1),
                          file_done_pct=round(100 * size_done / size, 1),
                          rate_mb_s=round(st.download_payload_rate / MB, 2),
                          http=dict(streamer.counters,
                                    bytes_mb=round(streamer.counters["bytes"]
                                                   / MB, 1)),
                          log=log_path if player != "ffprobe" else None,
                          **res)
        except Exception as exc:  # сценарий упал — фиксируем и дальше
            report.record(event="scenario_failed", scenario=scenario,
                          error=repr(exc))
        finally:
            if srv is not None:
                srv.shutdown()
            streamer.close()
            shutil.rmtree(save, ignore_errors=True)

    report.record(event="done", appdata_vlc_after=os.path.isdir(appdata_vlc),
                  alerts=report.alert_counts())


if __name__ == "__main__":
    main()
