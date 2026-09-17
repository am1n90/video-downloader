"""Локальный сид + HTTP-трекер на 127.0.0.1 для проверки УСТАНОВЛЕННОЙ копии.

Живые проверки 2.1-2.3 шли в процессе приложения и подключали сида
напрямую (`connect_peer`). Установленный exe так не подключишь — снаружи в
его движок не залезть, поэтому пира ему сообщает трекер: его адрес зашит в
.torrent и в magnet-ссылку, трекер на любой анонс отвечает адресом сида.
DHT/LSD у сида выключены — раздача видна только через этот трекер.

Состав раздачи — как у сериала на две серии: два видео в одной папке и
.srt к первому (для диалога «Что смотреть» и субтитров-спутников).

Запуск: python seed_local_tracker.py <папка> [лимит КБ/с]
Пишет <папка>\\seed.json (пути, magnet, порты) и работает, пока не
появится файл <папка>\\stop.
"""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import libtorrent as lt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.path.join(ROOT, "dist", "VideoDownloader", "ffmpeg.exe")

BASE = os.path.abspath(sys.argv[1])
LIMIT_KB = int(sys.argv[2]) if len(sys.argv) > 2 else 1500
SRC_ROOT = os.path.join(BASE, "источник")
CONTENT = os.path.join(SRC_ROOT, "Сериал про котиков")
VIDEO1 = os.path.join(CONTENT, "Котики s01e01.mkv")
VIDEO2 = os.path.join(CONTENT, "Котики s01e02.mkv")
SUB1 = os.path.join(CONTENT, "Котики s01e01.ru.srt")
TORRENT = os.path.join(BASE, "Сериал про котиков.torrent")
STOP = os.path.join(BASE, "stop")
INFO = os.path.join(BASE, "seed.json")


def make_video(path, seconds, bitrate, freq):
    if os.path.isfile(path):
        return
    ff = os.path.join(BASE, "ffmpeg.exe")
    if not os.path.isfile(ff):
        import shutil
        shutil.copy2(FFMPEG, ff)
    subprocess.run(
        [ff, "-v", "error", "-y",
         "-f", "lavfi",
         "-i", f"testsrc2=size=1280x720:rate=25:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
         "-c:v", "libx264", "-preset", "veryfast", "-b:v", bitrate,
         "-c:a", "aac", "-shortest", path],
        check=True, capture_output=True)


def compact_peer(port):
    return bytes([127, 0, 0, 1]) + port.to_bytes(2, "big")


class Tracker(BaseHTTPRequestHandler):
    seed_port = 0
    announces = 0

    def do_GET(self):
        from urllib.parse import parse_qs, urlsplit
        Tracker.announces += 1
        port = parse_qs(urlsplit(self.path).query).get("port", ["0"])[0]
        # Сид анонсируется на тот же трекер: отдай ему его же адрес — и он
        # подключится сам к себе и рвёт соединения (поймано диагностикой)
        own = port == str(Tracker.seed_port)
        body = lt.bencode({b"interval": 15,
                           b"peers": b"" if own
                           else compact_peer(Tracker.seed_port)})
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    os.makedirs(CONTENT, exist_ok=True)
    if os.path.exists(STOP):
        os.remove(STOP)
    make_video(VIDEO1, 180, "6M", 440)
    make_video(VIDEO2, 40, "4M", 660)
    with open(SUB1, "wb") as f:
        f.write(b"1\r\n00:00:01,000 --> 00:00:30,000\r\n"
                + "мяу-субтитр".encode("utf-8") + b"\r\n")

    # Порт постоянный: адрес трекера зашит в раздачу, и после перезапуска
    # сида уже добавленная в приложение раздача должна его найти
    try:
        tracker = ThreadingHTTPServer(("127.0.0.1", 7788), Tracker)
    except OSError:
        tracker = ThreadingHTTPServer(("127.0.0.1", 0), Tracker)
    announce = f"http://127.0.0.1:{tracker.server_port}/announce"
    threading.Thread(target=tracker.serve_forever, daemon=True).start()

    fs = lt.file_storage()
    lt.add_files(fs, CONTENT)
    ct = lt.create_torrent(fs, 512 * 1024, lt.create_torrent.v1_only)
    ct.add_tracker(announce)
    lt.set_piece_hashes(ct, SRC_ROOT)
    with open(TORRENT, "wb") as f:
        f.write(lt.bencode(ct.generate()))
    ti = lt.torrent_info(TORRENT)
    ih = ti.info_hashes().v1.to_bytes().hex()

    ses = lt.session({"enable_dht": False, "enable_lsd": False,
                      "enable_upnp": False, "enable_natpmp": False,
                      # uTP на loopback сид рвёт сразу (EOF), а повтор к
                      # тому же пиру libtorrent ждёт 60 с (находка 18)
                      "enable_incoming_utp": False,
                      "enable_outgoing_utp": False,
                      "listen_interfaces": "127.0.0.1:0"})
    atp = lt.add_torrent_params()
    atp.ti = ti
    atp.save_path = SRC_ROOT
    atp.flags = atp.flags | lt.torrent_flags.seed_mode
    h = ses.add_torrent(atp)
    h.set_upload_limit(LIMIT_KB * 1024)
    Tracker.seed_port = ses.listen_port()

    from urllib.parse import quote
    magnet = (f"magnet:?xt=urn:btih:{ih}&dn={quote(ti.name())}"
              f"&tr={quote(announce, safe='')}")
    info = {"torrent": TORRENT, "magnet": magnet, "infohash": ih,
            "announce": announce, "seed_port": Tracker.seed_port,
            "limit_kb": LIMIT_KB,
            "files": [{"path": fs.file_path(i), "size": fs.file_size(i)}
                      for i in range(fs.num_files())]}
    with open(INFO, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print("SEED_READY", json.dumps(info, ensure_ascii=False), flush=True)

    last = 0
    while not os.path.exists(STOP):
        time.sleep(1)
        st = h.status()
        if time.monotonic() - last > 30:
            last = time.monotonic()
            print(f"seed: {st.state} peers={st.num_peers} "
                  f"up={st.upload_rate / 1024:.0f} KB/s "
                  f"total_up={st.total_upload / 1048576:.1f} MB "
                  f"announces={Tracker.announces}", flush=True)
    tracker.shutdown()
    print("SEED_STOPPED", flush=True)


if __name__ == "__main__":
    main()
