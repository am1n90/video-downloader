"""Этап 0: тестовые видео для локального сида (без чужого контента).

Создаёт в %TEMP%\\vd-torrent-proto\\seed\\Тест раздача:
  - «Тестовый фильм 1080p.mkv»: 600 с, 1920x1080 h264 16 Мбит/с (testsrc2 +
    шум, чтобы битрейт был реальным), 2 AAC (rus 440 Гц / eng 880 Гц),
    2 SRT (rus/eng) — ~1.2 ГБ
  - «test moov-at-end.mp4»: 300 с, h264 8 Мбит/с + AAC, moov в КОНЦЕ файла
    (без +faststart) — худший случай для стриминга, ~292 МБ
  - «Тестовый фильм 1080p.ru.srt» — внешние субтитры

ffmpeg берётся из %TEMP%\\vd-torrent-proto\\bin (копируется туда из
dist\\VideoDownloader). НЕ запускать ffmpeg из build-ffmpeg-cache: build.bat
пересоздаёт extracted и падает на занятом exe (инцидент 15.09.2026).
Кодирование ~11 мин на домашнем ноутбуке.
"""
import os
import shutil
import subprocess
import sys
import time

from common import PROTO_TMP

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MEDIA = os.path.join(HERE, "media")
BIN = os.path.join(PROTO_TMP, "bin")
OUT = os.path.join(PROTO_TMP, "seed", "Тест раздача")


def ensure_tools():
    os.makedirs(BIN, exist_ok=True)
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        target = os.path.join(BIN, name)
        if os.path.isfile(target):
            continue
        source = os.path.join(ROOT, "dist", "VideoDownloader", name)
        if not os.path.isfile(source):
            sys.exit(f"нет {source} — сначала build.bat или build_ffmpeg.ps1")
        shutil.copy2(source, target)
    return os.path.join(BIN, "ffmpeg.exe")


def run(cmd, label):
    t = time.monotonic()
    subprocess.run(cmd, check=True)
    print(f"{label}: {time.monotonic() - t:.0f} с", flush=True)


def main():
    ffmpeg = ensure_tools()
    os.makedirs(OUT, exist_ok=True)
    noise = ",noise=alls=20:allf=t+u"
    run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i",
         "testsrc2=size=1920x1080:rate=30:duration=600" + noise,
         "-f", "lavfi", "-i", "sine=frequency=440:duration=600",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=600",
         "-i", os.path.join(MEDIA, "sub_ru.srt"),
         "-i", os.path.join(MEDIA, "sub_en.srt"),
         "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3", "-map", "4",
         "-c:v", "libx264", "-preset", "veryfast", "-b:v", "16M",
         "-maxrate", "16M", "-bufsize", "32M", "-g", "60",
         "-c:a", "aac", "-b:a", "128k", "-c:s", "srt",
         "-metadata:s:a:0", "language=rus", "-metadata:s:a:1", "language=eng",
         "-metadata:s:s:0", "language=rus", "-metadata:s:s:1", "language=eng",
         os.path.join(OUT, "Тестовый фильм 1080p.mkv")], "MKV")
    run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i",
         "testsrc2=size=1920x1080:rate=30:duration=300" + noise,
         "-f", "lavfi", "-i", "sine=frequency=660:duration=300",
         "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "veryfast", "-b:v", "8M",
         "-maxrate", "8M", "-bufsize", "16M", "-g", "60",
         "-c:a", "aac", "-b:a", "128k",
         os.path.join(OUT, "test moov-at-end.mp4")], "MP4")
    shutil.copy2(os.path.join(MEDIA, "sub_ru.srt"),
                 os.path.join(OUT, "Тестовый фильм 1080p.ru.srt"))
    print("готово:", OUT)


if __name__ == "__main__":
    main()
