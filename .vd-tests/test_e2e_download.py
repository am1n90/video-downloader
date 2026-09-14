# -*- coding: utf-8 -*-
"""Живой end-to-end: реальная загрузка через DownloadManager с паузой и
докачкой .part (проверка целостности после рефакторинга downloader.py).

Запуск (каждая фаза укладывается в лимит раннера):
  python test_e2e_download.py one     # короткое видео одной фазой (smoke)
  python test_e2e_download.py phase1 # старт+пауза в середине, .part на диске
  python test_e2e_download.py phase2 # resume (докачка .part) -> COMPLETED
"""
import os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import downloader

PHASE = sys.argv[1] if len(sys.argv) > 1 else "one"
URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"

# dev-окружение этой машины: ffmpeg в PATH отсутствует, а приложение
# подставляет ffmpeg_location только при sys.frozen. Инъекция ТОЛЬКО
# в тесте (как в check_fragment.py): обёртка _build_options + ContextVar
# для FFmpegFD — иначе merge bestvideo+bestaudio падает «ffmpeg is not
# installed» (см. downloader.py 1.0.5, комментарий про ContextVar).
_FFMPEG_DIR = os.path.join(ROOT, "dist", "VideoDownloader")
if os.path.isfile(os.path.join(_FFMPEG_DIR, "ffmpeg.exe")):
    from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor
    _orig_build = downloader.DownloadManager._build_options

    def _build_with_ffmpeg(self, it):
        opts = _orig_build(self, it)
        opts["ffmpeg_location"] = _FFMPEG_DIR
        FFmpegPostProcessor._ffmpeg_location.set(_FFMPEG_DIR)
        return opts

    downloader.DownloadManager._build_options = _build_with_ffmpeg

outdir = os.path.join(os.environ.get("TEMP", "/tmp"), "vd-e2e-yt")
os.makedirs(outdir, exist_ok=True)

events = []
def on_change(item):
    events.append((time.time(), item.status, round(item.progress, 1)))

mgr = downloader.DownloadManager(max_concurrent=1, on_change=on_change)
mgr.start()

print("Фаза:", PHASE, "| URL:", URL)
print("Папка:", outdir)
item = mgr.add(URL, mode="video", quality="best", output_dir=outdir)

def wait_status(statuses, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if item.status in statuses:
            return True
        time.sleep(0.1)
    return False

def final_check():
    ok = item.files and all(os.path.isfile(f) for f in item.files)
    size = os.path.getsize(item.files[0]) if ok else 0
    print("Файлы:", item.files, "| размер:", size)
    return ok and size > 10000

if PHASE == "one":
    # --- smoke: анализ -> скачивание -> пауза -> resume -> готово ---
    if not wait_status({downloader.STATUS_DOWNLOADING, downloader.STATUS_COMPLETED}, 60):
        print("E2E FAIL: загрузка не началась:", item.status, item.error)
        sys.exit(1)
    if item.status == downloader.STATUS_DOWNLOADING:
        mgr.pause(item.id)
        wait_status({downloader.STATUS_PAUSED, downloader.STATUS_COMPLETED}, 20)
        print("После паузы:", item.status)
        if item.status == downloader.STATUS_PAUSED:
            mgr.resume(item.id)
    if not wait_status({downloader.STATUS_COMPLETED, downloader.STATUS_ERROR}, 240):
        print("E2E FAIL: зависло:", item.status)
        sys.exit(1)
    if item.status == downloader.STATUS_ERROR:
        print("E2E FAIL:", (item.error or "")[:300])
        sys.exit(1)
    ok = final_check()
    print("E2E " + ("OK" if ok else "FAIL"))
    sys.exit(0 if ok else 1)

if PHASE == "phase1":
    # Стартуем, ждём >=5% и вешаем паузу; остаёмся в PAUSED, .part на диске.
    if not wait_status({downloader.STATUS_DOWNLOADING, downloader.STATUS_COMPLETED}, 60):
        print("PHASE1 FAIL: загрузка не началась:", item.status, item.error)
        sys.exit(1)
    if item.status == downloader.STATUS_COMPLETED:
        print("Файл скачан мгновенно — двухфазный тест неприменим, OK")
        sys.exit(0)
    deadline = time.time() + 20
    while time.time() < deadline and item.progress < 5.0:
        time.sleep(0.05)
    mgr.pause(item.id)
    if not wait_status({downloader.STATUS_PAUSED}, 20):
        print("PHASE1 FAIL: пауза не сработала:", item.status)
        sys.exit(1)
    parts = [f for f in os.listdir(outdir) if f.endswith(".part")]
    print("PHASE1 OK: пауза на", round(item.progress, 1), "% | .part-файлы:", parts)
    sys.exit(0 if parts else 1)

if PHASE == "phase2":
    # Resume: докачка .part -> COMPLETED.
    mgr.resume(item.id)
    if not wait_status({downloader.STATUS_COMPLETED, downloader.STATUS_ERROR}, 240):
        print("PHASE2 FAIL: зависло:", item.status)
        sys.exit(1)
    if item.status == downloader.STATUS_ERROR:
        print("PHASE2 FAIL:", (item.error or "")[:300])
        sys.exit(1)
    ok = final_check()
    parts = [f for f in os.listdir(outdir) if f.endswith(".part")]
    print("PHASE2", "OK" if ok and not parts else "FAIL",
          "| .part остаток:", parts)
    sys.exit(0 if ok and not parts else 1)

print("Неизвестная фаза:", PHASE)
sys.exit(2)
