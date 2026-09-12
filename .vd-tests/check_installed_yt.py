# -*- coding: utf-8 -*-
"""Проверка YouTube в УСТАНОВЛЕННОЙ сборке (только чтение).

Импортирует yt_dlp прямо из %LOCALAPPDATA%\\Programs\\VideoDownloader\\_internal —
ровно тот код, что вшит в exe. Показывает:
  1) версию вшитого yt-dlp, наличие пакета yt-dlp-ejs и deno.exe рядом с exe
     (импорт пакета — по ПРАВИЛЬНОМУ имени yt_dlp_ejs; в версиях до 1.0.2
     проверка делалась по неверному имени yt_dlp_js и её вывод был
     недостоверен — см. AGENTS.md)
  2) возникает ли предупреждение "No supported JavaScript runtime"
     (сырой вызов; с опциями fetch_info нашего приложения предупреждения
     уходят в logger/yt-dlp.log, а не в GUI)
  3) какие качества возвращает анализ 4K-ролика
  4) что записалось в yt-dlp.log установленной копии
"""
import os, sys, io, logging

APP = os.path.join(os.environ["LOCALAPPDATA"],
                   "Programs", "VideoDownloader", "_internal")
sys.path.insert(0, APP)

import yt_dlp
print("=" * 60)
print("1) ВШИТЫЙ yt-dlp:", yt_dlp.version.__version__,
      "| VARIANT:", yt_dlp.version.VARIANT)

# EJS: пакет yt-dlp-ejs предоставляет модуль yt_dlp_ejs (имя верное!)
try:
    import yt_dlp_ejs  # noqa: F401
    print("   пакет yt-dlp-ejs: ПРИСУТСТВУЕТ, версия:",
          getattr(yt_dlp_ejs, "version", "?"))
except ImportError as e:
    print("   пакет yt-dlp-ejs: ОТСУТСТВУЕТ:", e)

# deno.exe рядом с VideoDownloader.exe (yt-dlp ищет его там при frozen)
exe_dir = os.path.dirname(APP)   # _internal -> папка exe (один уровень вверх)
deno = os.path.join(exe_dir, "deno.exe")
if os.path.isfile(deno):
    import subprocess
    out = subprocess.run([deno, "--version"], capture_output=True, text=True)
    line = (out.stdout or out.stderr).strip().splitlines()
    print("   deno.exe рядом с exe:", line[0] if line else "(пусто)")
else:
    print("   deno.exe рядом с exe: ОТСУТСТВУЕТ ->", exe_dir)
print("=" * 60)

URL_4K = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"  # Big Buck Bunny 60fps 4K

# --- 2) сырой вызов БЕЗ подавления предупреждений (как консольный yt-dlp) ---
print("2) Сырой extract_info (предупреждения ВКЛЮЧЕНЫ):")
opts_raw = {"quiet": True, "no_warnings": False}
with yt_dlp.YoutubeDL(opts_raw) as ydl:
    # перехват реальных warning'ов: yt-dlp пишет их через to_stderr
    captured = []
    ydl.to_stderr = lambda msg, *a, **k: captured.append(str(msg))
    info = ydl.extract_info(URL_4K, download=False)
    ydl.to_stderr = lambda msg, *a, **k: None
print("   предупреждения yt-dlp:", captured if captured else "(нет)")
print("   'No supported JavaScript runtime' есть:",
      any("JavaScript runtime" in c for c in captured))

# --- 3) вызов с опциями НАШЕГО fetch_info (1.0.2: logger вместо no_warnings) ---
print("3) extract_info с опциями приложения (logger):")
lg = logging.getLogger("check.inst")
lg.addHandler(logging.NullHandler())
lg.propagate = False
opts_app = {"quiet": True, "logger": lg,
            "extract_flat": "in_playlist", "noplaylist": True}
with yt_dlp.YoutubeDL(opts_app) as ydl:
    captured2 = []
    ydl.to_stderr = lambda msg, *a, **k: captured2.append(str(msg))
    info2 = ydl.extract_info(URL_4K, download=False)
    ydl.to_stderr = lambda msg, *a, **k: None
print("   предупреждения yt-dlp:", captured2 if captured2 else "(нет — идут в logger)")

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

# --- 6) лог установленной копии: предупреждение о JS-рантайме ушло в файл ---
log_path = os.path.join(os.environ["LOCALAPPDATA"],
                        "VideoDownloader", "yt-dlp.log")
print("6) yt-dlp.log установленной копии:", log_path)
if os.path.isfile(log_path):
    body = io.open(log_path, encoding="utf-8", errors="replace").read()
    lines = [l for l in body.splitlines() if l.strip()]
    print("   записей:", len(lines))
    print("   'No supported JavaScript runtime' в логе:",
          "JavaScript runtime" in body)
else:
    print("   лог ещё не создан (создастся при первом анализе/загрузке)")
