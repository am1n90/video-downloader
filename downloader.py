"""Ядро загрузчика: анализ ссылок и менеджер очереди на yt-dlp.

DownloadItem — одна задача (метаданные + статус + управление).
DownloadManager — очередь задач: параллельные загрузки, пауза/отмена,
статусы, потокобезопасные колбэки для GUI.
"""

import os
import re
import sys
import threading
import time
import uuid

from yt_dlp import YoutubeDL

from config import get_logger

# Повтор переходящих ошибок (1.0.4): сетевые сбои и сбои извлечения yt-dlp
# иногда проходят со второй попытки (замечено на TikTok и VK). Повторяются
# ТОЛЬКО переходящие ошибки; постоянные (видео удалено, приватное, нужен
# вход) завершаются сразу — повтор их не исправит.
RETRY_ATTEMPTS = 3            # всего попыток: 1 основная + 2 повтора
RETRY_PAUSE_SECONDS = 2.0     # пауза между попытками

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


def fragment_suffix(start, end):
    """Суффикс имени файла фрагмента (1.0.5): «[clip 35s-95s]».

    Только латиница, цифры, пробел и дефис-минус — по правилу владельца
    (без русских букв и типографских тире): имя файла не зависит от
    раскладки/кодировки и гарантированно допустимо на Windows.
    Эмодзи/кириллица в %(title)s остаются — yt-dlp санитизирует их сам
    (sanitize_filename в prepare_filename).
    """
    return f"[clip {int(round(start))}s-{int(round(end))}s]"


# Фрагмент (1.0.5): yt-dlp качает секции через FFmpegFD — ffmpeg-процесс,
# progress-hooks НЕ вызываются (статус зависал в ANALYZING, отмена не
# срабатывала до конца ffmpeg). Сторож в _run_item следит за задачей:
#   - отмена: hooks не придут — задача честно завершается сразу;
#   - VK: HLS-секция зависает на медленном CDN (дважды по 15+ минут,
#     12.09 и 13.09) — нет роста файлов 10 минут -> понятная ошибка.
# «Прогресс» = рост файлов с суффиксом [clip] в папке назначения:
# точная обрезка 4K длится и дольше 10 минут, но файл растёт —
# ложных срабатываний нет.
FRAGMENT_STALL_TIMEOUT = 600.0    # 10 минут без роста файлов
FRAGMENT_WATCH_POLL = 1.0        # период опроса сторожа


def _is_vk_url(url):
    """VK-ссылка (для текста ошибки зависания фрагмента; как
    source_from_url в gui)."""
    low = (url or "").lower()
    return "vk.com" in low or "vkvideo" in low


# Переходящие сбои (1.0.4): сеть (таймауты, обрывы, SSL, 5xx) и сбои
# извлечения yt-dlp. Наблюдения на реальных ссылках: TikTok «Unexpected
# response from webpage request» и «Unable to extract ... universal data
# for rehydration», SSL CERTIFICATE_VERIFY_FAILED у VK — со второй попытки
# проходят. Проверяются ПЕРВЫМИ: «HTTP Error 503: Service Unavailable»
# содержит слово «unavailable», но 503 — именно переходящая.
RETRYABLE_MARKERS = (
    "timed out",
    "timeout",
    "connection",              # reset / refused / aborted
    "ssl",
    "certificate",
    "unexpected response",     # TikTok
    "unable to extract",       # TikTok (universal data for rehydration)
    "unable to download webpage",
    "temporary",
    "reset by peer",
    "internal server error",
    "500", "502", "503", "504",
)

# Ошибки, которые повтор не исправит: видео удалено/недоступно, приватное,
# нужен вход, возрастное ограничение, авторские права, гео-блокировка,
# неподдерживаемый URL, 404. Слова с границами (\b): голая подстрока "age"
# совпала бы с "webpage"/"message" и не дала бы повторять переходящие
# ошибки TikTok. Язык сообщений yt-dlp стабилен, перевода нет.
NO_RETRY_MARKERS = (
    "unavailable",              # Video unavailable (YouTube)
    "not available",            # в т.ч. not available in your country
    "private",
    "login",                    # login required
    "sign in",
    "log in",
    "unsupported url",
    "removed",
    "deleted",
    "age",                      # Confirm your age / age-restricted
    "copyright",
    "geo",                      # geo-restricted
    "404",
    "not a video",
    # Ошибки записи на диск: повтор с перекачкой файла их не исправит
    "no space left",           # [Errno 28] No space left on device
    "not enough space",        # WinError 112
    "permission denied",       # [Errno 13] / WinError 5
    "being used by another process",   # WinError 32 (файл занят)
    "unable to write data",    # обёртка yt-dlp для ошибок записи
)

_RETRYABLE_RE = re.compile(
    "|".join(re.escape(m) for m in RETRYABLE_MARKERS), re.IGNORECASE
)
_NO_RETRY_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(m) for m in NO_RETRY_MARKERS) + r")\b",
    re.IGNORECASE,
)


def _is_retryable(exc):
    """True — переходящая ошибка (сеть/извлечение), повтор имеет смысл.

    False — постоянная (видео удалено, приватное, нужен вход и т.п.):
    повтор не изменит результат, задача завершается сразу.

    Порядок: сначала переходящие маркеры (503 содержит «Unavailable», но
    повторяется), затем постоянные. Неизвестная ошибка — True: безопаснее
    дать повтору шанс (постоянная всё равно даст понятный текст после
    исчерпанных попыток), чем не повторить переходящую.
    """
    if isinstance(exc, DownloadCancelled):
        return False
    text = str(exc).lower()
    if _RETRYABLE_RE.search(text):
        return True
    if _NO_RETRY_RE.search(text):
        return False
    return True


def _log_attempt_error(exc, attempt, total, url):
    """Неудачная попытка -> yt-dlp.log (существующий логгер vdl.ytdlp).

    Для диагностики по логам: сколько было попыток и чем закончилась каждая.
    """
    logger = get_logger("vdl.ytdlp", "yt-dlp.log")
    logger.warning(
        "attempt %d/%d failed (%s): %s",
        attempt, total, url, str(exc).replace("\n", " ")[:500],
    )


def _final_error_message(exc, attempts):
    """Текст финальной ошибки: исходное сообщение + счётчик попыток.

    Одна строка (переводы строк схлопнуты): GUI показывает ошибку в одно-
    строчных подписях. «(после N попыток)» — для диагностики: видно, что
    повтор был и не помог.
    """
    text = " ".join(str(exc).split())
    return f"{text} (после {attempts} попыток)"


def fetch_info(url, playlist=False):
    """Информация о ссылке без скачивания (для превью и валидации)."""
    # Предупреждения yt-dlp (в т.ч. о JS-рантайме) — не подавляются и не
    # попадают в GUI, а пишутся в yt-dlp.log через единый config.get_logger.
    # Ошибки для GUI не меняются: исключения идут прежним путём.
    options = {
        "quiet": True,
        "logger": get_logger("vdl.ytdlp", "yt-dlp.log"),
        "extract_flat": "in_playlist",
        "noplaylist": not playlist,
    }

    # Повтор переходящих ошибок (1.0.4): попытки с паузой RETRY_PAUSE_SECONDS;
    # постоянные ошибки (удалено/приватно/нужен вход) — без повторов.
    last_exc = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
            break
        except Exception as exc:
            if not _is_retryable(exc):
                raise
            last_exc = exc
            _log_attempt_error(exc, attempt, RETRY_ATTEMPTS, url)
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_PAUSE_SECONDS)
    else:
        # все попытки исчерпаны — та же ошибка + счётчик попыток
        raise Exception(
            _final_error_message(last_exc, RETRY_ATTEMPTS)
        ) from last_exc

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
                 playlist=False, time_range=None, precise_cut=False):
        self.id = uuid.uuid4().hex[:12]
        self.url = url
        self.mode = mode
        self.quality = quality
        self.output_dir = output_dir
        self.playlist = playlist
        # 1.0.5 фрагмент: (start, end) в секундах, None — полное видео
        # (поведение прежнее). precise_cut — force_keyframes_at_cuts
        # (точная обрезка с перекодированием).
        self.time_range = time_range
        self.precise_cut = bool(precise_cut)

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

        # 1.0.5: задача брошена сторожем (отмена/зависание фрагмента) —
        # статусы и файлы воркера-зомби игнорируются (см. _run_item)
        self._abandoned = False

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
            playlist=False, time_range=None, precise_cut=False):
        """Добавить задачу. Возвращает DownloadItem.

        time_range=(start, end) — скачивать только фрагмент (секунды);
        None — полное видео. precise_cut — точная обрезка (1.0.5).
        """
        item = DownloadItem(url, mode, quality, output_dir, playlist,
                            time_range, precise_cut)
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
            # Предупреждения yt-dlp — в yt-dlp.log (не подавляются и не
            # попадают в GUI); ошибки для GUI — прежним путём (исключения).
            "logger": get_logger("vdl.ytdlp", "yt-dlp.log"),
            "noprogress": True,
            "continuedl": True,   # докачка .part — основa паузы/возобновления
        }

        # Фрагмент (1.0.5): download_ranges + force_keyframes_at_cuts.
        # Имя файла отличается от полного видео суффиксом [clip Ns-Ms] —
        # фрагмент и полное видео не перезаписывают друг друга (и не
        # вытесняют запись друг друга в истории: add_history заменяет
        # запись по пути). Механизм тот же, что проверен 12.09 в
        # check_sources.py на VK (прямой формат) — см. AGENTS.md.
        if item.time_range:
            start, end = item.time_range
            options["outtmpl"] = os.path.join(
                item.output_dir,
                f"%(title)s {fragment_suffix(start, end)}.%(ext)s",
            )
            options["download_ranges"] = (
                lambda info, ydl, _s=float(start), _e=float(end):
                [{"start_time": _s, "end_time": _e}]
            )
            if item.precise_cut:
                # Точная обрезка: ключевые кадры вставляются в точки реза,
                # но секции перекодируются — заметно медленнее.
                options["force_keyframes_at_cuts"] = True

        # В собранном exe (PyInstaller) ffmpeg лежит рядом с исполняемым
        # файлом; в dev-режиме yt-dlp ищет его в PATH.
        if getattr(sys, "frozen", False):
            ffmpeg_dir = os.path.dirname(sys.executable)
            options["ffmpeg_location"] = ffmpeg_dir
            # Фрагмент (1.0.5): FFmpegFD.available() — выбор downloader'а
            # при download_ranges — читает ContextVar, а НЕ params
            # (известный Fixme yt-dlp: CLI ставит его в parse_options,
            # см. yt_dlp/__init__.py: FFmpegPostProcessor._ffmpeg_location.
            # set(...)). Без него фрагмент https-форматов падает
            # «You have requested downloading the video partially, but
            # ffmpeg is not installed», хотя ffmpeg передан в params.
            # Ставим так же, как CLI; ContextVar живёт в контексте потока,
            # а _build_options вызывается в потоке скачивания.
            from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor
            FFmpegPostProcessor._ffmpeg_location.set(ffmpeg_dir)

        if item.mode == "audio":
            options["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]
        return options

    def _run_attempt(self, item, ydl):
        """Повторяемая часть попытки: ТОЛЬКО extract_info(download=True).

        Установка COMPLETED, item.files и _notify вынесены в _run_item:
        если extract_info прошёл, файл скачан полностью, и падение на
        последующих шагах (prepare_filename, оповещение GUI) не должно
        запускать повторное скачивание.

        1.0.5 фрагмент: FFmpegFD не дергает progress-hooks — статус сам
        бы не ушёл из ANALYZING. Ставим честный DOWNLOADING с нулевым
        прогрессом до extract_info: GUI показывает «Скачивание
        фрагмента…» без процента (процента нет, но статус правдив).
        """
        if item.time_range and item.status != STATUS_DOWNLOADING:
            item.status = STATUS_DOWNLOADING
            item.progress = 0.0
            self._notify(item)
        info = ydl.extract_info(item.url, download=True)

        # Метаданные задачи (для отображения и истории)
        if not item.title:
            item.title = info.get("title") or ""
        if item.duration is None:
            item.duration = info.get("duration")
        if not item.thumbnail:
            item.thumbnail = info.get("thumbnail")
        return info

    def _finish_item(self, item, info, ydl):
        """Финальная фаза после успешного extract_info (не повторяется).

        Здесь исключение — уже не сетевая проблема yt-dlp: файл скачан,
        повтор скачивания не поможет и не нужен.
        """
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

    def _wait_between_attempts(self, item):
        """Пауза между попытками (RETRY_PAUSE_SECONDS), прерываемая
        отменой/паузой пользователя. Возвращает:
            None      — пауза истекла, можно повторять;
            "cancel"  — отмена: прервать задачу (ERROR «Отменено»);
            "pause"   — пауза: задача уходит в PAUSED.
        Отмена и пауза в этот момент должны срабатывать мгновенно, а не
        ждать конца таймаута (AC: отмена прерывает ожидание)."""
        if item._cancel.wait(RETRY_PAUSE_SECONDS):
            if item._cancel_intent:
                return "cancel"
            return "pause"
        return None

    def _run_item(self, item):
        # 1.0.5 фрагмент: сторож — отмена и зависание VK. FFmpegFD не
        # дергает progress-hooks, поэтому cancel-намерение пользователя
        # не пробьётся через _progress_hook; сторож опрашивает задачу
        # из отдельного потока и завершает её сам.
        if item.time_range:
            watch = threading.Thread(
                target=self._fragment_watch, args=(item,),
                daemon=True,
            )
            watch.start()
        try:
            # Повтор переходящих ошибок (1.0.4). Тот же экземпляр YoutubeDL
            # на все попытки: докачка .part и кеш cookies переживают повтор,
            # а частично скачанное не теряется. Постоянные ошибки (удалено/
            # приватно/нужен вход) — ровно одна попытка. Каждая неудачная
            # попытка пишется в yt-dlp.log (_log_attempt_error).
            # extract_info(download=True) и финальная фаза (_finish_item)
            # разделены: успех extract_info = файл скачан; падение после
            # него (prepare_filename, _notify) повторного скачивания
            # не запускает.
            with YoutubeDL(self._build_options(item)) as ydl:
                last_exc = None
                for attempt in range(1, RETRY_ATTEMPTS + 1):
                    try:
                        info = self._run_attempt(item, ydl)
                        break
                    except DownloadCancelled:
                        raise
                    except Exception as exc:
                        if not _is_retryable(exc):
                            raise
                        last_exc = exc
                        _log_attempt_error(exc, attempt, RETRY_ATTEMPTS,
                                           item.url)
                        if attempt < RETRY_ATTEMPTS:
                            how = self._wait_between_attempts(item)
                            if how == "cancel":
                                # Отмена в ожидании: задача завершается как
                                # отменённая (ERROR «Отменено»), а не сетевой
                                # ошибкой (AC: отмена прерывает ожидание).
                                raise DownloadCancelled()
                            if how == "pause":
                                raise DownloadCancelled()
                else:
                    raise Exception(
                        _final_error_message(last_exc, RETRY_ATTEMPTS)
                    ) from last_exc
                # 1.0.5: сторож завершил задачу (отмена) — воркер-зомби
                # не должен перезаписывать её финальный статус
                if item._abandoned:
                    return
                self._finish_item(item, info, ydl)
        except DownloadCancelled:
            # Намерение пользователя (последнее действие побеждает):
            #   пауза → PAUSED (планировщик вернёт в очередь при resume),
            #   отмена → ERROR «Отменено».
            # 1.0.5: если задачу уже завершил сторож (item._abandoned),
            # воркер-зомби молчит — его статус устарел.
            if item._abandoned:
                return
            if item._cancel_intent:
                item.status = STATUS_ERROR
                item.error = "Отменено"
            else:
                item.status = STATUS_PAUSED
            self._notify(item)
        except Exception as exc:
            # 1.0.5: сторож завершил задачу раньше — игнорируем
            # запоздалую ошибку воркера-зомби (ffmpeg может ещё писать
            # файл; файл перезапишется при следующем скачивании)
            if item._abandoned:
                return
            item.status = STATUS_ERROR
            item.error = str(exc)
            self._notify(item)
        finally:
            self._wake.set()

    def _fragment_watch(self, item):
        """Сторож фрагментной задачи (1.0.5). Живёт, пока задача активна.

        FFmpegFD не дергает progress-hooks: без сторожа «Отмена» ждала
        бы конца ffmpeg (VK HLS — бесконечно), а VK-зависание висело
        бы до пользователя. Что делает:
          - отмена: _cancel взведён, а задача в DOWNLOADING -> задача
            завершается сразу (ERROR «Отменено»); воркер-зомби
            игнорируется по item._abandoned;
          - зависание: файлы фрагмента не растут FRAGMENT_STALL_TIMEOUT
            (по умолчанию 10 минут) -> ERROR; для VK — «VK пока не
            поддерживает скачивание фрагмента».
        «Рост файлов»: суммарный размер файлов с суффиксом [clip ...]
        в папке назначения (.part включены): ffmpeg дописывает их по
        ходу скачивания, даже если очень медленно.
        """
        deadline_stall = time.monotonic() + FRAGMENT_STALL_TIMEOUT
        last_size = self._fragment_files_size(item)
        while True:
            # задача закончилась (воркер успел раньше сторожа)
            if item.status not in ACTIVE_STATUSES:
                return
            # отмена пользователя: hooks от FFmpegFD не придут — действуем
            if item._cancel.is_set():
                item._abandoned = True
                self._kill_fragment_ffmpeg(item)
                item.status = STATUS_ERROR
                item.error = "Отменено"
                self._notify(item)
                self._wake.set()
                return
            # пауза не поддерживается при фрагменте (кнопка скрыта в GUI),
            # но защита от «зависшей паузы» всё равно нужна
            if item._pause.is_set():
                item._abandoned = True
                item.status = STATUS_PAUSED
                self._notify(item)
                self._wake.set()
                return
            size = self._fragment_files_size(item)
            if size != last_size:
                last_size = size
                deadline_stall = time.monotonic() + FRAGMENT_STALL_TIMEOUT
            elif time.monotonic() >= deadline_stall:
                _log_attempt_error(
                    Exception("fragment stall: no file growth for %ds"
                              % int(FRAGMENT_STALL_TIMEOUT)),
                    1, 1, item.url,
                )
                item._abandoned = True
                self._kill_fragment_ffmpeg(item)
                item.status = STATUS_ERROR
                item.error = ("VK пока не поддерживает скачивание "
                              "фрагмента" if _is_vk_url(item.url)
                              else "Скачивание фрагмента прервано: нет "
                                   "прогресса 10 минут")
                self._notify(item)
                self._wake.set()
                return
            time.sleep(FRAGMENT_WATCH_POLL)

    @staticmethod
    def _fragment_files_size(item):
        """Суммарный размер файлов фрагмента в папке назначения
        (включая .part) — «прогресс» для сторожа. Ошибки игнорируем:
        значение лишь ориентир (0 -> нет файлов -> считаем ростом от 0).
        """
        try:
            suffix = fragment_suffix(*item.time_range)
            total = 0
            for name in os.listdir(item.output_dir):
                if suffix in name:
                    p = os.path.join(item.output_dir, name)
                    try:
                        total += os.path.getsize(p)
                    except OSError:
                        pass
            return total
        except OSError:
            return 0

    def _kill_fragment_ffmpeg(self, item):
        """Убить ffmpeg-сироту фрагмента (1.0.5/1.0.6).

        Только Windows. Ищем дочерний ffmpeg.exe по трём условиям:
          1. ParentProcessId == PID нашего процесса
          2. В cmdline есть суффикс [clip Ns-Ms] этого item
          3. В cmdline есть normcase(output_dir) этого item
        Убиваем os.kill(pid, 9). Ошибки — в yt-dlp.log, не бросаем.
        time_range=None -> сразу выход (не запрашиваем CIM).
        """
        if os.name != "nt":
            return
        if item.time_range is None:
            return
        try:
            import json
            import subprocess
            suffix = fragment_suffix(*item.time_range)
            ps = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process "
                 "-Filter "
                 "\"Name='ffmpeg.exe'\" | "
                 "Select-Object ProcessId,ParentProcessId,CommandLine | "
                 "ConvertTo-Json"],
                capture_output=True, text=True, timeout=30,
            )
            if ps.returncode != 0:
                return
            rows = json.loads(ps.stdout) if ps.stdout.strip() else []
            if isinstance(rows, dict):
                rows = [rows]
        except Exception:
            return
        pid = os.getpid()
        norm_out = os.path.normcase(item.output_dir)
        targets = []
        for r in rows:
            pp = r.get("ParentProcessId")
            if pp != pid:
                continue
            # normcase обеих сторон: реальный cmdline содержит путь в
            # регистре пользователя (C:/Videos), а normcase — нижний
            # регистр + backslash (c:\videos)
            cl = os.path.normcase(r.get("CommandLine") or "")
            if suffix not in cl:
                continue
            if norm_out not in cl:
                continue
            targets.append(r["ProcessId"])
        for tpid in targets:
            try:
                os.kill(tpid, 9)
            except Exception:
                _log_attempt_error(
                    Exception("kill(%d) failed" % tpid),
                    1, 1, item.url,
                )
