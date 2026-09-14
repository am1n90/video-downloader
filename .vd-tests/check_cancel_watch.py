# -*- coding: utf-8 -*-
"""Live: отмена фрагмента через сторож (1.0.5 правка владельца п.5).

Реальный код: DownloadManager.add(time_range=...) -> _run_item ->
_fragment_watch. Ждём DOWNLOADING («Скачивание фрагмента…»), ждём
появления файлов, затем request_cancel(): задача должна завершиться
ERROR «Отменено» быстрой смертью (не дожидаясь конца ffmpeg).
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import downloader  # noqa: E402
from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor  # noqa: E402

TEMP = os.environ.get("TEMP", os.path.expanduser("~\\AppData\\Local\\Temp"))
WORKDIR = os.path.join(TEMP, "vd-frag-live")
FFMPEG_DIR = os.path.join(ROOT, "dist", "VideoDownloader")
YT_URL = ("https://www.youtube.com/watch?v=aqz-KE-bpKQ")  # Big Buck Bunny

os.makedirs(WORKDIR, exist_ok=True)

# чистим целевые файлы (иначе skip-скачивание за 3с)
for name in os.listdir(WORKDIR):
    if "[clip 0s-240s]" in name:
        os.remove(os.path.join(WORKDIR, name))

orig = downloader.DownloadManager._build_options


def build(self, it):
    opts = orig(self, it)
    opts["ffmpeg_location"] = FFMPEG_DIR
    FFmpegPostProcessor._ffmpeg_location.set(FFMPEG_DIR)
    return opts


downloader.DownloadManager._build_options = build

mgr = downloader.DownloadManager(max_concurrent=1)
mgr.start()
t0 = time.monotonic()
item = mgr.add(YT_URL, mode="video", quality="best", output_dir=WORKDIR,
               playlist=False, time_range=(0, 240), precise_cut=True)

print("ждём DOWNLOADING (статус «Скачивание фрагмента…»)...", flush=True)
deadline = time.monotonic() + 120
saw_downloading = False
while time.monotonic() < deadline:
    if item.status == downloader.STATUS_DOWNLOADING:
        saw_downloading = True
        print("  DOWNLOADING виден через %.1fs (статус честный)"
              % (time.monotonic() - t0), flush=True)
        break
    if item.status in (downloader.STATUS_COMPLETED,
                       downloader.STATUS_ERROR):
        break
    time.sleep(0.2)

print("status=%s" % item.status, flush=True)
if not saw_downloading:
    print("FAIL: статус не дошёл до DOWNLOADING", flush=True)
    print("  error=%s" % (item.error or "")[:200], flush=True)
    sys.exit(1)

# ждём появления/роста файлов (ffmpeg начал писать) — до 60с
print("ждём начала записи файлов...", flush=True)
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    files = [f for f in os.listdir(WORKDIR) if "[clip 0s-240s]" in f]
    if files:
        print("  файлы: %s" % files[:2], flush=True)
        break
    time.sleep(0.5)
if not files:
    print("файлы не появились — отменяем сразу после начала статуса",
          flush=True)

t_cancel = time.monotonic()
print("request_cancel()...", flush=True)
mgr.cancel(item.id)
deadline = time.monotonic() + 15
while time.monotonic() < deadline and item.status not in (
        downloader.STATUS_ERROR, downloader.STATUS_COMPLETED):
    time.sleep(0.2)
elapsed_cancel = time.monotonic() - t_cancel

if (item.status == downloader.STATUS_ERROR
        and item.error == "Отменено" and elapsed_cancel < 15):
    print("PASS: отмена сработала за %.1fs (ERROR «Отменено», сторож)"
          % elapsed_cancel, flush=True)
    print("abandoned=%s" % item._abandoned, flush=True)
    sys.exit(0)
print("FAIL: status=%s error=%s elapsed=%.1fs"
      % (item.status, (item.error or "")[:200], elapsed_cancel), flush=True)
sys.exit(1)
