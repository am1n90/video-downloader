# -*- coding: utf-8 -*-
"""Внешние .srt через тот же сервер — с НАСТОЯЩИМ mpv (сессия 2.3).

Офлайн-тест (test_torrent_stream.py, сценарий 22) проверяет наш слой:
какие субтитры выбраны, что они отданы тем же сервером и что stop()
закрывает их вместе с видео. Здесь проверяется то, чего офлайн-тест
знать не может: НАСТОЯЩИЙ плеер действительно берёт субтитры по нашей
ссылке и показывает их как дорожку.

Раздача локальная (127.0.0.1), видео генерирует ffmpeg, субтитры —
два .srt рядом. Плеер — портативный mpv из прототипа Этапа 0.2, без
окна; дорожки спрашиваем у него по IPC (track-list).

  build-venv\\Scripts\\python.exe .vd-tests\\check_subtitles_live.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import libtorrent as lt

import config

config.save = lambda settings: None

import player
import torrent_engine as te
import torrent_stream as ts

MB = 1024 * 1024
BASE = os.path.join(tempfile.gettempdir(), "vd-subs-live")
SRC_ROOT = os.path.join(BASE, "источник")
CONTENT = os.path.join(SRC_ROOT, "Фильм")
FFMPEG = os.path.join(ROOT, "dist", "VideoDownloader", "ffmpeg.exe")
VIDEO = os.path.join(CONTENT, "Фильм.mkv")
PROTO_MPV = os.path.join(tempfile.gettempdir(), "vd-torrent-proto",
                         "players", "mpv", "mpv.exe")

PASS, FAIL = [], []


def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details, flush=True)


def say(text):
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


class Mpv:
    """Тот же клиент IPC, что в check_long_stream.py: строго
    «запрос-ответ» в одной нити (иначе блокирующее чтение именованного
    канала на Windows не даёт пройти записи — см. там же)."""

    def __init__(self, name):
        self.path = rf"\\.\pipe\{name}"
        self.pipe = None
        self._buf = b""

    def connect(self, timeout=30.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                self.pipe = open(self.path, "r+b", buffering=0)
                return True
            except OSError:
                time.sleep(0.2)
        return False

    def get(self, prop, tries=60):
        if self.pipe is None:
            return None
        try:
            self.pipe.write((json.dumps({"command": ["get_property", prop],
                                         "request_id": 1}) + "\n").encode())
        except Exception:
            return None
        for _ in range(tries):
            while b"\n" not in self._buf:
                try:
                    chunk = self.pipe.read(4096)
                except Exception:
                    return None
                if not chunk:
                    return None
                self._buf += chunk
            line, _, self._buf = self._buf.partition(b"\n")
            try:
                msg = json.loads(line.strip().decode("utf-8", "replace"))
            except ValueError:
                continue
            if msg.get("event"):
                continue
            return msg.get("data") if msg.get("error") == "success" else None
        return None

    def close(self):
        try:
            if self.pipe is not None:
                self.pipe.close()
        except Exception:
            pass


def main():
    if not os.path.isfile(PROTO_MPV):
        print(f"mpv не найден: {PROTO_MPV}")
        return 2
    if not os.path.isfile(FFMPEG):
        print("ffmpeg не найден — запустите build.bat или build_ffmpeg.ps1")
        return 2

    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(CONTENT, exist_ok=True)
    ff = os.path.join(BASE, "ffmpeg.exe")
    shutil.copy2(FFMPEG, ff)
    res = subprocess.run(
        [ff, "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=1280x720:rate=25:duration=20",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
         "-c:v", "libx264", "-preset", "veryfast", "-b:v", "3M",
         "-c:a", "aac", "-shortest", VIDEO], capture_output=True)
    check("подготовка: видео сгенерировано", res.returncode == 0,
          res.stderr.decode("utf-8", "replace")[:200])
    if res.returncode:
        return 1
    # Два файла субтитров рядом: по правилу «имя начинается с имени
    # видео» должны подойти оба
    for name, text in (("Фильм.rus.srt", "проверка субтитров"),
                       ("Фильм.eng.srt", "subtitle check")):
        with open(os.path.join(CONTENT, name), "w", encoding="utf-8") as f:
            f.write("1\n00:00:01,000 --> 00:00:19,000\n" + text + "\n")
    # И один посторонний — он не должен попасть в подборку
    with open(os.path.join(CONTENT, "заметки.txt"), "w",
              encoding="utf-8") as f:
        f.write("не субтитры")

    torrent = os.path.join(BASE, "subs.torrent")
    fs = lt.file_storage()
    lt.add_files(fs, CONTENT)
    ct = lt.create_torrent(fs, 256 * 1024, lt.create_torrent.v1_only)
    lt.set_piece_hashes(ct, SRC_ROOT)
    with open(torrent, "wb") as f:
        f.write(lt.bencode(ct.generate()))
    ih = lt.torrent_info(torrent).info_hashes().v1.to_bytes().hex()

    local = {"enable_dht": False, "enable_lsd": False, "enable_upnp": False,
             "enable_natpmp": False}
    seed = lt.session(dict(local, listen_interfaces="127.0.0.1:0"))
    atp = lt.add_torrent_params()
    atp.ti = lt.torrent_info(torrent)
    atp.save_path = SRC_ROOT
    atp.flags = atp.flags | lt.torrent_flags.seed_mode
    sh = seed.add_torrent(atp)
    end = time.monotonic() + 20
    while str(sh.status().state) != "seeding" and time.monotonic() < end:
        time.sleep(0.1)
    sh.set_upload_limit(400 * 1024)
    check("подготовка: сид раздаёт", str(sh.status().state) == "seeding",
          f"порт {seed.listen_port()}")

    engine = te.TorrentEngine(data_dir=os.path.join(BASE, "данные"),
                              seed_after_download=False,
                              extra_settings=dict(local))
    engine.start()
    service = ts.StreamService(engine)
    mpv = proc = None
    try:
        tid = engine.add_magnet(
            f"magnet:?xt=urn:btih:{ih}&x.pe=127.0.0.1:{seed.listen_port()}",
            os.path.join(BASE, "Загрузки"))
        end = time.monotonic() + 60
        while time.monotonic() < end:
            item = engine.get(tid)
            if item and item.has_metadata:
                break
            time.sleep(0.1)
        check("метаданные получены", bool(item and item.has_metadata), "")

        # Субтитры снимаем галочкой — так их выбирает обычный
        # пользователь, которому в дереве нужны только видео. Просмотр
        # обязан включить их сам.
        video = ts.choose_video_file(item.files)
        engine.set_files(tid, [4 if f.index == video.index else 0
                               for f in item.files])
        time.sleep(1.0)

        url = service.watch(tid, video.index)
        subs = service.subtitles
        names = sorted(name for name, _ in subs)
        check("подобраны оба .srt, посторонний файл не взят",
              names == ["Фильм.eng.srt", "Фильм.rus.srt"], str(names))

        sub_urls = [u for _, u in subs]
        args = player.subtitle_args(PROTO_MPV, sub_urls)
        check("для mpv собраны ключи на обе дорожки",
              len(args) == 2 and all(a.startswith("--sub-file=")
                                     for a in args), str(args))

        pipe_name = f"vd-subs-{os.getpid()}"
        log_path = os.path.join(BASE, "mpv.log")
        cmd = [PROTO_MPV, url, f"--input-ipc-server={pipe_name}",
               "--no-config", "--no-terminal", "--vo=null", "--ao=null",
               f"--log-file={log_path}", "--msg-level=all=v"] + args
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess,
                                                      "CREATE_NO_WINDOW", 0))
        mpv = Mpv(pipe_name)
        check("mpv отозвался по IPC", mpv.connect(timeout=45), "")

        tracks = None
        end = time.monotonic() + 90
        while time.monotonic() < end:
            tracks = mpv.get("track-list")
            if tracks and any(t.get("type") == "sub" for t in tracks):
                break
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        subs_in_player = [t for t in (tracks or []) if t.get("type") == "sub"]
        check("mpv загрузил субтитры ПО НАШЕЙ ССЫЛКЕ",
              len(subs_in_player) == 2,
              f"дорожек субтитров {len(subs_in_player)}: "
              + ", ".join(str(t.get("external-filename") or t.get("title"))
                          for t in subs_in_player))
        check("субтитры подключены как внешние (наш http-адрес)",
              all(str(t.get("external-filename", "")).startswith("http://")
                  for t in subs_in_player),
              str([t.get("external-filename") for t in subs_in_player]))
        check("выбранная дорожка субтитров активна",
              any(t.get("selected") for t in subs_in_player),
              str([t.get("selected") for t in subs_in_player]))

        errors = []
        if os.path.isfile(log_path):
            with open(log_path, encoding="utf-8", errors="replace") as f:
                errors = [ln.strip() for ln in f
                          if "][e]" in ln or "][fatal]" in ln]
        check("в логе mpv нет ошибок", not errors,
              "; ".join(errors[:2])[:200])

        service.stop()
        check("stop() закрыл и видео, и субтитры",
              service.subtitles == [] and not engine.streams(), "")
    finally:
        if mpv is not None:
            mpv.close()
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        service.shutdown(timeout=2.0)
        engine.shutdown(timeout=5.0)
        seed.remove_torrent(sh)
        del seed
        shutil.rmtree(BASE, ignore_errors=True)

    print(f"\nИТОГ: PASS {len(PASS)}, FAIL {len(FAIL)}")
    for name in FAIL:
        print("  FAIL:", name)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
