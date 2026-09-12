# -*- coding: utf-8 -*-
"""Тесты повтора переходящих ошибок (1.0.4).

Все сценарии офлайновые: YoutubeDL подменяется фейком (как в
test_ytdlp_logger.py), сеть не используется. Проверяем обвязку
вокруг extract_info:
  AC4 - первая попытка падает сетевой ошибкой, вторая проходит ->
        задача завершается успешно (анализ fetch_info и скачивание
        _run_item);
  AC5 - постоянные ошибки (удалено/приватно/нужен вход) - ровно
        одна попытка, ERROR сразу, текст понятный;
  AC6 - отмена и пауза во время ожидания между попытками прерывают
        задачу немедленно (не ждут конца паузы);
  правка владельца - финальная ошибка содержит число попыток
        («после N попыток»), каждая неудачная попытка пишется в
        yt-dlp.log (логгер vdl.ytdlp).

Для AC6 пауза между попытками в тесте удлиняется подменой
RETRY_PAUSE_SECONDS (в приложении 2 секунды; тесту нужно окно).
"""
import io
import logging
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
import downloader

PASS, FAIL = [], []
def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details)

# --- изоляция: логи в temp, не в корень проекта (как test_ytdlp_logger) ---
tmp = tempfile.mkdtemp(prefix="vd_retry_")
old_cache = config._log_dir_cache
config._log_dir_cache = tmp
config._loggers.pop("vdl.ytdlp", None)
_ytdlp_logger = logging.getLogger("vdl.ytdlp")
for _h in _ytdlp_logger.handlers[:]:
    _ytdlp_logger.removeHandler(_h)

# пауза между попытками: 0.4 с — окно для отмены/паузы в AC6
old_pause = downloader.RETRY_PAUSE_SECONDS
downloader.RETRY_PAUSE_SECONDS = 0.4

real_ytdl = downloader.YoutubeDL

def flush_log():
    for h in logging.getLogger("vdl.ytdlp").handlers:
        h.flush()
    return io.open(os.path.join(tmp, "yt-dlp.log"),
                   encoding="utf-8").read()

try:
    # ============ 1. _is_retryable: классификация ============
    def retryable(msg):
        return downloader._is_retryable(Exception(msg))

    r_cases = [
        ("ERROR: [TikTok] 123: Unexpected response from webpage request", True),
        ("ERROR: [TikTok] 123: Unable to extract universal data for rehydration", True),
        ("ERROR: [generic] ssl: CERTIFICATE_VERIFY_FAILED", True),
        ("ERROR: unable to download webpage: <urlopen error [Errno 10060] timed out>", True),
        ("ERROR: HTTP Error 503: Service Unavailable", True),
        ("ERROR: HTTP Error 500: Internal Server Error", True),
        ("ERROR: [youtube] dQw4w9WgXcQ: Video unavailable", False),
        ("ERROR: [youtube] dQw4w9WgXcQ: Private video", False),
        ("ERROR: [instagram] 123: Main login required to view this", False),
        ("ERROR: Unsupported URL: https://example.com/watch", False),
        ("ERROR: [youtube] dQw4w9WgXcQ: This video has been removed by the uploader", False),
        ("ERROR: [youtube] xyz: HTTP Error 404: Not Found", False),
        ("ERROR: Requested content is not available in your country", False),
    ]
    for msg, expected in r_cases:
        got = retryable(msg)
        check(f"_is_retryable: {msg[:58]!r}", got == expected,
              f"got {got}, expected {expected}")

    # слово с границей не ловится внутри других слов («message» содержит age)
    check("_is_retryable: age внутри «message» — не маркер (границы слов)",
          retryable("ERROR: [generic] Bad response message from server") is True)

    # DownloadCancelled — никогда не повторяется
    check("_is_retryable: DownloadCancelled -> False",
          downloader._is_retryable(downloader.DownloadCancelled()) is False)

    # ============ 2. AC4: fetch_info, 2-я попытка проходит ============
    attempts = {"n": 0}

    class FakeYDLFlaky:
        """Падает сетевой ошибкой 1 раз, дальше успешен."""
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def extract_info(self, url, download=False):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise Exception(
                    "ERROR: [TikTok] 123: Unexpected response from webpage request")
            return {"title": "t", "uploader": "u", "duration": 10,
                    "formats": [{"height": 720, "acodec": "mp4a"}]}

    downloader.YoutubeDL = FakeYDLFlaky
    info = downloader.fetch_info("https://vt.tiktok.com/x/")
    check("AC4 fetch_info: сбой 1-й попытки, успех на 2-й -> результат",
          info is not None and info["title"] == "t",
          f"attempts={attempts['n']}")
    check("AC4 fetch_info: попыток было ровно 2",
          attempts["n"] == 2, f"attempts={attempts['n']}")

    body = flush_log()
    check("попытки пишутся в yt-dlp.log (attempt 1/3 failed)",
          "attempt 1/3 failed" in body, os.path.join(tmp, "yt-dlp.log"))

    # ============ 3. AC4: fetch_info, все попытки падают ============
    attempts2 = {"n": 0}

    class FakeYDLAlwaysFail:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def extract_info(self, url, download=False):
            attempts2["n"] += 1
            raise Exception("ERROR: unable to download webpage: timed out")

    downloader.YoutubeDL = FakeYDLAlwaysFail
    try:
        downloader.fetch_info("https://example.com/x")
        check("AC4 fetch_info: все попытки падают -> исключение", False,
              "не упал")
    except Exception as exc:
        check("AC4 fetch_info: все попытки падают -> исключение",
              attempts2["n"] == downloader.RETRY_ATTEMPTS,
              f"attempts={attempts2['n']}")
        check("финальная ошибка содержит счётчик попыток",
              f"после {downloader.RETRY_ATTEMPTS} попыток" in str(exc),
              str(exc)[:120])

    # ============ 4. AC5: fetch_info постоянная ошибка ============
    attempts3 = {"n": 0}

    class FakeYDLPermanent:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def extract_info(self, url, download=False):
            attempts3["n"] += 1
            raise Exception("ERROR: [youtube] xyz: Video unavailable")

    downloader.YoutubeDL = FakeYDLPermanent
    try:
        downloader.fetch_info("https://youtube.com/watch?v=xyz")
        check("AC5: постоянная ошибка -> исключение", False, "не упал")
    except Exception as exc:
        check("AC5: постоянная ошибка -> ровно 1 попытка",
              attempts3["n"] == 1, f"attempts={attempts3['n']}")
        check("AC5: текст понятный (нет счётчика попыток)",
              "Video unavailable" in str(exc)
              and "попыток" not in str(exc), str(exc)[:100])

    # ============ 5. AC4: _run_item, сбой -> повтор -> успех ============
    run_attempts = {"n": 0}
    OUT_DIR = os.path.join(tmp, "out")
    os.makedirs(OUT_DIR, exist_ok=True)

    class FakeYDLRun:
        """Скачивание: 1-я попытка сетевой сбой, 2-я ок."""
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def prepare_filename(self, info):
            return os.path.join(OUT_DIR, "video.mp4")
        def extract_info(self, url, download=False):
            run_attempts["n"] += 1
            if run_attempts["n"] == 1:
                raise Exception(
                    "ERROR: [TikTok] 123: Unable to extract universal data "
                    "for rehydration")
            return {"title": "t", "uploader": "u", "duration": 10,
                    "thumbnail": None, "formats": []}

    downloader.YoutubeDL = FakeYDLRun
    mgr = downloader.DownloadManager(max_concurrent=1)
    item = downloader.DownloadItem("https://vt.tiktok.com/x/", output_dir=OUT_DIR)
    mgr._run_item(item)
    check("AC4 _run_item: сбой 1-й попытки, успех на 2-й -> COMPLETED",
          item.status == downloader.STATUS_COMPLETED,
          f"status={item.status}, error={item.error}")
    check("AC4 _run_item: попыток ровно 2", run_attempts["n"] == 2,
          f"attempts={run_attempts['n']}")

    # ============ 6. AC6: отмена во время паузы между попытками =======
    run_attempts2 = {"n": 0}

    class FakeYDLAlwaysFailRun:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def prepare_filename(self, info):
            return os.path.join(OUT_DIR, "video2.mp4")
        def extract_info(self, url, download=False):
            run_attempts2["n"] += 1
            raise Exception("ERROR: unable to download webpage: timed out")

    downloader.YoutubeDL = FakeYDLAlwaysFailRun
    item2 = downloader.DownloadItem("https://example.com/y/", output_dir=OUT_DIR)

    cancel_timer = threading.Timer(
        0.15,   # внутри ожидания между попытками (пауза 0.4 с)
        item2.request_cancel)
    t0 = time.monotonic()
    cancel_timer.start()
    mgr._run_item(item2)
    elapsed = time.monotonic() - t0
    cancel_timer.join()
    check("AC6: отмена в паузе -> ERROR «Отменено»",
          item2.status == downloader.STATUS_ERROR
          and item2.error == "Отменено",
          f"status={item2.status}, error={item2.error}")
    check("AC6: отмена прервала ожидание немедленно (<0.35с)",
          elapsed < 0.35,
          f"elapsed={elapsed:.2f}s, attempts={run_attempts2['n']}")
    check("AC6: после отмены новых попыток нет (<=2)",
          run_attempts2["n"] <= 2, f"attempts={run_attempts2['n']}")

    # ============ 7. AC6: пауза во время ожидания =====================
    run_attempts3 = {"n": 0}

    class FakeYDLAlwaysFailRun3:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def prepare_filename(self, info):
            return os.path.join(OUT_DIR, "video3.mp4")
        def extract_info(self, url, download=False):
            run_attempts3["n"] += 1
            raise Exception("ERROR: unable to download webpage: timed out")

    downloader.YoutubeDL = FakeYDLAlwaysFailRun3
    item3 = downloader.DownloadItem("https://example.com/z/", output_dir=OUT_DIR)

    pause_timer = threading.Timer(0.15, item3.request_pause)
    pause_timer.start()
    mgr._run_item(item3)
    pause_timer.join()
    check("AC6: пауза в ожидании -> PAUSED (задача не упала)",
          item3.status == downloader.STATUS_PAUSED,
          f"status={item3.status}, error={item3.error}")
    check("AC6: после паузы новых попыток нет (<=2)",
          run_attempts3["n"] <= 2, f"attempts={run_attempts3['n']}")

    # ============ 8. AC5: _run_item постоянная ошибка =================
    run_attempts4 = {"n": 0}

    class FakeYDLPrivate:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def prepare_filename(self, info):
            return os.path.join(OUT_DIR, "video4.mp4")
        def extract_info(self, url, download=False):
            run_attempts4["n"] += 1
            raise Exception("ERROR: [youtube] xyz: Private video. "
                            "Sign in if you've been granted access to this video")

    downloader.YoutubeDL = FakeYDLPrivate
    item4 = downloader.DownloadItem("https://youtube.com/watch?v=pv",
                                    output_dir=OUT_DIR)
    mgr._run_item(item4)
    check("AC5 _run_item: постоянная ошибка -> 1 попытка, ERROR",
          item4.status == downloader.STATUS_ERROR
          and run_attempts4["n"] == 1,
          f"status={item4.status}, attempts={run_attempts4['n']}")
    check("AC5 _run_item: текст понятный (Private video, без счётчика)",
          "Private video" in (item4.error or "")
          and "попыток" not in (item4.error or ""),
          (item4.error or "")[:100])

    # ============ 9. финальная ошибка _run_item: счётчик ==============
    run_attempts5 = {"n": 0}

    class FakeYDLRunTimeout:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def prepare_filename(self, info):
            return os.path.join(OUT_DIR, "video5.mp4")
        def extract_info(self, url, download=False):
            run_attempts5["n"] += 1
            raise Exception("ERROR: unable to download webpage: timed out")

    downloader.YoutubeDL = FakeYDLRunTimeout
    item5 = downloader.DownloadItem("https://example.com/t/", output_dir=OUT_DIR)
    mgr._run_item(item5)
    check("финальная ошибка _run_item: счётчик + понятный текст",
          item5.status == downloader.STATUS_ERROR
          and f"после {downloader.RETRY_ATTEMPTS} попыток" in (item5.error or "")
          and "timed out" in (item5.error or ""),
          (item5.error or "")[:120])
    check("финальная ошибка: одна строка (для однострочного GUI)",
          (item5.error or "").count("\n") == 0,
          repr((item5.error or "")[:80]))

    body = flush_log()
    check("yt-dlp.log: все неудачные попытки записаны (1/3 и 2/3)",
          body.count("attempt 1/3 failed") >= 2
          and body.count("attempt 2/3 failed") >= 2,
          "счётчики attempt k/3 в логе")

    # ============ 10. правка 1: extract_info ок, следующий шаг упал ==
    # Файл скачан (extract_info вернулся) -> повторное скачивание
    # запрещено, даже если prepare_filename/_notify упали.
    run_attempts6 = {"n": 0}

    class FakeYDLFinishFails:
        """extract_info успешен, prepare_filename падает."""
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def prepare_filename(self, info):
            raise ValueError("boom in prepare_filename")
        def extract_info(self, url, download=False):
            run_attempts6["n"] += 1
            return {"title": "t", "uploader": "u", "duration": 10,
                    "thumbnail": None, "formats": []}

    downloader.YoutubeDL = FakeYDLFinishFails
    item6 = downloader.DownloadItem("https://example.com/f/", output_dir=OUT_DIR)
    mgr._run_item(item6)
    check("правка 1: падение ПОСЛЕ extract_info -> без повторов (1 попытка)",
          run_attempts6["n"] == 1
          and item6.status == downloader.STATUS_ERROR
          and "boom in prepare_filename" in (item6.error or ""),
          f"attempts={run_attempts6['n']}, status={item6.status}, "
          f"error={(item6.error or '')[:60]}")
    check("правка 1: текст ошибки без счётчика попыток (это не сеть)",
          "попыток" not in (item6.error or ""),
          (item6.error or "")[:80])

    # ============ 11. правка 3: ошибки записи на диск — 1 попытка ===
    d_cases = [
        ("ERROR: unable to write data: [Errno 28] No space left on device", False),
        ("OSError: [WinError 112] There is not enough space on the disk", False),
        ("OSError: [Errno 13] Permission denied: 'C:\\x\\video.mp4'", False),
        ("OSError: [WinError 32] The process cannot access the file "
         "because it is being used by another process", False),
        ("ERROR: [Errno 28] No space left on device", False),
    ]
    for msg, expected in d_cases:
        got = retryable(msg)
        check(f"правка 3 _is_retryable: {msg[:52]!r}", got == expected,
              f"got {got}, expected {expected}")

    run_attempts7 = {"n": 0}

    class FakeYDLNoSpace:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def prepare_filename(self, info):
            return os.path.join(OUT_DIR, "video7.mp4")
        def extract_info(self, url, download=False):
            run_attempts7["n"] += 1
            raise OSError(
                "[Errno 28] No space left on device: 'video7.mp4.part'")

    downloader.YoutubeDL = FakeYDLNoSpace
    item7 = downloader.DownloadItem("https://example.com/d/", output_dir=OUT_DIR)
    mgr._run_item(item7)
    check("правка 3: диск переполнен -> 1 попытка, ERROR сразу",
          run_attempts7["n"] == 1
          and item7.status == downloader.STATUS_ERROR
          and "No space left" in (item7.error or "")
          and "попыток" not in (item7.error or ""),
          f"attempts={run_attempts7['n']}, status={item7.status}")

finally:
    downloader.YoutubeDL = real_ytdl
    downloader.RETRY_PAUSE_SECONDS = old_pause
    config._log_dir_cache = old_cache
    config._loggers.pop("vdl.ytdlp", None)
    for _h in _ytdlp_logger.handlers[:]:
        _ytdlp_logger.removeHandler(_h)

print()
print(f"ИТОГ: {len(PASS)} PASS, {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
