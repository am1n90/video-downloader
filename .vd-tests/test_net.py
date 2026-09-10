# -*- coding: utf-8 -*-
"""Живой тест fetch_info (сеть). Отдельный файл: если сеть недоступна, остальной suite не страдает."""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import downloader

url = sys.argv[1] if len(sys.argv) > 1 else "https://www.youtube.com/watch?v=jNQXAC9IVRw"
print("Анализирую:", url)
try:
    info = downloader.fetch_info(url)
except Exception as e:
    print("NET_FAIL:", type(e).__name__, str(e)[:300])
    sys.exit(2)

print("title      :", info["title"])
print("uploader   :", info["uploader"])
print("duration   :", info["duration"])
print("qualities  :", info["video_qualities"])
print("sizes keys  :", sorted(info["quality_sizes"].keys()))
print("audio      :", info["audio_available"])
print("NET_OK")
