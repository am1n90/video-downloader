# -*- coding: utf-8 -*-
"""Локальный HTTP-сервер манифеста для регресса автообновления 1.0.0 -> 1.0.1.

Отдаёт /latest.json (версия 1.0.1, url -> локальный установщик) и сам
установщик Output/VideoDownloader-Setup-1.0.1.exe. Пишет журнал запросов.
Запуск: python .vd-tests/update_server.py --port 8765
"""
import http.server, json, os, sys, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP = os.path.join(ROOT, "Output", "VideoDownloader-Setup-1.0.1.exe")

MANIFEST = {
    "version": "1.0.1",
    "url": None,  # подставится в __main__ (localhost + порт)
    "sha256": None,
    "notes": "Локальный тест автообновления: фиксы P0/P1/P2",
}

LOG = os.path.join(ROOT, ".vd-tests", "update_server.log")

def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    print(msg, flush=True)


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, length_only=False):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not length_only:
            self.wfile.write(body)

    def do_GET(self):
        log(f"GET {self.path} from {self.client_address[0]}")
        if self.path.startswith("/latest.json"):
            body = json.dumps(MANIFEST).encode()
            self._send(body, "application/json")
        elif self.path.startswith("/setup.exe"):
            with open(SETUP, "rb") as f:
                data = f.read()
            # отдаём медленно, чтобы прогресс был виден и отменяем
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            step = 512 * 1024
            for i in range(0, len(data), step):
                try:
                    self.wfile.write(data[i:i + step])
                    self.wfile.flush()
                except Exception:
                    log("КЛИЕНТ ОТРЫЛ СОЕДИНЕНИЕ (отмена?)")
                    return
                time.sleep(0.02)
            log("SETUP ОТДАН ПОЛНОСТЬЮ")
        else:
            self.send_error(404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8765
    import hashlib
    sha = hashlib.sha256(open(SETUP, "rb").read()).hexdigest()
    MANIFEST["url"] = f"http://127.0.0.1:{port}/setup.exe"
    MANIFEST["sha256"] = sha
    open(LOG, "w").close()   # сброс журнала
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    log(f"SERVER UP on 127.0.0.1:{port} | setup={os.path.getsize(SETUP)} байт")
    server.serve_forever()
