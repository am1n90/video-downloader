# -*- coding: utf-8 -*-
"""Проверка TikTok в УСТАНОВЛЕННОЙ сборке 1.0.4 (анализ + скачивание + звук).

Импортирует yt_dlp и curl_cffi прямо из %LOCALAPPDATA%\\Programs\\
VideoDownloader\\_internal — ровно те пакеты, что вшиты в exe
(включая бинарники curl_cffi: _wrapper.pyd + libcurl-impersonate).
Ссылки читаются из sources.local.txt (личное, в .gitignore; в чат
не выводятся). Файлы — в %TEMP%\\vd-inst-test, настройки и история
установленной копии не трогаются.

  build-venv\\Scripts\\python.exe .vd-tests\\check_installed_tiktok.py
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.environ["LOCALAPPDATA"],
                   "Programs", "VideoDownloader")
INTERNAL = os.path.join(APP, "_internal")
# Порядок важен: yt_dlp и curl_cffi берутся из УСТАНОВЛЕННОЙ копии,
# а downloader (наш код 1.0.4 с повтором) — из корня проекта.
sys.path.insert(0, INTERNAL)
sys.path.insert(1, ROOT)

import downloader  # noqa: E402  (корень проекта; внутри — yt_dlp из APP)
import yt_dlp  # noqa: E402  (установленная копия)

SOURCES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "sources.local.txt")
WORK = os.path.join(os.environ.get("TEMP", os.path.expanduser("~\\AppData\\Local\\Temp")),
                    "vd-inst-test")
FFPROBE = os.path.join(APP, "ffprobe.exe")

PASS, FAIL = [], []
def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details)


def load_tiktok_urls():
    urls = []
    with open(SOURCES, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "tiktok" in line.lower():
                urls.append(line)
    return urls


def ffprobe_audio(path):
    """Есть ли аудио-поток (звук на месте)."""
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries",
         "stream=codec_type", "-of", "csv=p=0", path],
        capture_output=True, text=True)
    return "audio" in (out.stdout or "")


def wait_done(item, timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if item.status in (downloader.STATUS_COMPLETED,
                           downloader.STATUS_ERROR,
                           downloader.STATUS_PAUSED):
            return
        time.sleep(0.2)


def main():
    os.makedirs(WORK, exist_ok=True)
    urls = load_tiktok_urls()
    print("installed copy:", APP)
    print("yt_dlp:", yt_dlp.version.__version__, "из", os.path.dirname(yt_dlp.__file__))
    print("tiktok urls:", len(urls))
    if len(urls) < 2:
        print("FAIL: need 2 tiktok urls in sources.local.txt")
        sys.exit(1)

    # 0) curl_cffi вшит и импортируется из установленной копии
    try:
        import curl_cffi  # noqa: F401
        from importlib.metadata import version as _v
        check("curl_cffi импортируется из установленной копии", True,
              "curl_cffi " + _v("curl_cffi"))
    except Exception as e:
        check("curl_cffi импортируется из установленной копии", False, str(e))
        sys.exit(1)

    # 1) анализ обеих ссылок — fetch_info приложения (с повтором 1.0.4)
    for i, url in enumerate(urls, 1):
        try:
            info = downloader.fetch_info(url)
            check(f"AC3 анализ ссылки {i} (fetch_info с повтором)",
                  bool(info.get("title")),
                  "duration=%s heights=%s" % (
                      info.get("duration"), info.get("video_qualities")))
        except Exception as e:
            check(f"AC3 анализ ссылки {i} (fetch_info с повтором)",
                  False, str(e)[:160])
            sys.exit(1)

    # 2) скачивание видео (ссылка 1) через DownloadManager — путь
    #    приложения целиком: повтор, .part, ffmpeg из папки exe
    mgr = downloader.DownloadManager(max_concurrent=1)
    mgr.start()

    def run_download(tag, url, mode):
        item = mgr.add(url, mode=mode, quality="best", output_dir=WORK)
        item_id = item.id
        wait_done(item)
        if item.status == downloader.STATUS_PAUSED:   # паузы тут не ждём
            item.request_resume()
            wait_done(item)
        ok = (item.status == downloader.STATUS_COMPLETED
              and item.files and os.path.isfile(item.files[0]))
        check(f"AC3 {tag}", ok,
              (("%.1f MB %s" % (os.path.getsize(item.files[0]) / 1048576.0,
                                os.path.basename(item.files[0])))
               if ok else f"status={item.status} err={(item.error or '')[:120]}"))
        return item.files[0] if ok else None

    # ffmpeg_location в dev-режиме менеджер не ставит — подменим опции
    # так же, как это делает установленная копия при sys.frozen
    orig_build = downloader.DownloadManager._build_options

    def build_frozen(self, it):
        opts = orig_build(self, it)
        opts["ffmpeg_location"] = APP
        return opts

    downloader.DownloadManager._build_options = build_frozen
    try:
        video = run_download("скачивание видео (ссылка 1)", urls[0], "video")
        if video:
            check("AC3 звук в видео (ffprobe audio-поток)",
                  ffprobe_audio(video), os.path.basename(video))
        run_download("скачивание MP3 (ссылка 2, звук)", urls[1], "audio")
    finally:
        downloader.DownloadManager._build_options = orig_build

    print()
    print("ИТОГ: %d PASS, %d FAIL" % (len(PASS), len(FAIL)))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()

