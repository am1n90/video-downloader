# -*- coding: utf-8 -*-
"""Офлайн-тест: предупреждения yt-dlp идут через logger в yt-dlp.log.

Правка 1.0.2: no_warnings убран, в опции YoutubeDL передаётся logger
(единый config.get_logger); предупреждения пишутся в yt-dlp.log, который
в dev-режиме лежит в корне проекта и покрыт *.log в .gitignore.
Сеть не используется: YoutubeDL подменяется фейком (только перехват опций).
"""
import io
import logging
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
import downloader

PASS, FAIL = [], []
def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details)

# --- изоляция: логи и настройки в temp, не в корень проекта ---
tmp = tempfile.mkdtemp(prefix="vd_ytdlp_logger_")
old_cache = config._log_dir_cache
config._log_dir_cache = tmp
config._loggers.pop("vdl.ytdlp", None)
_ytdlp_logger = logging.getLogger("vdl.ytdlp")
for _h in _ytdlp_logger.handlers[:]:
    _ytdlp_logger.removeHandler(_h)

try:
    # 1) подмена YoutubeDL: перехватываем опции, сеть не трогаем
    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured.update(opts)
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {"title": "t", "uploader": "u", "duration": 1,
                    "formats": [{"height": 720, "acodec": "none"}]}

    real_ytdl = downloader.YoutubeDL
    downloader.YoutubeDL = FakeYDL
    try:
        downloader.fetch_info("https://example.com/x")
        check("fetch_info: logger передан", "logger" in captured)
        check("fetch_info: no_warnings убран", "no_warnings" not in captured)
        check("fetch_info: logger - logging.Logger",
              isinstance(captured.get("logger"), logging.Logger),
              type(captured.get("logger")).__name__)

        # 2) опции скачивания (_build_options)
        manager = downloader.DownloadManager()
        item = downloader.DownloadItem("https://example.com/x", output_dir=tmp)
        opts = manager._build_options(item)
        check("_build_options: logger передан", "logger" in opts)
        check("_build_options: no_warnings убран", "no_warnings" not in opts)
    finally:
        downloader.YoutubeDL = real_ytdl

    # 3) предупреждение через logger попадает в yt-dlp.log (в tmp)
    lg = config.get_logger("vdl.ytdlp", "yt-dlp.log")
    lg.warning("TESTWARNING-12345")
    for h in lg.handlers:
        h.flush()
    log_path = os.path.join(tmp, "yt-dlp.log")
    body = io.open(log_path, encoding="utf-8").read() if os.path.isfile(log_path) else ""
    check("warning -> yt-dlp.log записан", "TESTWARNING-12345" in body, log_path)

    # 4) уровень и propagate: debug-шум отсечён, дубли в root-логгер не идут
    check("logger: WARNING, propagate=False",
          lg.level == logging.WARNING and lg.propagate is False,
          f"level={lg.level} propagate={lg.propagate}")

    # 5) yt-dlp.log в dev-режиме покрыт .gitignore
    r = subprocess.run(["git", "check-ignore", "yt-dlp.log"],
                       cwd=ROOT, capture_output=True)
    check("git check-ignore yt-dlp.log", r.returncode == 0, f"exit={r.returncode}")
finally:
    config._log_dir_cache = old_cache
    config._loggers.pop("vdl.ytdlp", None)
    for _h in _ytdlp_logger.handlers[:]:
        _ytdlp_logger.removeHandler(_h)

print()
print(f"ИТОГ: {len(PASS)} PASS, {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
