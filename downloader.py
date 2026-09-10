"""Ядро загрузчика: анализ ссылок и менеджер очереди на yt-dlp.

DownloadItem — одна задача (метаданные + статус + управление).
DownloadManager — очередь задач: параллельные загрузки, пауза/отмена,
статусы, потокобезопасные колбэки для GUI.
"""

import os
import sys
import threading
import uuid

from yt_dlp import YoutubeDL

STATUS_QUEUED = "queued"
STATUS_ANALYZING = "analyzing"
STATUS_DOWNLOADING = "downloading"
STATUS_PAUSED = "paused"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_ERROR = "error"

ACTIVE_STATUSES = (STATUS_ANALYZING, STATUS_DOWNLOADING, STATUS_PROCESSING)


def fmt_speed(value):
    if value is None:
        return ""
    if value >= 1024 * 1024:
        return f"{value / 1024 / 1024:.1f} МБ/с"
    if value >= 1024:
        return f"{value / 1024:.0f} КБ/с"
    return f"{value:.0f} Б/с"


def fmt_eta(value):
    if value is None:
        return ""
    minutes, seconds = divmod(int(value), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def fetch_info(url, playlist=False):
    """Информация о ссылке без скачивания (для превью и валидации)."""
    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "noplaylist": not playlist,
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

    if "entries" in info:
        entries = [e for e in (info.get("entries") or []) if e]
        return {
            "title": info.get("title", "Плейлист"),
            "uploader": info.get("uploader", ""),
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
            "is_playlist": True,
            "video_qualities": [1080, 720, 480],
            "quality_sizes": {},
            "entries": [
                {"title": e.get("title", ""), "duration": e.get("duration")}
                for e in entries
            ],
        }

    formats = info.get("formats") or []
    heights = sorted({f.get("height") for f in formats if f.get("height")}, reverse=True)

    # Размер аудио: максимальный среди аудио-форматов
    audio_size = 0
    for f in formats:
        if f.get("acodec") not in (None, "none"):
            size = f.get("filesize") or f.get("filesize_approx") or 0
            audio_size = max(audio_size, size)

    # Лучший видео-размер для каждой высоты
    quality_sizes = {}
    for f in formats:
        height = f.get("height")
        if not height:
            continue
        size = f.get("filesize") or f.get("filesize_approx") or 0
        current = quality_sizes.get(height, 0)
        quality_sizes[height] = max(current, size)

    # Полный размер = видео + аудио (видео-трек обычно без звука)
    size_map = {h: v + (audio_size if audio_size else 0)
                for h, v in quality_sizes.items()}

    has_audio = any(f.get("acodec") not in (None, "none") for f in formats)
    return {
        "title": info.get("title", ""),
        "uploader": info.get("uploader") or info.get("channel", ""),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "is_playlist": False,
        "video_qualities": heights,      # включает 2160 (4K) и 4320 (8K)
        "quality_sizes": size_map,        # {высота: байты}
        "audio_available": has_audio,
        "entries": [],
    }


class DownloadItem:
    """Одна задача в очереди."""

    def __init__(self, url, mode="video", quality="best", output_dir=".",
                 playlist=False):
        self.id = uuid.uuid4().hex[:12]
        self.url = url
        self.mode = mode
        self.quality = quality
        self.output_dir = output_dir
        self.playlist = playlist

        self.status = STATUS_QUEUED
        self.title = ""
        self.uploader = ""
        self.thumbnail = None
        self.duration = None
        self.error = None

        self.progress = 0.0
        self.speed = None
        self.eta = None
        self.files = []

        self.downloaded_bytes = 0
        self.total_bytes = None

        self._cancel = threading.Event()
        self._pause = threading.Event()
        # Намерение пользователя (последнее действие побеждает):
        #   pause  -> _pause + _cancel,     _cancel_intent=False
        #   resume -> _resume_requested,    _pause сброшен
        #   cancel -> _cancel,              _cancel_intent=True
        # Нужно, чтобы «Пауза → сразу Отмена» завершала задачу (ERROR),
        # а не оставляла PAUSED.
        self._thread = None
        self._resume_requested = False
        self._cancel_intent = False

    @property
    def label(self):
        return self.title or self.url

    def request_pause(self):
        # Пауза доступна на всех активных фазах, включая анализ: в ANALYZING
        # _cancel прервёт extract_info по первому progress-хуку, как только
        # начнётся фаза скачивания (см. _run_item / _progress_hook).
        if self.status in (STATUS_QUEUED, STATUS_ANALYZING, STATUS_DOWNLOADING):
            self._pause.set()
            self._cancel.set()
            # отложенный resume и намерение отмены снимаются: актуальна пауза
            self._resume_requested = False
            self._cancel_intent = False

    def request_resume(self):
        # Не сбрасываем _cancel и не меняем статус здесь: если рабочий поток
        # ещё жив, это сделает планировщик после его фактического завершения
        # (иначе два потока качали бы один файл — гонка pause→resume).
        self._resume_requested = True
        self._pause.clear()
        self._cancel_intent = False

    def request_cancel(self):
        self._cancel.set()
        self._cancel_intent = True


class DownloadCancelled(Exception):
    pass


class DownloadManager:
    """Очередь загрузок: параллелизм, статусы, колбэки.

    События (вызываются из фоновых потоков!):
        on_change(item)   — любое изменение задачи
        on_queue_change() — изменился состав очереди
    """

    def __init__(self, max_concurrent=2, on_change=None, on_queue_change=None):
        self.max_concurrent = max(1, int(max_concurrent))
        self.on_change = on_change
        self.on_queue_change = on_queue_change
        self.items = {}
        self.order = []  # id-шники в порядке добавления
        self._lock = threading.Lock()
        self._scheduler_active = threading.Event()
        self._wake = threading.Event()

    # ---------- публичное API ----------

    def add(self, url, mode="video", quality="best", output_dir=".",
            playlist=False):
        """Добавить задачу. Возвращает DownloadItem."""
        item = DownloadItem(url, mode, quality, output_dir, playlist)
        with self._lock:
            self.items[item.id] = item
            self.order.append(item.id)
        self._notify_queue()
        self._wake.set()
        return item

    def pause(self, item_id):
        with self._lock:
            item = self.items.get(item_id)
        if item is None:
            return
        item.request_pause()
        # Задача в очереди (потока ещё нет) — сразу честная пауза
        if item.status == STATUS_QUEUED:
            item.status = STATUS_PAUSED
        self._notify(item)
        self._notify_queue()

    def resume(self, item_id):
        with self._lock:
            item = self.items.get(item_id)
        if item is None:
            return
        item.request_resume()
        # Перезапуск возможен, только если рабочий поток уже мёртв; в противном
        # случае _resume_requested подхватит finally-ветка _run_item или
        # планировщик после завершения потока (отложенный resume).
        thread = item._thread
        if thread is None or not thread.is_alive():
            self._restart_item(item)
        else:
            self._notify(item)
        self._wake.set()

    def _restart_item(self, item):
        """Сбросить события и вернуть задачу в очередь (поток мёртв)."""
        item._cancel.clear()
        item._pause.clear()
        item._resume_requested = False
        item._cancel_intent = False
        item.status = STATUS_QUEUED
        self._notify(item)

    def cancel(self, item_id):
        with self._lock:
            item = self.items.get(item_id)
        if item is None:
            return
        item.request_cancel()
        # Если рабочего потока нет — завершаем сразу. Мёртвый поток всегда
        # оставляет финальный статус, поэтому перезатираем только QUEUED/PAUSED
        # (иначе можно было бы затереть COMPLETED в момент завершения).
        thread = item._thread
        if thread is None or not thread.is_alive():
            if item.status in (STATUS_QUEUED, STATUS_PAUSED):
                item.status = STATUS_ERROR
                item.error = "Отменено"
                self._notify(item)
                self._notify_queue()
                self._wake.set()

    def remove(self, item_id):
        with self._lock:
            item = self.items.get(item_id)
            if item and item.status in ACTIVE_STATUSES:
                item.request_cancel()
        with self._lock:
            if item_id in self.items:
                del self.items[item_id]
                if item_id in self.order:
                    self.order.remove(item_id)
        self._notify_queue()
        self._wake.set()

    def set_max_concurrent(self, value):
        self.max_concurrent = max(1, int(value))
        self._wake.set()

    def active_count(self):
        with self._lock:
            return sum(
                1 for i in self.items.values()
                if i.status in ACTIVE_STATUSES
            )

    # ---------- внутреннее ----------

    def _notify(self, item):
        if self.on_change:
            try:
                self.on_change(item)
            except Exception:
                pass

    def _notify_queue(self):
        if self.on_queue_change:
            try:
                self.on_queue_change()
            except Exception:
                pass

    def _can_start(self, item):
        return (
            item.status == STATUS_QUEUED
            and not item._pause.is_set()
            and not item._cancel.is_set()
        )

    def _run_scheduler(self):
        while True:
            self._wake.wait(timeout=0.5)
            self._wake.clear()

            with self._lock:
                # Отложенный resume: пользователь нажал «Продолжить», пока
                # поток ещё разматывался. Поток умер → задача в очередь.
                # (Проверка is_alive() исключает второй поток у задачи.)
                for iid in list(self.order):
                    item = self.items.get(iid)
                    if (
                        item is not None
                        and item._resume_requested
                        and item.status == STATUS_PAUSED
                        and (item._thread is None
                             or not item._thread.is_alive())
                    ):
                        self._restart_item(item)

                # Пауза не занимает слот параллельности (BUG-2): активны
                # только реально работающие задачи.
                active = sum(
                    1 for i in self.items.values()
                    if i.status in ACTIVE_STATUSES
                )
                startable = [
                    self.items[iid] for iid in self.order
                    if iid in self.items
                    and self._can_start(self.items[iid])
                ]

            for item in startable[: max(0, self.max_concurrent - active)]:
                item.status = STATUS_ANALYZING
                self._notify(item)
                thread = threading.Thread(
                    target=self._run_item, args=(item,), daemon=True
                )
                item._thread = thread
                thread.start()

    def start(self):
        """Запустить планировщик (один раз)."""
        if not self._scheduler_active.is_set():
            self._scheduler_active.set()
            threading.Thread(target=self._run_scheduler, daemon=True).start()

    def _progress_hook(self, item):
        def hook(data):
            if item._cancel.is_set():
                raise DownloadCancelled()
            if data.get("status") == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                downloaded = data.get("downloaded_bytes", 0)
                item.downloaded_bytes = downloaded
                if total:
                    item.total_bytes = total
                    item.progress = downloaded / total * 100
                item.speed = data.get("speed")
                item.eta = data.get("eta")
                if item.status == STATUS_ANALYZING:
                    item.status = STATUS_DOWNLOADING
                self._notify(item)
        return hook

    def _build_options(self, item):
        if item.mode == "audio":
            fmt = "bestaudio/best"
        elif item.quality == "best":
            fmt = "bestvideo+bestaudio/best"
        else:
            fmt = (
                f"bestvideo[height<={item.quality}]+bestaudio/"
                f"best[height<={item.quality}]/best"
            )

        options = {
            "format": fmt,
            "outtmpl": os.path.join(item.output_dir, "%(title)s.%(ext)s"),
            "noplaylist": not item.playlist,
            "progress_hooks": [self._progress_hook(item)],
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "continuedl": True,   # докачка .part — основa паузы/возобновления
        }

        # В собранном exe (PyInstaller) ffmpeg лежит рядом с исполняемым
        # файлом; в dev-режиме yt-dlp ищет его в PATH.
        if getattr(sys, "frozen", False):
            options["ffmpeg_location"] = os.path.dirname(sys.executable)

        if item.mode == "audio":
            options["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]
        return options

    def _run_item(self, item):
        try:
            with YoutubeDL(self._build_options(item)) as ydl:
                info = ydl.extract_info(item.url, download=True)

                # Метаданные задачи (для отображения и истории)
                if not item.title:
                    item.title = info.get("title") or ""
                if item.duration is None:
                    item.duration = info.get("duration")
                if not item.thumbnail:
                    item.thumbnail = info.get("thumbnail")

                # extract_info вернулся — файл скачан полностью; даже если
                # пауза пришла в последний момент, задача завершена (при
                # возобновлении yt-dlp и так увидел бы готовый файл).
                item.status = STATUS_PROCESSING
                self._notify(item)

                if "entries" in info:
                    files = []
                    for entry in (info.get("entries") or []):
                        if entry:
                            files.append(ydl.prepare_filename(entry))
                else:
                    files = [ydl.prepare_filename(info)]

                if item.mode == "audio":
                    files = [os.path.splitext(p)[0] + ".mp3" for p in files]

                item.files = files
                item.progress = 100
                item.status = STATUS_COMPLETED
                self._notify(item)
        except DownloadCancelled:
            # Намерение пользователя (последнее действие побеждает):
            #   пауза → PAUSED (планировщик вернёт в очередь при resume),
            #   отмена → ERROR «Отменено».
            if item._cancel_intent:
                item.status = STATUS_ERROR
                item.error = "Отменено"
            else:
                item.status = STATUS_PAUSED
            self._notify(item)
        except Exception as exc:
            item.status = STATUS_ERROR
            item.error = str(exc)
            self._notify(item)
        finally:
            self._wake.set()
