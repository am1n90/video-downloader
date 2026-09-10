# -*- coding: utf-8 -*-
"""Проверка YouTube в УСТАНОВЛЕННОЙ сборке 1.0.1 (только чтение).

Импортирует yt_dlp прямо из %LOCALAPPDATA%\\Programs\\VideoDownloader\\_internal —
ровно тот код, что вшит в exe. Показывает:
  1) версию вшитого yt-dlp и наличие EJS/Deno
  2) возникает ли предупреждение «No supported JavaScript runtime»
     (как в сыром yt-dlp, так и с опциями fetch_info нашего приложения)
  3) какие качества возвращает анализ 4K-ролика
"""
import os, sys, io, contextlib

APP = os.path.join(os.environ["LOCALAPPDATA"],
                   "Programs", "VideoDownloader", "_internal")
sys.path.insert(0, APP)

import yt_dlp
print("=" * 60)
print("1) ВШИТЫЙ yt-dlp:", yt_dlp.version.__version__,
      "| VARIANT:", yt_dlp.version.VARIANT)

# EJS: пакет yt-dlp-ejs (с бандлом Deno) vs встроенный фолбэк-интерпретатор
try:
    import yt_dlp_js  # пакет yt-dlp-ejs предоставляет модуль?
    print("   пакет yt-dlp-ejs: ПРИСУТСТВУЕТ")
except ImportError:
    print("   пакет yt-dlp-ejs: ОТСУТСТВУЕТ (только встроенный jsc/_builtin/ejs.py)")
print("=" * 60)

URL_4K = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"  # Big Buck Bunny 60fps 4K

# --- 2) сырой вызов БЕЗ подавления предупреждений (как консольный yt-dlp) ---
print("2) Сырой extract_info (предупреждения ВКЛЮЧЕНЫ):")
import warnings as _w

class WarnCollector:
    def __init__(self):
        self.msgs = []
    def __call__(self, msg):
        self.msgs.append(str(msg))

opts_raw = {"quiet": True, "no_warnings": False}
with yt_dlp.YoutubeDL(opts_raw) as ydl:
    # перехват реальных warning'ов: yt-dlp пишет их через to_stderr
    captured = []
    ydl.to_stderr = lambda msg, *a, **k: captured.append(str(msg))
    info = ydl.extract_info(URL_4K, download=False)
    ydl.to_stderr = lambda msg, *a, **k: None
print("   предупреждения yt-dlp:", captured if captured else "(нет)")

# --- 3) вызов с опциями НАШЕГО fetch_info (quiet+no_warnings, как в GUI) ---
print("3) extract_info с опциями приложения (no_warnings=True):")
opts_app = {"quiet": True, "no_warnings": True,
            "extract_flat": "in_playlist", "noplaylist": True}
with yt_dlp.YoutubeDL(opts_app) as ydl:
    captured2 = []
    ydl.to_stderr = lambda msg, *a, **k: captured2.append(str(msg))
    info2 = ydl.extract_info(URL_4K, download=False)
    ydl.to_stderr = lambda msg, *a, **k: None
print("   предупреждения yt-dlp:", captured2 if captured2 else "(нет — подавлены no_warnings)")

# --- качества на 4K-ролике ---
for tag, inf in (("сырой", info), ("как в GUI", info2)):
    fmts = inf.get("formats") or []
    heights = sorted({f.get("height") for f in fmts if f.get("height")},
                     reverse=True)
    print(f"4) {tag}: высоты = {heights}")
    has_2160 = 2160 in heights
    print(f"   4K (2160) {'ДОСТУПЕН' if has_2160 else 'ОТСУТСТВУЕТ'}; "
          f"формат-строк всего: {len(fmts)}")

# Что построит наш GUI (fetch_info отдаёт heights в video_qualities)
heights = sorted({f.get("height") for f in (info2.get("formats") or [])
                  if f.get("height")}, reverse=True)
print("5) GUI покажет качества:", heights)
