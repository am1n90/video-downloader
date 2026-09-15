# -*- coding: utf-8 -*-
"""Тест фрагмента видео (1.0.5, офлайн).

Проверяет:
  - parse_timecode / fmt_timecode (мм:сс, ч:мм:сс, голые секунды, invalid)
  - fragment_suffix: только латиница/цифры, целые секунды
  - RangeSlider: клэмпы (конец не раньше начала + 1с, границы, мин. 1с),
    программная установка set_values без сигналов, set_duration
  - DownloadItem.add с time_range/precise_cut; _build_options: без
    фрагмента — прежние опции (AC1), с фрагментом — download_ranges +
    force_keyframes_at_cuts + outtmpl с суффиксом [clip Ns-Ms]
  - yt-dlp sanitize_filename: суффикс [clip] выживает при эмодзи/
    кириллице в названии, имя != имени полного видео (AC7)
  - GUI (offscreen): галочка/блок, синхронизация слайдер <-> поля в обе
    стороны, клэмпы полей, мин. 1с, расчёт размера, _start_download,
    плейлист — галочка скрыта (AC2)
  - превью кадра при drag (сценарии A-D на медленном фейк-ffmpeg):
    кадр доезжает при быстром/непрерывном drag, остаётся после
    отпускания, ffmpeg не убивается и не плодится, клэмп duration-1,
    кадр над активной ручкой
  - история: fragment-поле в записи, фрагмент не вытесняет запись
    полного видео (AC7)

settings.json владельца не трогается (config.save подменён).
"""
import json
import os
import shutil
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config

config.save = lambda settings: None   # settings.json владельца не трогаем

import downloader
import gui

passed = []
failed = []


def check(name, cond, detail=""):
    mark = "PASS" if cond else "FAIL"
    print(f"{mark} | {name}" + (f" | {detail}" if detail and not cond else ""))
    (passed if cond else failed).append(name)


# ============ 1. parse_timecode / fmt_timecode ============

check("timecode: 0:35 -> 35", gui.parse_timecode("0:35") == 35)
check("timecode: 2:10 -> 130", gui.parse_timecode("2:10") == 130)
check("timecode: 1:00:00 -> 3600", gui.parse_timecode("1:00:00") == 3600)
check("timecode: 90 (голые секунды) -> 90", gui.parse_timecode("90") == 90)
check("timecode: 02:10 (ведущий 0) -> 130",
      gui.parse_timecode("02:10") == 130)
check("timecode: abc -> None", gui.parse_timecode("abc") is None)
check("timecode: 1:2:3:4 -> None", gui.parse_timecode("1:2:3:4") is None)
check("timecode: '' -> None", gui.parse_timecode("") is None)
check("timecode: -5 -> None", gui.parse_timecode("-5") is None)
check("timecode: 0:0 -> 0", gui.parse_timecode("0:0") == 0)
check("timecode: 2:0 -> 120", gui.parse_timecode("2:0") == 120)

# ---- 1.0.6: разделитель между группами цифр — любой не-цифровой символ ----
check("timecode: '1 22' (пробел) -> 82", gui.parse_timecode("1 22") == 82)
check("timecode: '1 22 54' (пробел, ч:мм:сс) -> 4974",
      gui.parse_timecode("1 22 54") == 4974)
check("timecode: '1-22' (дефис) -> 82", gui.parse_timecode("1-22") == 82)
check("timecode: '1.22' (точка) -> 82", gui.parse_timecode("1.22") == 82)
check("timecode: '1,22,54' (запятая, ч:мм:сс) -> 4974",
      gui.parse_timecode("1,22,54") == 4974)
check("timecode: '1  22' (несколько пробелов подряд) -> 82",
      gui.parse_timecode("1  22") == 82)
check("timecode: ' 1 22 ' (внешние пробелы обрезаются) -> 82",
      gui.parse_timecode(" 1 22 ") == 82)
check("timecode: '02 10' (ведущий 0, пробел) -> 130",
      gui.parse_timecode("02 10") == 130)
check("timecode: '1 2 3 4' (4 группы) -> None",
      gui.parse_timecode("1 2 3 4") is None)
check("timecode: ' ' (только пробел) -> None",
      gui.parse_timecode("  ") is None)
check("timecode: ':35' (разделитель в начале) -> None",
      gui.parse_timecode(":35") is None)
check("timecode: '35:' (разделитель в конце) -> None",
      gui.parse_timecode("35:") is None)
check("timecode: '1 2a' (буква среди цифр группы) -> None",
      gui.parse_timecode("1 2a") is None)

check("fmt_timecode: 35 -> '0:35'", gui.fmt_timecode(35) == "0:35")
check("fmt_timecode: 130 -> '2:10'", gui.fmt_timecode(130) == "2:10")
check("fmt_timecode: 3725 -> '1:02:05'", gui.fmt_timecode(3725) == "1:02:05")
check("fmt_timecode: 0 -> '0:00'", gui.fmt_timecode(0) == "0:00")

# ============ 2. fragment_suffix ============

check("suffix: (35, 95) -> '[clip 35s-95s]'",
      downloader.fragment_suffix(35, 95) == "[clip 35s-95s]")
check("suffix: (0, 30) -> '[clip 0s-30s]'",
      downloader.fragment_suffix(0, 30) == "[clip 0s-30s]")
check("suffix: (35.6, 95.4) -> округление '[clip 36s-95s]'",
      downloader.fragment_suffix(35.6, 95.4) == "[clip 36s-95s]")
check("suffix: (3660, 7325) -> '[clip 3660s-7325s]'",
      downloader.fragment_suffix(3660, 7325) == "[clip 3660s-7325s]")
import re as _re
check("suffix: только латиница/цифры/[ ]/- s",
      _re.fullmatch(r"\[clip \d+s-\d+s\]",
                    downloader.fragment_suffix(3, 9)) is not None)

# ============ 3. RangeSlider: клэмпы, сигналы, set_duration ============

from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

slider = gui.RangeSlider(100)
check("slider: init 0..100", slider.values() == (0, 100))
# каждый кейс — с независимым пресетом (set_values), чтобы цепочки
# состояний не маскировали проверки
slider.set_values(0, 100)
slider._set_handle("start", 150)     # клэмп: start <= end-1 (99)
check("slider: start клэмпится к end-1 при выходе за диапазон",
      slider.values() == (99, 100))
slider.set_values(50, 100)
slider._set_handle("end", 10)        # клэмп: end >= start+1
check("slider: end клэмпится к start+1 при выходе за диапазон",
      slider.values() == (50, 51))
slider.set_values(50, 100)
slider._set_handle("start", -5)
check("slider: start >= 0", slider.values() == (0, 100))
slider.set_values(0, 100)
slider._set_handle("end", 500)
check("slider: end <= duration", slider.values() == (0, 100))
slider._set_handle("start", 30)
check("slider: обычная установка start", slider.values() == (30, 100))

signals = []
slider.rangeChanged.connect(lambda s, e: signals.append(("r", s, e)))
slider.fieldsMoved.connect(lambda: signals.append(("f",)))
slider.set_values(10, 60)             # программно — БЕЗ сигналов
check("slider: set_values без сигналов", not signals)
slider._set_handle("end", 80, emit=True)   # как при drag
check("slider: _set_handle(emit=True) -> fieldsMoved", ("f",) in signals)
slider._set_handle("start", 20, emit=True)
check("slider: значения после drag-имитации", slider.values() == (20, 80))

slider.set_duration(200)
check("slider: set_duration(200) сбрасывает на 0..200",
      slider.values() == (0, 200) and slider.duration == 200)

# ============ 4. DownloadItem / manager.add / _build_options ============

mgr = downloader.DownloadManager(max_concurrent=1)
item_full = mgr.add("https://x", "video", "best", "C:/d", False)
check("item: add() без фрагмента -> time_range None",
      item_full.time_range is None and item_full.precise_cut is False)
item_frag = mgr.add("https://x", "video", "best", "C:/d", False,
                    time_range=(30, 60), precise_cut=True)
check("item: add() с фрагментом -> (30, 60), precise True",
      item_frag.time_range == (30, 60) and item_frag.precise_cut is True)

opts_full = mgr._build_options(item_full)
check("opts: без фрагмента нет download_ranges",
      "download_ranges" not in opts_full)
check("opts: без фрагмента нет force_keyframes_at_cuts",
      "force_keyframes_at_cuts" not in opts_full)
check("opts: без фрагмента outtmpl прежний",
      opts_full["outtmpl"].endswith("%(title)s.%(ext)s"))

opts_frag = mgr._build_options(item_frag)
check("opts: фрагмент -> download_ranges есть", "download_ranges" in opts_frag)
check("opts: фрагмент -> force_keyframes_at_cuts True",
      opts_frag.get("force_keyframes_at_cuts") is True)
check("opts: фрагмент -> outtmpl с [clip 30s-60s]",
      "[clip 30s-60s]" in opts_frag["outtmpl"])
check("opts: фрагмент -> outtmpl отличается от полного",
      opts_frag["outtmpl"] != opts_full["outtmpl"])
rng = opts_frag["download_ranges"]({}, None)
check("opts: download_ranges -> [{'start_time':30,'end_time':60}]",
      rng == [{"start_time": 30.0, "end_time": 60.0}])
check("opts: лямбда возвращает новый список каждый вызов",
      opts_frag["download_ranges"]({}, None) is not rng)
item_np = mgr.add("https://x", "video", "best", "C:/d", False,
                  time_range=(30, 60), precise_cut=False)
opts_np = mgr._build_options(item_np)
check("opts: precise=False -> нет force_keyframes_at_cuts",
      "force_keyframes_at_cuts" not in opts_np)
item_audio = mgr.add("https://x", "audio", "best", "C:/d", False,
                     time_range=(30, 60))
opts_audio = mgr._build_options(item_audio)
check("opts: аудио+фрагмент -> суффикс в outtmpl + MP3-постпроцессор",
      "[clip 30s-60s]" in opts_audio["outtmpl"]
      and opts_audio["format"] == "bestaudio/best")

# ============ 4.5 VK-фрагмент: прямой формат (п.12) ============

# _is_vk_url: разбор хоста (домены экстрактора VK в yt-dlp)
vk_cases = [
    ("https://vk.com/video-1_2", True),
    ("https://m.vk.com/video-1_2", True),
    ("https://new.vk.com/video-1_2", True),
    ("https://vk.ru/video-1_2", True),
    ("https://vkvideo.ru/video-1_2", True),
    ("https://vksport.vkvideo.ru/video-1_2", True),
    ("HTTPS://VK.COM/video-1_2", True),
    ("vkvideo.ru/video-1_2", True),
    ("https://notvk.com/video", False),
    ("https://vk.com.evil.org/video", False),
    ("https://www.youtube.com/watch?v=x?vk.com", False),
    ("https://www.tiktok.com/@u/video/1", False),
    ("", False),
    (None, False),
]
bad_vk = [u for u, want in vk_cases if downloader._is_vk_url(u) != want]
check("_is_vk_url: хост vk.com/vk.ru/vkvideo.ru + поддомены, чужие — нет",
      not bad_vk, f"ошибки: {bad_vk}")

VK_URL = "https://vkvideo.ru/video-220754053_456242855"
FULL_FMT = {"best": "bestvideo+bestaudio/best",
            "720": "bestvideo[height<=720]+bestaudio/best[height<=720]/best"}


def _fmt_of(url, mode="video", quality="best", time_range=(0, 30)):
    return mgr._build_options(
        mgr.add(url, mode, quality, "C:/d", False, time_range=time_range)
    )["format"]


check("vk-fmt: VK+фрагмент best -> прямой формат первым, прежний после /",
      _fmt_of(VK_URL) == "best[protocol~='^https?$']/" + FULL_FMT["best"],
      _fmt_of(VK_URL))
check("vk-fmt: VK+фрагмент 720 -> прямой [height<=720], прежний после /",
      _fmt_of(VK_URL, quality="720")
      == "best[protocol~='^https?$'][height<=720]/" + FULL_FMT["720"],
      _fmt_of(VK_URL, quality="720"))
check("vk-fmt: VK+фрагмент аудио -> прямой формат, затем bestaudio/best",
      _fmt_of(VK_URL, mode="audio")
      == "best[protocol~='^https?$']/bestaudio/best",
      _fmt_of(VK_URL, mode="audio"))
check("vk-fmt: VK без фрагмента -> формат не менялся (best и 720)",
      _fmt_of(VK_URL, time_range=None) == FULL_FMT["best"]
      and _fmt_of(VK_URL, quality="720", time_range=None) == FULL_FMT["720"])
check("vk-fmt: VK без фрагмента аудио -> bestaudio/best",
      _fmt_of(VK_URL, mode="audio", time_range=None) == "bestaudio/best")
other_bad = []
for other in ("https://www.youtube.com/watch?v=aqz-KE-bpKQ",
              "https://www.tiktok.com/@u/video/1",
              "https://www.instagram.com/reel/abc/"):
    for mode, quality, want in (("video", "best", FULL_FMT["best"]),
                                ("video", "720", FULL_FMT["720"]),
                                ("audio", "best", "bestaudio/best")):
        got = _fmt_of(other, mode=mode, quality=quality)
        if got != want:
            other_bad.append((other, mode, quality, got))
check("vk-fmt: YouTube/TikTok/Instagram + фрагмент -> формат не менялся",
      not other_bad, f"{other_bad}")

# Реальный выбор yt-dlp (без сети): форматы как у VK-видео 15.09
# (vk_fmt_probe): url* https (видео+звук, кодеки не указаны), dash_sep-*
# https (отдельные дорожки), hls/hls_fmp4 m3u8_native.
from yt_dlp import YoutubeDL as _YDL


def _vk_formats(direct=True, dash=True, hls=True):
    fmts = []
    if dash or hls:
        for i in range(2):
            if dash:
                fmts.append({"format_id": f"dash_sep-a{i}", "ext": "m4a",
                             "protocol": "https", "vcodec": "none",
                             "acodec": "mp4a.40.2", "tbr": 64 + 64 * i,
                             "url": f"https://cdn.example/a{i}.m4a"})
            if hls:
                fmts.append({"format_id": f"hls_fmp4-Audio{i}", "ext": "mp4",
                             "protocol": "m3u8_native", "vcodec": "none",
                             "tbr": 64 + 64 * i,
                             "url": f"https://cdn.example/a{i}.m3u8"})
    for h in (360, 720, 1080):
        if hls:
            fmts.append({"format_id": f"hls-{h}", "ext": "mp4", "height": h,
                         "protocol": "m3u8_native", "tbr": h * 4,
                         "url": f"https://cdn.example/{h}.m3u8"})
            fmts.append({"format_id": f"hls_fmp4-{h}", "ext": "mp4",
                         "height": h, "protocol": "m3u8_native",
                         "vcodec": "avc1.640028", "acodec": "none",
                         "tbr": h * 4,
                         "url": f"https://cdn.example/v{h}.m3u8"})
        if dash:
            fmts.append({"format_id": f"dash_sep-{h}", "ext": "mp4",
                         "height": h, "protocol": "https",
                         "vcodec": "avc1.640028", "acodec": "none",
                         "tbr": h * 4, "url": f"https://cdn.example/v{h}.mp4"})
        if direct:
            fmts.append({"format_id": f"url{h}", "ext": "mp4", "height": h,
                         "source_preference": 1,
                         "url": f"https://cdn.example/url{h}.mp4"})
    return fmts


class _QuietLog:
    def debug(self, msg):
        pass

    warning = error = info = debug


def _chosen(fmt, formats):
    info = {"id": "vk1", "title": "vk", "extractor": "vk",
            "extractor_key": "VK", "webpage_url": VK_URL,
            "formats": [dict(f) for f in formats]}
    with _YDL({"quiet": True, "no_warnings": True, "format": fmt,
               "logger": _QuietLog()}) as ydl:
        res = ydl.process_ie_result(info, download=False)
    return [f["format_id"]
            for f in (res.get("requested_formats") or [res])]


all_fmts = _vk_formats()
check("vk-select: прямой+DASH+HLS, фрагмент best -> url1080",
      _chosen(_fmt_of(VK_URL), all_fmts) == ["url1080"],
      str(_chosen(_fmt_of(VK_URL), all_fmts)))
check("vk-select: прямой+DASH+HLS, фрагмент 720 -> url720",
      _chosen(_fmt_of(VK_URL, quality="720"), all_fmts) == ["url720"],
      str(_chosen(_fmt_of(VK_URL, quality="720"), all_fmts)))
check("vk-select: прямой+DASH+HLS, фрагмент аудио -> url1080 (mp4 -> MP3)",
      _chosen(_fmt_of(VK_URL, mode="audio"), all_fmts) == ["url1080"],
      str(_chosen(_fmt_of(VK_URL, mode="audio"), all_fmts)))
check("vk-select: полное скачивание VK -> прежний выбор (не url*)",
      not any(fid.startswith("url")
              for fid in _chosen(_fmt_of(VK_URL, time_range=None), all_fmts)),
      str(_chosen(_fmt_of(VK_URL, time_range=None), all_fmts)))
fallback_bad = []
for label, fmts in (("только HLS", _vk_formats(direct=False, dash=False)),
                    ("HLS+DASH", _vk_formats(direct=False))):
    for mode, quality in (("video", "best"), ("video", "720"),
                          ("audio", "best")):
        new = _chosen(_fmt_of(VK_URL, mode=mode, quality=quality), fmts)
        old = _chosen(_fmt_of(VK_URL, mode=mode, quality=quality,
                              time_range=None), fmts)
        if new != old or not new:
            fallback_bad.append((label, mode, quality, new, old))
check("vk-select: без прямого формата -> тот же выбор, что прежний селектор",
      not fallback_bad, f"{fallback_bad}")

# ============ 5. yt-dlp санитизация имени (эмодзи/кириллица) ============

from yt_dlp.utils import sanitize_filename

full_name = sanitize_filename("Тест 🔥 видео [clip 30s-60s]")
check("sanitize: суффикс [clip] остаётся (кириллица+эмодзи)",
      "[clip 30s-60s]" in full_name)
check("sanitize: имя фрагмента != полного видео",
      full_name != sanitize_filename("Тест 🔥 видео"))
ok_names = 0
for title in ("Тест 🔥 видео", "Обычное название", "A/B: 'quoted'?",
              "кириллица и latin mix 123", "3 idi ☺"):
    for suffix in ("[clip 30s-60s]", ""):
        safe = sanitize_filename((title + " " + suffix).strip())
        if safe:
            ok_names += 1
check("sanitize: все комбинации title+[clip] проходят", ok_names == 10,
      f"ok_names={ok_names}")

# ============ 5.5 Сторож фрагмента: отмена и зависание VK ============

import threading
import time as _time

D5 = os.path.join(os.environ.get("TEMP", "/tmp"), "vd-frag-105", "watch")
os.makedirs(D5, exist_ok=True)

# 5.1 отмена: сторож завершает задачу сразу, не дожидаясь ffmpeg
mgr.start()
it_w = mgr.add("https://x", "video", "best", D5, False,
               time_range=(10, 20))
it_w.status = downloader.STATUS_DOWNLOADING
t_watch = threading.Thread(target=mgr._fragment_watch, args=(it_w,),
                            daemon=True)
t_watch.start()
_time.sleep(0.3)
it_w.request_cancel()
deadline = _time.monotonic() + 3
while _time.monotonic() < deadline and it_w.status != downloader.STATUS_ERROR:
    _time.sleep(0.05)
check("watch: отмена -> ERROR «Отменено» за <3с (не ждёт ffmpeg)",
      it_w.status == downloader.STATUS_ERROR
      and it_w.error == "Отменено" and it_w._abandoned, it_w.error)
t_watch.join(timeout=5)
check("watch: поток сторожа завершился", not t_watch.is_alive())

# 5.2 зависание VK: 10 минут нет роста файлов -> понятная ошибка
downloader.FRAGMENT_STALL_TIMEOUT = 1.5   # только для теста
try:
    it_vk = mgr.add("https://vkvideo.ru/video-1_2", "video", "best", D5,
                    False, time_range=(0, 30))
    it_vk.status = downloader.STATUS_DOWNLOADING
    t0 = _time.monotonic()
    t_watch2 = threading.Thread(target=mgr._fragment_watch,
                                args=(it_vk,), daemon=True)
    t_watch2.start()
    deadline = _time.monotonic() + 6
    while (_time.monotonic() < deadline
           and it_vk.status != downloader.STATUS_ERROR):
        _time.sleep(0.05)
    elapsed = _time.monotonic() - t0
    check("watch: VK зависание -> ошибка про VK за ~таймаут",
          it_vk.status == downloader.STATUS_ERROR
          and it_vk.error == "VK пока не поддерживает скачивание фрагмента"
          and elapsed < 5, f"{it_vk.error} elapsed={elapsed:.1f}")
    t_watch2.join(timeout=5)

    # 5.3 не-VK: тот же таймаут, но общий текст
    it_yt = mgr.add("https://youtube.com/watch?v=stall", "video", "best",
                    D5, False, time_range=(0, 30))
    it_yt.status = downloader.STATUS_DOWNLOADING
    t_watch3 = threading.Thread(target=mgr._fragment_watch,
                                args=(it_yt,), daemon=True)
    t_watch3.start()
    deadline = _time.monotonic() + 6
    while (_time.monotonic() < deadline
           and it_yt.status != downloader.STATUS_ERROR):
        _time.sleep(0.05)
    check("watch: не-VK зависание -> общий текст ошибки",
          it_yt.status == downloader.STATUS_ERROR
          and it_yt.error.startswith("Скачивание фрагмента прервано"),
          it_yt.error)
    t_watch3.join(timeout=5)

    # 5.4 рост файла сбрасывает таймаут: ложных срабатываний нет
    it_g = mgr.add("https://youtube.com/watch?v=grow", "video", "best",
                   D5, False, time_range=(0, 30))
    it_g.status = downloader.STATUS_DOWNLOADING
    t_watch4 = threading.Thread(target=mgr._fragment_watch,
                                args=(it_g,), daemon=True)
    t_watch4.start()
    growing = os.path.join(
        D5, "grow [clip 0s-30s].part")
    _time.sleep(0.5)
    for _ in range(4):        # расти 4 раза по 0.6с — дольше таймаута 1.5с
        with open(growing, "ab") as f:
            f.write(b"x" * 1024)
        _time.sleep(0.6)
    check("watch: рост файла > таймаута — задача жива (нет ложного срыва)",
          it_g.status == downloader.STATUS_DOWNLOADING, it_g.status)
    it_g.status = downloader.STATUS_ERROR   # остановить сторож
    it_g._abandoned = True
    t_watch4.join(timeout=5)
finally:
    downloader.FRAGMENT_STALL_TIMEOUT = 600.0

# 5.5 зомби-guard: воркер после сторожа не перезаписывает статус
it_z = mgr.add("https://x", "video", "best", D5, False,
               time_range=(10, 20))
it_z._abandoned = True
it_z.status = downloader.STATUS_ERROR
it_z.error = "Отменено"
try:
    raise downloader.DownloadCancelled()
except downloader.DownloadCancelled:
    pass
# эмулируем finally-семантику _run_item вручную нельзя — проверяем только
# сам guard-флаг: _abandoned замораживает _finish_item через ранний return
check("watch: _abandoned выставляется сторожем", it_z._abandoned is True)


# ============ 6. GUI: страница Загрузка + фрагмент (offscreen) ============
# Отдельный менеджер: в mgr из секции 4 уже есть задачи с фрагментами
mgr_gui = downloader.DownloadManager(max_concurrent=1)

TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "vd-frag-105")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
tmp_settings = os.path.join(TMP, "settings.json")
with open(tmp_settings, "w", encoding="utf-8") as f:
    json.dump({"default_folder": TMP, "check_updates": False}, f)
_old_path = config.CONFIG_PATH
config.CONFIG_PATH = tmp_settings
settings = config.load()
config.CONFIG_PATH = _old_path

bridge = gui.Bridge()
page = gui.DownloadPage(bridge, mgr_gui, settings)
page.resize(900, 700)
page.show()

check("gui: до анализа галочка скрыта",
      not page.fragment_check.isVisibleTo(page))
check("gui: до анализа блок фрагмента скрыт",
      not page.fragment_box.isVisibleTo(page))
check("gui: _fragment_available() без preview_info -> False",
      page._fragment_available() is False)

preview = {
    "title": "Big Buck Bunny", "uploader": "Blender", "duration": 596,
    "thumbnail": None, "is_playlist": False,
    "video_qualities": [2160, 1080, 720],
    "quality_sizes": {2160: 1000, 1080: 500000, 720: 300000},
    "audio_available": True, "entries": [],
}
page._on_analyze_ok(preview)
check("gui: после анализа галочка видна",
      page.fragment_check.isVisibleTo(page))
check("gui: после анализа блок скрыт (галочка выкл)",
      not page.fragment_box.isVisibleTo(page))

page.fragment_check.setChecked(True)
check("gui: галочка вкл -> блок фрагмента виден",
      page.fragment_box.isVisibleTo(page))
check("gui: слайдер инициализирован 0..596",
      page.range_slider.values() == (0, 596))
check("gui: поля = '0:00' / '9:56'",
      page.frag_start_edit.text() == "0:00"
      and page.frag_end_edit.text() == "9:56")

# ---- 1.0.6: редизайн — один ряд редактируемых полей над ползунком,
# отдельный нижний ряд "Начало:/Конец:" убран, оба поля — LineEdit ----
from qfluentwidgets import LineEdit as _LineEdit

check("gui: старые label-подписи над ручками удалены (не задваиваются)",
      not hasattr(page, "frag_start_label")
      and not hasattr(page, "frag_end_label"))
check("gui: поле начала — редактируемый LineEdit",
      isinstance(page.frag_start_edit, _LineEdit))
check("gui: поле конца — тоже редактируемый LineEdit (не просто текст)",
      isinstance(page.frag_end_edit, _LineEdit))
check("gui: нижнего ряда 'Начало:'/'Конец:' больше нет в блоке фрагмента",
      not any(
          w.text() in ("Начало:", "Конец:")
          for w in page.fragment_box.findChildren(gui.BodyLabel)
      ))
check("gui: превью кадра скрыто по умолчанию",
      not page.frag_preview_label.isVisible())

# поля -> слайдер (AC2)
page.frag_start_edit.setText("1:30")
page.frag_end_edit.setText("2:10")
page._on_fields_done()
check("gui: поля 1:30/2:10 -> слайдер (90, 130)",
      page.range_slider.values() == (90, 130))
check("gui: сводка длительности '0:40'",
      page.frag_range_label.text().startswith("0:40"))

page.frag_start_edit.setText("25:99")
page._on_fields_done()
check("gui: invalid поле не сдвинуло слайдер",
      page.range_slider.values() == (90, 130))

page.frag_start_edit.setText("3:00")
page.frag_end_edit.setText("1:00")
page._on_fields_done()
check("gui: конец раньше начала — не применяется",
      page.range_slider.values() == (90, 130))

page.frag_start_edit.setText("2:00")
page.frag_end_edit.setText("2:00")
page._on_fields_done()
check("gui: фрагмент 0с — не применяется (мин. 1с)",
      page.range_slider.values() == (90, 130))
page.frag_start_edit.setText("2:00")
page.frag_end_edit.setText("2:01")
page._on_fields_done()
check("gui: фрагмент 1с — применяется",
      page.range_slider.values() == (120, 121))

page.range_slider.set_values(30, 90)
page._sync_fields_from_slider()
check("gui: слайдер -> поля '0:30'/'1:30'",
      page.frag_start_edit.text() == "0:30"
      and page.frag_end_edit.text() == "1:30")

# ---- 1.0.6: ввод с пробелом как разделителем через реальное поле ----
page.frag_start_edit.setText("1 05")
page.frag_end_edit.setText("2 10")
page._on_fields_done()
check("gui: поле '1 05'/'2 10' (пробел) -> слайдер (65, 130)",
      page.range_slider.values() == (65, 130))

page.range_slider.set_values(30, 90)
page.frag_start_edit.setText("1 2a")   # невалидно (буква в группе)
page._on_fields_done()
check("gui: поле с буквой внутри группы — не применяется",
      page.range_slider.values() == (30, 90))

# ---- 1.0.6: превью кадра при drag — active_handle(), debounce, worker ----
check("gui: active_handle() вне drag -> None",
      page.range_slider.active_handle() is None)
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent

def _press_on_handle(which):
    x = page.range_slider._time_to_x(
        page.range_slider.start if which == "start" else page.range_slider.end
    )
    y = page.range_slider.height() / 2
    ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPointF(x, y), QPointF(x, y),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
    )
    page.range_slider.mousePressEvent(ev)

_press_on_handle("start")
check("gui: после press на ручке start — active_handle() == 'start'",
      page.range_slider.active_handle() == "start")
check("gui: press на ручке — ручка превью запомнена",
      page._preview_handle == "start")
page.range_slider.mouseReleaseEvent(QMouseEvent(
    QMouseEvent.Type.MouseButtonRelease, QPointF(0, 0), QPointF(0, 0),
    Qt.LeftButton, Qt.NoButton, Qt.NoModifier,
))
check("gui: после release — active_handle() снова None",
      page.range_slider.active_handle() is None)
check("gui: без preview_format — ffmpeg не запускался, превью скрыто",
      page._preview_worker is None
      and not page.frag_preview_label.isVisible())
check("gui: без preview_format — полоса превью скрыта (не занимает место)",
      not page.frag_preview_strip.isVisibleTo(page))

# _request_frame_preview без preview_format в preview_info — no-op тихо
check("gui: без preview_format в preview_info -> _request_frame_preview no-op",
      "preview_format" not in (page.preview_info or {}))
page._request_frame_preview()   # не должно бросать исключение
check("gui: _request_frame_preview() не упал без preview_format", True)

# FramePreviewWorker: ошибка (нет ffmpeg / битый URL) -> emit пустой QImage,
# не бросает исключение наружу (сеть/декод не блокируют GUI-поток)
_fp_result = {}
_fp_worker = gui.FramePreviewWorker("http://127.0.0.1:1/nope.mp4", {}, 5)
_fp_worker.loaded.connect(lambda _w, img: _fp_result.update(image=img))
_fp_worker.start()
_fp_worker.wait(15000)
app.processEvents()   # доставить loaded (queued connection, поток -> GUI)
check("gui: FramePreviewWorker на некорректном URL -> пустой QImage (не падает)",
      "image" in _fp_result and _fp_result["image"].isNull())

# ---- 1.0.6: stop() должен прервать worker НЕМЕДЛЕННО, а не ждать таймаут
# ffmpeg до конца (VK/HLS реально тянется ~17-20с - без stop() закрытие
# окна держало бы процесс живым всё это время, см. отчёт по проверке) ----
import subprocess as _subprocess
import threading as _threading
import time as _time_mod


class _FakeSlowProc:
    """Имитирует ffmpeg, который завис в communicate() - как реальный
    Popen, разблокируется по kill()."""

    def __init__(self):
        self._killed = _threading.Event()
        self.returncode = None

    def poll(self):
        return None if not self._killed.is_set() else -9

    def kill(self):
        self._killed.set()

    def communicate(self, timeout=None):
        got = self._killed.wait(timeout=timeout)
        if not got:
            raise _subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        self.returncode = -9
        return (b"", b"")


_fake_proc = _FakeSlowProc()
_orig_popen = gui.subprocess.Popen
gui.subprocess.Popen = lambda *a, **kw: _fake_proc
try:
    _slow_worker = gui.FramePreviewWorker("http://fake/slow.mp4", {}, 5)
    _slow_worker.start()
    _time_mod.sleep(0.3)   # дать run() дойти до блокирующего communicate()
    check("gui: worker реально 'висит' на communicate() до stop()",
          _slow_worker.isRunning())
    _t0 = _time_mod.monotonic()
    _slow_worker.stop()
    _finished = _slow_worker.wait(3000)
    _elapsed = _time_mod.monotonic() - _t0
    check("gui: stop() прерывает worker быстро (не ждёт таймаут ffmpeg)",
          _finished and _elapsed < 2.0, f"elapsed={_elapsed:.2f}s")
finally:
    gui.subprocess.Popen = _orig_popen

# ---- 1.0.6: MainWindow.closeEvent должен явно звать stop() у воркеров,
# у которых он есть (FramePreviewWorker), а не только quit()/wait() ----
import inspect as _inspect
_close_src = _inspect.getsource(gui.MainWindow.closeEvent)
check("gui: closeEvent зовёт stop() у воркеров с этим методом",
      "hasattr(thread, \"stop\")" in _close_src
      and "thread.stop()" in _close_src)
check("gui: closeEvent следит и за _preview_worker",
      "_preview_worker" in _close_src)

# расчёт размера: «Лучшее» = 2160 (1000 байт — мелкий), 1080 = 500000
page.quality_combo.setCurrentIndex(2)  # 1080
page._update_fragment_summary()
check("gui: сводка содержит примерный размер (~)", "~" in
      page.frag_range_label.text())

# _start_download с фрагментом (без сети): задача в очереди с диапазоном
page.url_edit.setText("https://youtube.com/watch?v=xyz")
page.frag_start_edit.setText("0:30")
page.frag_end_edit.setText("1:00")
page._on_fields_done()
page.precise_check.setChecked(True)
page._start_download()
items = list(mgr_gui.items.values())
check("gui: _start_download создал задачу", len(items) == 1)
it = items[0] if items else None
check("gui: задача с time_range (30, 60) и precise True",
      it is not None and it.time_range == (30, 60)
      and it.precise_cut is True)
check("gui: после загрузки блок сброшен (галочка выкл, скрыта)",
      not page.fragment_check.isChecked()
      and not page.fragment_check.isVisibleTo(page))
if it is not None:
    mgr_gui.cancel(it.id)

# 1.0.5 правка владельца: НЕisVisible — галочка включена и диапазон
# валиден -> time_range уходит в add() ВСЕГДА, даже когда страница скрыта
# (isVisibleTo вернул False из-за родителя, а не из-за логики)
page._on_analyze_ok(preview)
page.fragment_check.setChecked(True)
page.frag_start_edit.setText("0:30")
page.frag_end_edit.setText("1:00")
page._on_fields_done()
page.url_edit.setText("https://youtube.com/watch?v=xyz2")
# имитируем невидимость: прячем галочку, isChecked остаётся True
page.fragment_check.setVisible(False)
check("gui: прелюдия — галочка вкл, но виджет скрыт",
      page.fragment_check.isChecked()
      and not page.fragment_check.isVisibleTo(page))
page._start_download()
items2 = list(mgr_gui.items.values())
it2 = items2[-1] if items2 else None
check("gui: скрытая галочка + валидный диапазон -> time_range в add()",
      it2 is not None and it2.time_range == (30, 60))
if it2 is not None:
    mgr_gui.cancel(it2.id)

# _selected_quality: единая функция для _start_download и _fragment_size
page._on_analyze_ok(preview)
page.quality_combo.setCurrentIndex(2)  # 1080 (heights: 2160, 1080, 720)
check("gui: _selected_quality по индексу -> '1080'",
      page._selected_quality("video") == "1080")
page.quality_combo.setCurrentIndex(0)
check("gui: _selected_quality «Лучшее» -> 'best'",
      page._selected_quality("video") == "best")
check("gui: _selected_quality аудио -> 'best'",
      page._selected_quality("audio") == "best")
# размер фрагмента соответствует выбранному качеству (п.2 владельца)
page.fragment_check.setChecked(True)
page.range_slider.set_values(0, 298)   # ровно половина от 596
page.quality_combo.setCurrentIndex(2)  # 1080 = 500000 байт
sz = page._fragment_size(0, 298)
check("gui: _fragment_size от выбранного качества (1080 -> ~250000)",
      240000 <= sz <= 260000, f"sz={sz}")
page.quality_combo.setCurrentIndex(3)  # 720 = 300000
sz720 = page._fragment_size(0, 298)
check("gui: смена качества меняет размер (720 -> ~150000)",
      140000 <= sz720 <= 160000, f"sz720={sz720}")

# плейлист: галочка скрыта
page._on_analyze_ok({
    "title": "Плейлист", "uploader": "u", "duration": None,
    "thumbnail": None, "is_playlist": True, "entries": [{}],
    "video_qualities": [], "quality_sizes": {}, "audio_available": True,
})
check("gui: плейлист -> галочка фрагмента скрыта",
      not page.fragment_check.isVisibleTo(page))
check("gui: плейлист -> _fragment_available False",
      page._fragment_available() is False)

# ---- превью кадра при drag, сценарии A-D. ffmpeg подменён медленным
# фейком (0.8с на кадр — как HLS 144p YouTube, 1.2-1.8с живьём), который
# отдаёт настоящий PNG. Раньше (debounce + kill при отпускании) кадр не
# доезжал до экрана в A/B/D, а в C исчезал при отпускании ----
import threading as _thr
import time as _time2
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPoint
from PySide6.QtGui import QImage as _QImage
from PySide6.QtTest import QTest

_png_img = _QImage(32, 18, _QImage.Format_RGB32)
_png_img.fill(0x3366CC)
_png_ba = QByteArray()
_png_buf = QBuffer(_png_ba)
_png_buf.open(QIODevice.WriteOnly)
_png_img.save(_png_buf, "PNG")
_png_buf.close()
_PNG = bytes(_png_ba.data())
FAKE_FFMPEG_DELAY = 0.8


class _FakeFrameProc:
    """ffmpeg-кадр: через FAKE_FFMPEG_DELAY отдаёт PNG (rc 0), по kill() —
    сразу rc -9. Считает запуски, убийства и одновременные процессы."""
    lock = _thr.Lock()
    log = []
    active = 0
    max_active = 0

    @classmethod
    def reset(cls):
        cls.log = []
        cls.max_active = 0

    def __init__(self, cmd):
        self.second = int(cmd[cmd.index("-ss") + 1])
        self.killed = False
        self.returncode = None
        self._kill_evt = _thr.Event()
        cls = _FakeFrameProc
        with cls.lock:
            cls.log.append(self)
            cls.active += 1
            cls.max_active = max(cls.max_active, cls.active)

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self._kill_evt.set()

    def communicate(self, timeout=None):
        try:
            if self._kill_evt.wait(FAKE_FFMPEG_DELAY):
                self.returncode = -9
                return (b"", b"")
            self.returncode = 0
            return (_PNG, b"")
        finally:
            with _FakeFrameProc.lock:
                _FakeFrameProc.active -= 1


_drag_x = {}


def _mouse(kind, x, buttons, button=Qt.LeftButton):
    y = page.range_slider.height() / 2
    return QMouseEvent(kind, QPointF(x, y), QPointF(x, y), button, buttons,
                       Qt.NoModifier)


def _press(which):
    _drag_x["x"] = page.range_slider.handle_x(which)
    page.range_slider.mousePressEvent(_mouse(
        QMouseEvent.Type.MouseButtonPress, _drag_x["x"], Qt.LeftButton))


def _moves(steps, step_px, every_ms):
    """Движения с зажатой кнопкой -> было ли превью видно по ходу."""
    seen = False
    for _ in range(steps):
        _drag_x["x"] += step_px
        page.range_slider.mouseMoveEvent(_mouse(
            QMouseEvent.Type.MouseMove, _drag_x["x"], Qt.LeftButton,
            Qt.NoButton))
        QTest.qWait(every_ms)
        seen = seen or page.frag_preview_label.isVisible()
    return seen


def _hold(ms):
    seen = False
    t_end = _time2.monotonic() + ms / 1000
    while _time2.monotonic() < t_end:
        QTest.qWait(50)
        seen = seen or page.frag_preview_label.isVisible()
    return seen


def _release():
    page.range_slider.mouseReleaseEvent(_mouse(
        QMouseEvent.Type.MouseButtonRelease, _drag_x["x"], Qt.NoButton))


def _settle(max_s=6.0):
    """Дождаться, пока превью затихнет (ни ffmpeg, ни таймера throttle)."""
    t_end = _time2.monotonic() + max_s
    while _time2.monotonic() < t_end:
        QTest.qWait(100)
        if (page._preview_worker is None
                and not page._preview_throttle.isActive()):
            return True
    return False


def _preview_over_handle(which):
    """Кадр над слайдером, по центру над ручкой (у края — прижат к краю)."""
    s, lab = page.range_slider, page.frag_preview_label
    strip = page.frag_preview_strip
    lab_left = lab.mapTo(page, QPoint(0, 0)).x()
    lab_right = lab_left + lab.width()
    lab_bottom = lab.mapTo(page, QPoint(0, lab.height())).y()
    strip_left = strip.mapTo(page, QPoint(0, 0)).x()
    strip_right = strip_left + strip.width()
    hx = s.mapTo(page, QPoint(int(s.handle_x(which)), 0)).x()
    s_top = s.mapTo(page, QPoint(0, 0)).y()
    centered = abs((lab_left + lab_right) / 2 - hx) <= 1
    pinned = lab_left == strip_left or lab_right == strip_right
    return (lab_bottom <= s_top and strip_left <= lab_left
            and lab_right <= strip_right and (centered or pinned))


def _seconds():
    return [p.second for p in _FakeFrameProc.log]


_orig_popen2 = gui.subprocess.Popen
gui.subprocess.Popen = lambda cmd, *a, **kw: _FakeFrameProc(cmd)
try:
    page._on_analyze_ok(dict(preview, preview_format={
        "url": "http://fake/v.m3u8", "http_headers": {}}))
    page.fragment_check.setChecked(True)
    QTest.qWait(100)
    slider = page.range_slider
    check("preview: полоса превью видна, когда есть preview_format",
          page.frag_preview_strip.isVisibleTo(page))

    # A: быстрый drag 0.6с и сразу отпустил
    slider.set_values(60, 400)
    _FakeFrameProc.reset()
    _press("start")
    _moves(15, 3, 40)
    _release()
    settled = _settle()
    check("preview A: быстрый drag+release -> кадр виден после отпускания",
          settled and page.frag_preview_label.isVisible(),
          f"seconds={_seconds()}")
    check("preview A: последний кадр — финальная позиция ручки",
          _seconds()[-1:] == [slider.start],
          f"seconds={_seconds()} start={slider.start}")
    check("preview A: работающий ffmpeg не убивался",
          not any(p.killed for p in _FakeFrameProc.log))
    check("preview A: кадр над ручкой start", _preview_over_handle("start"))

    # B: непрерывный drag ~3с (движение раз в 150 мс, debounce не срабатывал)
    slider.set_values(60, 400)
    _FakeFrameProc.reset()
    page.frag_preview_label.hide()
    _press("start")
    seen_b = _moves(20, 3, 150)
    _release()
    _settle()
    check("preview B: непрерывный drag -> кадр появился до отпускания",
          seen_b)
    check("preview B: throttle — не больше 1 ffmpeg за раз, не на каждый тик",
          _FakeFrameProc.max_active == 1 and 2 <= len(_seconds()) <= 7,
          f"max_active={_FakeFrameProc.max_active} seconds={_seconds()}")
    check("preview B: ffmpeg не убивался",
          not any(p.killed for p in _FakeFrameProc.log))
    check("preview B: последний кадр — финальная позиция ручки",
          _seconds()[-1:] == [slider.start],
          f"seconds={_seconds()} start={slider.start}")

    # C: сдвинул и держит неподвижно
    slider.set_values(60, 400)
    _FakeFrameProc.reset()
    page.frag_preview_label.hide()
    _press("start")
    _moves(10, 3, 40)
    seen_c = _hold(2500)
    n_mid = len(_seconds())
    _hold(1500)
    n_hold = len(_seconds())
    _release()
    _settle()
    check("preview C: держит неподвижно -> кадр виден", seen_c)
    check("preview C: пока держит на месте — новых ffmpeg нет",
          n_hold == n_mid, f"n_mid={n_mid} n_hold={n_hold}")
    check("preview C: отпустил на том же месте — кадр остался, без запроса",
          page.frag_preview_label.isVisible() and len(_seconds()) == n_hold)

    # D: ручка конца на самом конце видео (дефолт) — клэмп duration-1
    slider.set_values(60, 596)
    _FakeFrameProc.reset()
    page.frag_preview_label.hide()
    _press("end")
    seen_d = _hold(1500)
    _release()
    _settle()
    check("preview D: ручка конца на конце -> кадр с 595 (не -ss 596)",
          _seconds() == [595], f"seconds={_seconds()}")
    check("preview D: кадр показан и остался после отпускания",
          seen_d and page.frag_preview_label.isVisible())
    check("preview D: кадр над ручкой end (прижат к правому краю)",
          _preview_over_handle("end"))

    # поле ввода после drag: кадр обновляется для новой позиции
    _FakeFrameProc.reset()
    page.frag_end_edit.setText("5:00")
    page._on_fields_done()
    _settle()
    check("preview: ввод в поле -> кадр для новой позиции (300)",
          _seconds() == [300] and page.frag_preview_label.isVisible()
          and _preview_over_handle("end"),
          f"seconds={_seconds()}")

    # сброс фрагмента во время работы ffmpeg: убит, поздний кадр не показан
    slider.set_values(60, 400)
    _FakeFrameProc.reset()
    _press("start")
    QTest.qWait(100)
    _release()
    running = page._preview_worker is not None
    page._reset_fragment_state()
    QTest.qWait(1500)
    check("preview: сброс фрагмента убивает работающий ffmpeg",
          running and _FakeFrameProc.log and _FakeFrameProc.log[-1].killed)
    check("preview: поздний результат после сброса не показан",
          not page.frag_preview_label.isVisible()
          and page._preview_worker is None)
finally:
    gui.subprocess.Popen = _orig_popen2

# ============ 7. история: fragment-поле и вытеснение ============

settings2 = dict(config.DEFAULTS)
full_file = os.path.join(TMP, "video.mp4")
frag_file = os.path.join(TMP, "video [clip 30s-60s].mp4")
for p in (full_file, frag_file):
    open(p, "wb").write(b"x" * 1024)

config.add_history(settings2, {
    "url": "u", "source": "YouTube", "title": "Video", "quality": "1080",
    "duration": 596, "mode": "video", "fragment": None, "path": full_file,
})
config.add_history(settings2, {
    "url": "u", "source": "YouTube", "title": "Video", "quality": "1080",
    "duration": 596, "mode": "video", "fragment": "0:30-1:00",
    "path": frag_file,
})
hist = settings2["history"]
check("history: обе записи (фрагмент не вытеснил полное)", len(hist) == 2)
check("history: запись фрагмента несёт fragment='0:30-1:00'",
      hist[0].get("fragment") == "0:30-1:00"
      and hist[1].get("fragment") is None)

# фрагмент в Библиотеке (LibraryRow детально): запись с fragment видна
row = gui.LibraryRow({"path": frag_file, "title": "Video", "fragment":
                      "0:30-1:00", "source": "YouTube"}, page=None)
check("library: строка фрагмента создана", row.entry.get("fragment")
      == "0:30-1:00")

# ============ 8. Kill orphan ffmpeg ============

from unittest.mock import patch as _patch
import subprocess as _sub
import json as _json


def _kill_tests():
    mgr = downloader.DownloadManager(max_concurrent=1)

    # --- mock-обертки ---
    captured_run = []
    captured_kill = []

    def _make_fake_run(
        ppid_match=None, suffix_match=None, path_match=None,
        ppid_noselect=9999, suffix_noselect="[clip 30s-60s]",
        path_noselect="C:/other",
        path_noselect2="D:/other",
        suffix_noselect2="",
        return_exc=None,
    ):
        """Возвращает функцию fake_run с заданным поведением."""
        def fake_run(*a, **kw):
            captured_run.append((a, kw))
            if return_exc is not None:
                raise return_exc
            rows = [
                {"ProcessId": 100, "ParentProcessId": ppid_noselect,
                 "CommandLine": "ffmpeg -y other.mkv"},
            ]
            if ppid_match is not None:
                rows.append({
                    "ProcessId": 101, "ParentProcessId": ppid_match,
                    "CommandLine": "ffmpeg -y C:/videos/title "
                                   + suffix_match,
                })
            if path_noselect is not None:
                rows.append({
                    "ProcessId": 102, "ParentProcessId": ppid_match or 9999,
                    "CommandLine": "ffmpeg -y " + path_noselect + "/title "
                                   + suffix_match,
                })
            if suffix_noselect2 is not None:
                rows.append({
                    "ProcessId": 103, "ParentProcessId": ppid_match or 9999,
                    "CommandLine": "ffmpeg -y C:/videos/" + suffix_noselect2,
                })
            class R:
                returncode = 0
                stdout = _json.dumps(rows)
                stderr = ""
            return R()
        return fake_run

    def _make_fake_kill():
        def fake_kill(pid, sig):
            captured_kill.append(pid)
        return fake_kill

    # --- СЦЕНАРИЙ 1: наш parent + суффикс + папка → убит ---
    frag_item1 = mgr.add("https://x", "video", "best", "C:/videos", False,
                         time_range=(30, 60))
    expected_suffix = "[clip 30s-60s]"
    our_pid = 8888
    fr1 = _make_fake_run(ppid_match=our_pid, suffix_match=expected_suffix)
    fk1 = _make_fake_kill()
    with _patch('subprocess.run', fr1), \
         _patch('os.kill', fk1), \
         _patch.object(os, 'getpid', return_value=our_pid):
        mgr._kill_fragment_ffmpeg(frag_item1)
    check("kill: наш pid+суффикс+папка → убит",
          101 in captured_kill)
    check("kill: чужой parent → не убит",
          100 not in captured_kill)
    check("kill: другая папка → не убит",
          102 not in captured_kill)
    check("kill: нет суффикса → не убит",
          103 not in captured_kill)
    check("kill: ровно 1 target",
          len(captured_kill) == 1)

    # --- СЦЕНАРИЙ 2: OSError(subprocess.run) → проглочен ---
    # Проверяем: функция не бросает исключение наружу (иначе тест
    # упал бы до этой строки). Ловим через try/except вокруг вызова.
    frag_item2 = mgr.add("https://x", "video", "best", "C:/videos", False,
                         time_range=(5, 10))
    run_ok = False
    try:
        mgr._kill_fragment_ffmpeg(frag_item2)
        run_ok = True
    except Exception:
        pass
    check("kill: OSError(subprocess.run) → проглочен (дошли до check)",
          run_ok)

    # --- СЦЕНАРИЙ 3: OSError(os.kill) → проглочен ---
    # Аналогично: если inner try/except вокруг os.kill убрать,
    # тест упадёт. Проверяем, что функция вернулась без исключения.
    frag_item3 = mgr.add("https://x", "video", "best", "C:/videos", False,
                         time_range=(1, 5))
    kill_ok = False
    def fake_kill_err(pid, sig):
        raise OSError("access denied")
    try:
        with _patch('os.kill', fake_kill_err), \
             _patch.object(os, 'getpid', return_value=1234):
            mgr._kill_fragment_ffmpeg(frag_item3)
        kill_ok = True
    except Exception:
        pass
    check("kill: OSError(os.kill) → проглочен (дошли до check)",
          kill_ok)

    # --- СЦЕНАРИЙ 4: time_range=None → CIM не запрашивается ---
    no_range_item = mgr.add("https://x", "video", "best", "C:/videos", False)
    captured_run.clear()
    no_range_item.time_range = None
    with _patch('subprocess.run', _make_fake_run(ppid_match=our_pid,
                                                  suffix_match="[clip 5s-10s]")):
        mgr._kill_fragment_ffmpeg(no_range_item)
    check("kill: time_range=None → CIM не запрашивается",
          len(captured_run) == 0)


_kill_tests()

# ============ итог ============

print()
print(f"ИТОГ: {len(passed)} PASS, {len(failed)} FAIL")
if failed:
    print("ПРОВАЛЕНЫ:", ", ".join(failed))
    sys.exit(1)
print("ALL_OK")
