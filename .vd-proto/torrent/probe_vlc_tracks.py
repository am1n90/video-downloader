"""Этап 0.2: переключение аудиодорожки и субтитров MKV в VLC через сервер.

HTTP-интерфейс VLC 3 не показывает текущую дорожку, поэтому — RC-интерфейс
(TCP 127.0.0.1): `atrack` / `strack` печатают список дорожек, текущая
помечена `*`; `atrack N` / `strack N` переключают. Проверяем, что отметка
переместилась на выбранную дорожку и воспроизведение продолжается
(`get_time` растёт). Сид: local_seed.py (порт 6890).
"""
import argparse
import os
import re
import shutil
import socket
import subprocess
import time

from common import PROTO_TMP, Report
import probe_players as pp
import stream_server as ss

TRACK_LINE = re.compile(r"^\|\s*(-?\d+)\s*-\s*(.*?)(\s\*)?\s*$")


class VlcRc:
    def __init__(self, port, timeout=30):
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), 5)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        self.sock.settimeout(0.3)
        self._read(2.0)

    def _read(self, max_wait):
        """Читать, пока VLC пишет; конец ответа — 0.3 с тишины."""
        data = b""
        end = time.monotonic() + max_wait
        while time.monotonic() < end:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                if data:
                    break
                continue
            if not chunk:
                break
            data += chunk
        return data.decode("utf-8", "replace")

    def cmd(self, line, max_wait=3.0):
        self.sock.sendall((line + "\n").encode("utf-8"))
        return self._read(max_wait)

    def seconds(self):
        m = re.search(r"(\d+)", self.cmd("get_time"))
        return int(m.group(1)) if m else None


def parse_tracks(text):
    tracks = []
    for raw in text.splitlines():
        m = TRACK_LINE.match(raw.strip("> \r"))
        if m:
            tracks.append({"id": int(m.group(1)), "label": m.group(2).strip(),
                           "current": bool(m.group(3))})
    return tracks


def current_id(tracks):
    cur = [t["id"] for t in tracks if t["current"]]
    return cur[0] if cur else None


def switch(rc, kind, report):
    """kind: atrack | strack. Выбрать реальную дорожку, отличную от текущей."""
    before = parse_tracks(rc.cmd(kind))
    cur = current_id(before)
    candidates = [t["id"] for t in before if t["id"] >= 0 and t["id"] != cur]
    if not candidates:
        return {"kind": kind, "before": before, "error": "no candidate"}
    target = candidates[-1]
    rc.cmd(f"{kind} {target}")
    time.sleep(2)
    after = parse_tracks(rc.cmd(kind))
    return {"kind": kind, "before": before, "target": target,
            "current_after": current_id(after), "after": after,
            "ok": current_id(after) == target}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--torrent", default=ss.DEFAULT_TORRENT)
    ap.add_argument("--seed-port", type=int, default=6890)
    ap.add_argument("--vlc-dir", default=pp.VLC_DIR)
    args = ap.parse_args()

    report = Report("probe_vlc_tracks")
    os.makedirs(os.path.join(args.vlc_dir, "portable"), exist_ok=True)
    save = ss.new_save_dir("vlctracks")
    streamer = ss.open_local(report, args.torrent, args.seed_port, save)
    srv = proc = None
    try:
        streamer.wait_metadata()
        idx = streamer.choose_file(".mkv")
        streamer.prepare_file(idx)
        srv, token = ss.start_server(streamer, report)
        url = ss.url_for(srv, token, streamer.ti, idx)
        port = pp.free_port()
        log_path = os.path.join(PROTO_TMP, "player-logs",
                                f"vlc-tracks-{time.strftime('%H%M%S')}.log")
        proc = subprocess.Popen(
            [os.path.join(args.vlc_dir, "vlc.exe"), "-I", "dummy",
             "--dummy-quiet", "--extraintf=rc", f"--rc-host=127.0.0.1:{port}",
             "--rc-quiet", "--vout=dummy", "--aout=dummy",
             "--no-video-title-show", "--file-logging",
             f"--logfile={log_path}", "--verbose=2", url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rc = VlcRc(port)

        t0 = time.monotonic()
        playing = None
        prev = None
        while time.monotonic() - t0 < 90:
            s = rc.seconds()
            if s is not None and prev is not None and s > prev:
                playing = round(time.monotonic() - t0, 1)
                break
            prev = s if s is not None else prev
            time.sleep(1)
        report.record(event="playing", seconds_to_time_advance=playing)
        if playing is None:
            report.record(event="failed", error="get_time never advanced")
            return

        audio = switch(rc, "atrack", report)
        report.record(event="switch", **audio)
        subs = switch(rc, "strack", report)
        report.record(event="switch", **subs)
        t1 = rc.seconds()
        time.sleep(3)
        t2 = rc.seconds()
        plays = t1 is not None and t2 is not None and t2 > t1
        report.record(event="summary", audio_ok=audio.get("ok"),
                      subs_ok=subs.get("ok"), plays_after_switch=plays,
                      log=log_path)
        print(f"ИТОГ: audio_ok={audio.get('ok')} subs_ok={subs.get('ok')} "
              f"plays_after_switch={plays}", flush=True)
        try:
            rc.cmd("quit", 1.0)
        except OSError:
            pass
    finally:
        if proc is not None:
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if srv is not None:
            srv.shutdown()
        streamer.close()
        shutil.rmtree(save, ignore_errors=True)


if __name__ == "__main__":
    main()
