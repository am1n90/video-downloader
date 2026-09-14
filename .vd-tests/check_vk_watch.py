# -*- coding: utf-8 -*-
"""Live: VK + фрагмент -> сторож закрывает задачу понятной ошибкой (1.0.5).

Тот же код, что в приложении (_fragment_watch); FRAGMENT_STALL_TIMEOUT
ускорен до 180с (тестовый параметр — в продукте 600с). VK HLS-секция
не растёт -> ERROR «VK пока не поддерживает скачивание фрагмента».
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
SOURCES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "sources.local.txt")

os.makedirs(WORKDIR, exist_ok=True)
downloader.FRAGMENT_STALL_TIMEOUT = 180.0   # тест: 3 мин вместо 10

vk_url = None
with open(SOURCES, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and (
                "vk.com" in line or "vkvideo" in line):
            vk_url = line
            break
if not vk_url:
    sys.exit("no VK url in sources.local.txt")

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
item = mgr.add(vk_url, mode="video", quality="best", output_dir=WORKDIR,
               playlist=False, time_range=(0, 30), precise_cut=False)
print("VK fragment запущен; ждём verdict сторожа (до ~4 мин)...",
      flush=True)
deadline = time.monotonic() + 420
while time.monotonic() < deadline and item.status not in (
        downloader.STATUS_ERROR, downloader.STATUS_COMPLETED):
    time.sleep(1.0)
elapsed = time.monotonic() - t0
print("status=%s elapsed=%.0fs" % (item.status, elapsed), flush=True)
print("error=%s" % (item.error or "")[:200], flush=True)
ok = (item.status == downloader.STATUS_ERROR
      and item.error == "VK пока не поддерживает скачивание фрагмента"
      and elapsed < 400)
print(("PASS" if ok else "FAIL")
      + ": VK закрыт сторожем понятной ошибкой", flush=True)
sys.exit(0 if ok else 1)
