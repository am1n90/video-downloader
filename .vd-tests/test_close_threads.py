# -*- coding: utf-8 -*-
"""Офлайн-тест: сетевые фоновые потоки не держат закрытие окна и не
остаются работать после него (16.09.2026).

Воспроизведено на НАСТОЯЩЕМ окне до исправления: закрытие во время анализа
ссылки на молчащий сервер — падение при выходе 4 из 4 (0xC0000409), во
время медленной проверки обновлений — 2 из 4. Offscreen сам процесс
обычно не падает, поэтому проверяется инвариант: после window.close()
сетевой QThread не работает и closeEvent не ждёт сеть.
Отдельно: без закрытия результаты по-прежнему доходят (проверка и
скачивание обновления, миниатюра, анализ). Сеть — только 127.0.0.1.
"""
import hashlib
import http.server
import json
import os
import sys
import tempfile
import threading
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
import gui

config.save = lambda settings: None   # не трогаем settings.json

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

PASS, FAIL = [], []


def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details, flush=True)


app = QApplication(sys.argv)

# ---------- локальный сервер: /silent/... молчит, остальное отвечает ----------
_img = QImage(32, 18, QImage.Format_RGB32)
_img.fill(QColor(200, 30, 30))
_buf = QByteArray()
_dev = QBuffer(_buf)
_dev.open(QIODevice.WriteOnly)
_img.save(_dev, "PNG")
PNG = bytes(_buf)
FILE = os.urandom(300 * 1024)
MANIFEST = json.dumps({"version": "0.0.1", "url": "http://127.0.0.1/x.exe",
                       "sha256": "0" * 64}).encode()
PAGES = {
    "/manifest.json": (MANIFEST, "application/json"),
    "/thumb.png": (PNG, "image/png"),
    "/file.bin": (FILE, "application/octet-stream"),
    "/page": (b"<html><body>no video here</body></html>", "text/html"),
}
STOP = threading.Event()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/silent"):
            while not STOP.is_set():
                time.sleep(0.2)
            return
        body, ctype = PAGES.get(self.path, (None, None))
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_port}"
BASE = config.load()


def pump(ms):
    """Обработать события ms миллисекунд, отпуская GIL.

    Не QTest.qWait: он не отпускает GIL, и фоновый Python-поток (импорты
    экстракторов yt-dlp при анализе) почти не получает времени — анализ
    страницы не отвечал за 60 с и на коде ДО исправления, а в app.exec()
    и при sleep отвечает за 1.5 с (проверено 16.09.2026). В приложении
    главный поток в app.exec(), так что это свойство только тестов.
    """
    end = time.monotonic() + ms / 1000
    while True:
        app.processEvents()
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(0.02, left))


def wait_until(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        pump(50)
    return pred()


def new_window(**overrides):
    settings = dict(BASE)
    settings["history"] = []
    settings["check_updates"] = False
    settings.update(overrides)
    w = gui.MainWindow(settings)
    w.show()
    pump(200)
    return w


def close_timed(w, worker):
    t = time.monotonic()
    w.close()
    return time.monotonic() - t, worker.isRunning()


def check_close(prefix, w, worker):
    check(f"{prefix}: поток висит на молчащем сервере до закрытия",
          worker is not None and worker.isRunning())
    dt, running = close_timed(w, worker)
    check(f"{prefix}: закрытие окна не ждёт сеть (< 1 с)", dt < 1.0, f"{dt:.2f} с")
    check(f"{prefix}: после закрытия поток не работает", not running)


# ---- 1: проверка обновлений (таймер MainWindow, 2.5 с) ----
w = new_window(check_updates=True, update_manifest_url=URL + "/silent/latest.json")
wait_until(lambda: w.settings_page._update_worker is not None
           and w.settings_page._update_worker.isRunning(), 6)
check_close("1 проверка обновлений", w, w.settings_page._update_worker)

# ---- 2: анализ ссылки (yt-dlp) ----
w = new_window()
page = w.download_page
page.url_edit.setText(URL + "/silent/video.mp4")
page._analyze()
pump(1000)   # yt-dlp дошёл до сетевого запроса
check_close("2 анализ ссылки", w, page._analyze_worker)

# ---- 3: миниатюра на странице «Загрузка» ----
w = new_window()
page = w.download_page
page._load_thumb(URL + "/silent/thumb.png")
pump(300)
check_close("3 миниатюра", w, page._thumb_worker)

# ---- 4: миниатюра Библиотеки ----
w = new_window()
lib = w.library_page
lw = gui.LibraryThumbWorker(URL + "/silent/lib.png", (100, 56), lib)
lib._track_thumb(lw)
lw.start()
pump(300)
check_close("4 миниатюра Библиотеки", w, lw)

# ---- 5: скачивание обновления ----
w = new_window()
sp = w.settings_page
dest_silent = os.path.join(tempfile.gettempdir(), "vd-close-threads-silent.bin")
sp._update_worker = gui.UpdateWorker(
    "download", manifest={"url": URL + "/silent/file.bin", "sha256": "0" * 64},
    dest=dest_silent, parent=sp)
sp._update_worker.start()
pump(300)
check_close("5 скачивание обновления", w, sp._update_worker)

# ---- 6: без закрытия результаты доходят ----
got = {}
wk = gui.UpdateWorker("check", manifest_url=URL + "/manifest.json")
wk.manifestReady.connect(lambda m: got.__setitem__("manifest", m))
wk.manifestError.connect(lambda e: got.__setitem__("manifest_error", e))
wk.start()
ok = wait_until(lambda: "manifest" in got or "manifest_error" in got, 10)
check("6a проверка обновлений: результат доходит (старая версия -> None)",
      ok and "manifest" in got and got["manifest"] is None, str(got))
wait_until(wk.isFinished, 3)

tw = gui.ThumbWorker(URL + "/thumb.png", (168, 96))
tw.loaded.connect(lambda image: got.__setitem__("thumb", image))
tw.start()
ok = wait_until(lambda: "thumb" in got, 10)
check("6b миниатюра доходит", ok and not got["thumb"].isNull())
wait_until(tw.isFinished, 3)

dest_ok = os.path.join(tempfile.gettempdir(), "vd-close-threads-ok.bin")
progress = []
dw = gui.UpdateWorker(
    "download", manifest={"url": URL + "/file.bin",
                          "sha256": hashlib.sha256(FILE).hexdigest()},
    dest=dest_ok)
dw.downloadProgress.connect(lambda p, d, t: progress.append(p))
dw.downloadDone.connect(lambda path: got.__setitem__("done", path))
dw.downloadFailed.connect(lambda e: got.__setitem__("dl_error", e))
dw.start()
ok = wait_until(lambda: "done" in got or "dl_error" in got, 15)
check("6c скачивание обновления: готово, sha256 сошёлся",
      ok and got.get("done") == dest_ok, str(got.get("dl_error", "")))
check("6c прогресс доходит до 100%", bool(progress) and progress[-1] >= 99.9,
      f"{len(progress)} событий, последнее {progress[-1] if progress else None}")
wait_until(dw.isFinished, 3)

aw = gui.AnalyzeWorker(URL + "/page", False)
aw.done.connect(lambda info: got.__setitem__("an_done", info))
aw.failed.connect(lambda e: got.__setitem__("an_fail", e))
aw.start()
ok = wait_until(lambda: "an_done" in got or "an_fail" in got, 60)
check("6d анализ: ответ доходит (страница без видео -> ошибка)",
      ok, str(got.get("an_fail", got.get("an_done", "")))[:120])
wait_until(aw.isFinished, 3)

# ---- 7: cancel() без окна — поток выходит сразу и без сигнала ----
signals = []
cw = gui.ThumbWorker(URL + "/silent/cancel.png", (10, 10))
cw.loaded.connect(lambda image: signals.append(image))
cw.start()
pump(300)
t = time.monotonic()
cw.cancel()
finished = cw.wait(1000)
dt = time.monotonic() - t
pump(300)
check("7 cancel(): поток завершается < 0.5 с и без сигнала",
      finished and dt < 0.5 and not signals, f"{dt:.2f} с, сигналов {len(signals)}")

STOP.set()
pump(500)
# ни один QThread теста не должен работать к выходу процесса
for leftover in (wk, tw, dw, aw, cw):
    leftover.cancel()
    leftover.wait(5000)
check("к выходу потоки теста завершены",
      not any(x.isRunning() for x in (wk, tw, dw, aw, cw)))
srv.shutdown()
for path in (dest_silent, dest_ok):
    try:
        os.remove(path)
    except OSError:
        pass

print()
print(f"ИТОГ: {len(PASS)} PASS, {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
