# -*- coding: utf-8 -*-
"""Live-проверка фрагмента видео 1.0.5 (AC3/AC4/AC5/AC6 + пауза).

Usage (ASCII only):
  python check_fragment.py <site> <start> <end> <precise> [mode]
  python check_fragment.py pause <start> <end>

  site: yt | tiktok | inst | vk      (yt = Big Buck Bunny, публичный)
  start/end: секунды                 precise: 0|1   mode: video|audio
  pause: YouTube-фрагмент с паузой/докачкой в процессе

Uses the REAL app code path: DownloadManager.add(time_range=...,
precise_cut=...) -> _build_options -> download_ranges. Files go to
%TEMP%\\vd-frag-live only. ffmpeg/ffprobe from dist\\VideoDownloader.
TikTok/Instagram/VK URLs come from sources.local.txt (personal, never
printed). Checks with ffprobe: duration ~expected, audio stream, mp3.
"""
import os
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import downloader  # noqa: E402

TEMP = os.environ.get("TEMP", os.path.expanduser("~\\AppData\\Local\\Temp"))
WORKDIR = os.path.join(TEMP, "vd-frag-live")
SOURCES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "sources.local.txt")
FFMPEG_DIR = os.path.join(ROOT, "dist", "VideoDownloader")
YT_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"  # Big Buck Bunny

os.makedirs(WORKDIR, exist_ok=True)

passed = []
failed = []


def check(name, cond, detail=""):
    mark = "PASS" if cond else "FAIL"
    print("%s | %s | %s" % (mark, name, detail), flush=True)
    (passed if cond else failed).append(name)


def load_sources():
    urls = {}
    with open(SOURCES, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                host = line.split("/")[2].lower()
                if "tiktok" in host:
                    urls["tiktok"] = line
                elif "instagram" in host:
                    urls["inst"] = line
                elif "vk" in host or "vkvideo" in host:
                    urls["vk"] = line
    return urls


def inject_ffmpeg_location():
    """dev-режим: yt-dlp ищет ffmpeg в PATH; подменим опции, как это
    делает установленная копия при sys.frozen (ffmpeg_location + ContextVar
    для FFmpegFD — см. downloader.py 1.0.5)."""
    from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor
    orig = downloader.DownloadManager._build_options

    def build(self, it):
        opts = orig(self, it)
        opts["ffmpeg_location"] = FFMPEG_DIR
        FFmpegPostProcessor._ffmpeg_location.set(FFMPEG_DIR)
        return opts

    downloader.DownloadManager._build_options = build
    return orig


def wait_status(item, wanted, timeout, manager=None):
    """Ждать статус (или список) с таймаутом; PAUSED не ждём >loop."""
    if isinstance(wanted, str):
        wanted = [wanted]
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if item.status in wanted:
            return True
        time.sleep(0.3)
    return False


def ffprobe(path, entries="format=duration"):
    exe = os.path.join(FFMPEG_DIR, "ffprobe.exe")
    if not os.path.isfile(exe):
        exe = "ffprobe"   # fallback: PATH
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-show_entries", entries,
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=60)
        return (out.stdout or "").strip()
    except Exception as e:
        return "ERR: %s" % e


def has_audio(path):
    streams = ffprobe(path, "stream=codec_type")
    return "audio" in streams


def clear_target_files(start, end):
    """Удалить файлы этого диапазона из WORKDIR перед прогоном — иначе
    yt-dlp видит готовый файл и «скачивает» за 3с (skip), искажая
    wall-time и проверки pause/precise."""
    suffix = downloader.fragment_suffix(start, end)
    for name in os.listdir(WORKDIR):
        if suffix in name:
            try:
                os.remove(os.path.join(WORKDIR, name))
            except OSError:
                pass


def run_download(url, start, end, precise, mode, timeout=900):
    """Скачивание через реальный DownloadManager; возвращает
    (item, wall_seconds)."""
    clear_target_files(start, end)
    manager = downloader.DownloadManager(max_concurrent=1)
    manager.start()
    t0 = time.monotonic()
    item = manager.add(url, mode=mode, quality="best",
                       output_dir=WORKDIR, playlist=False,
                       time_range=(start, end), precise_cut=bool(precise))
    ok = wait_status(item, ("completed", "error"), timeout)
    wall = time.monotonic() - t0
    return (item if ok else None), wall, manager


def report(tag, item, start, end, wall, expect_dur, mode):
    print("%s: status=%s wall=%.1fs" % (tag, item.status, wall), flush=True)
    if item.status != "completed":
        check("%s: завершено" % tag, False,
              "status=%s err=%s" % (item.status, (item.error or "")[:200]))
        return None
    files = [f for f in (item.files or []) if os.path.isfile(f)]
    check("%s: файл на диске" % tag, bool(files),
          str(item.files)[:300])
    if not files:
        return None
    path = files[0]
    print("  file: %s (%.1f MB)" % (
        os.path.basename(path), os.path.getsize(path) / 1048576.0))
    dur_raw = ffprobe(path)
    try:
        dur = float(dur_raw)
    except ValueError:
        check("%s: ffprobe длительность" % tag, False, dur_raw)
        return path
    delta = abs(dur - expect_dur)
    print("  duration: %.2fs (ожидалось ~%ds)" % (dur, expect_dur))
    if mode == "video":
        check("%s: длительность ~%ds (+/-2с)" % (tag, expect_dur),
              abs(dur - expect_dur) <= 2.0, "%.2fs" % dur)
        check("%s: звук есть" % tag, has_audio(path))
    else:
        check("%s: mp3 длительность ~%ds (+/-2с)" % (tag, expect_dur),
              delta <= 2.0, "%.2fs" % dur)
    check("%s: имя содержит [clip" % tag,
          "[clip" in os.path.basename(path), os.path.basename(path))
    return path


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    inject_ffmpeg_location()

    if args[0] == "pause":
        # AC: пауза/докачка при фрагменте (задание: проверить и доложить,
        # НЕ чинить). Фрагмент YouTube качается быстро (~4с fast), окно
        # DOWNLOADING короткое: опрос 0.05с по прогрессу; если окно
        # упущено — пауза из QUEUED до старта потока.
        start, end = float(args[1]), float(args[2])
        print("== PAUSE TEST: YouTube %g-%gs ==" % (start, end), flush=True)
        clear_target_files(start, end)
        manager = downloader.DownloadManager(max_concurrent=1)
        manager.start()
        item = manager.add(YT_URL, mode="video", quality="best",
                           output_dir=WORKDIR, playlist=False,
                           time_range=(start, end), precise_cut=False)
        # ждём появления прогресса; как только есть — пауза
        paused_mid = False
        t0 = time.monotonic()
        while time.monotonic() - t0 < 180:
            if item.status == "completed":
                break
            if item.status == "downloading" and item.progress > 0:
                manager.pause(item.id)
                paused_mid = True
                break
            time.sleep(0.05)
        if not paused_mid and item.status != "completed":
            # окно DOWNLOADING упущено/ещё не наступило: пауза из очереди
            manager.pause(item.id)
            paused_mid = True
        print("paused_mid=%s status_at_pause=%s progress=%.1f%%"
              % (paused_mid, item.status, item.progress), flush=True)
        ok_paused = wait_status(item, ("paused", "completed", "error"), 60)
        check("pause: задача дошла до PAUSED", item.status == "paused",
              "status=%s" % item.status)
        parts = [f for f in os.listdir(WORKDIR) if f.endswith(".part")]
        print("  .part файлов после паузы: %d" % len(parts))
        time.sleep(2)
        manager.resume(item.id)
        ok_done = wait_status(item, ("completed", "error"), 600)
        check("pause: завершилось после resume", ok_done
              and item.status == "completed",
              "status=%s err=%s" % (item.status, (item.error or "")[:200]))
        if item.status == "completed" and item.files:
            p = [f for f in item.files if os.path.isfile(f)]
            if p:
                dur = ffprobe(p[0])
                print("  file: %s duration=%s" % (os.path.basename(p[0]),
                                                 dur))
                try:
                    dur_s = float(dur)
                except ValueError:
                    dur_s = None
                # fast-обрезка: сдвиг к ключевому кадру; главное — файл
                # цел и докачался (проверка паузы, не точности реза)
                expect = end - start
                check("pause: длительность в разумных границах",
                      dur_s is not None and dur_s >= expect * 0.8, dur)
        sys.exit(1 if failed else 0)

    site, s_str, e_str, p_str = args[0], args[1], args[2], args[3]
    mode = args[4] if len(args) > 4 else "video"
    start, end, precise = float(s_str), float(e_str), int(p_str)
    urls = load_sources()
    if site == "yt":
        url = YT_URL
    elif site in urls:
        url = urls[site]
    else:
        sys.exit("no URL for %s in sources.local.txt" % site)

    tag = "%s %g-%gs %s%s" % (site, start, end, mode,
                              " precise" if precise else "")
    print("== %s ==" % tag, flush=True)
    timeout = 300 if site == "vk" else 900   # VK HLS может висеть (12.09)
    item, wall, manager = run_download(url, start, end, precise, mode,
                                       timeout=timeout)
    if item is None:
        check("%s: завершено" % tag, False, "таймаут 900с")
        sys.exit(1)
    report(tag, item, start, end, wall, int(end - start), mode)
    print()
    print("ИТОГ: %d PASS, %d FAIL" % (len(passed), len(failed)))
    if failed:
        print("ПРОВАЛЕНЫ:", ", ".join(failed))
        sys.exit(1)


main()
