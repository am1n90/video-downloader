"""UI-слой: PySide6 + qfluentwidgets (Fluent Design).

Страницы: Загрузка / Очередь / Библиотека / Настройки.
Логика загрузок (downloader.py) — стабильное API, вызывается отсюда.

Точки интеграции с менеджером:
    Bridge (QObject) принимает колбэки из фоновых потоков и через
    сигналы Qt (auto-connection = queued) безопасно передаёт их в GUI.
"""

import os
import re
import subprocess
import sys
import threading
import urllib.request

from PySide6.QtCore import (
    QObject,
    QRect,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    EditableComboBox,
    IconWidget,
    IndeterminateProgressRing,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBoxBase,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    ScrollArea,
    SearchLineEdit,
    SegmentedWidget,
    SettingCard,
    SettingCardGroup,
    SpinBox,
    StrongBodyLabel,
    SubtitleLabel,
    Theme,
    TitleLabel,
    FluentWindow,
    NavigationItemPosition,
    FluentIcon as FIF,
    setTheme,
)

import config
import single_instance
import updater
from downloader import (
    DownloadManager,
    STATUS_ANALYZING,
    STATUS_COMPLETED,
    STATUS_DOWNLOADING,
    STATUS_ERROR,
    STATUS_PAUSED,
    STATUS_PROCESSING,
    STATUS_QUEUED,
    _is_vk_url,
    fetch_info,
    fmt_eta,
    fmt_speed,
    fragment_suffix,
)

try:
    from qfluentwidgets import ImageLabel
except ImportError:
    ImageLabel = QLabel

APP_NAME = "Video Downloader"

MODE_LABELS = {"video": "Видео (MP4)", "audio": "Аудио (MP3)"}
QUALITY_LABEL = "Лучшее"

# Подписи высот: 4320 → 8K, 2160 → 4K, прочие → Np
def quality_text(height):
    if height == 4320:
        return "8K"
    if height == 2160:
        return "4K"
    return f"{height}p"


def fmt_mb(bytes_value):
    if not bytes_value:
        return ""
    mb = bytes_value / 1024 / 1024
    if mb >= 1024:
        return f"{mb / 1024:.1f} ГБ"
    return f"{mb:.0f} МБ"

SP_WINDOW = 24   # 24px паддинг окна (6 × 4px сетка)
SP_BLOCK = 16    # 16px между блоками
SP_GROUP = 8     # 8px внутри групп


def fmt_duration(seconds):
    if not seconds:
        return ""
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def parse_timecode(text):
    r"""«м:сс» / «ч:мм:сс» / «90» (голые секунды) -> секунды, иначе None.

    Ведущие нули допустимы («0:35», «02:10», «1:00:00») — fmt_timecode
    пишет именно так, поля должны принимать свой же вывод. Для полей
    точного ввода фрагмента (1.0.5).

    Разделитель между группами цифр — любой символ, не только «:»
    («1 22» -> 1:22, «1-22-54» -> 1:22:54): группы цифр находятся через
    re.split(r"\D+", ...), сам символ-разделитель не проверяется (1.0.6).
    Пустая группа (разделитель в начале/конце — «:35», «-5», «abc») —
    невалидный ввод.
    """
    text = (text or "").strip()
    if not text:
        return None
    parts = re.split(r"\D+", text)
    if len(parts) > 3 or any(p == "" for p in parts):
        return None
    for p in parts:
        if not p.isdigit():
            return None
    nums = [int(p) for p in parts]
    if len(nums) == 1:
        return nums[0]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    return nums[0] * 3600 + nums[1] * 60 + nums[2]


def fmt_timecode(seconds):
    """Секунды -> «м:сс» или «ч:м:сс» без ведущих нулей минут (0:35, 2:10)."""
    seconds = max(0, int(round(seconds)))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def ru_records(n):
    """Форма слова для счётчика: 1 запись / 2 записи / 5 записей."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} запись"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return f"{n} записи"
    return f"{n} записей"


# ================= МОСТ: фоновые потоки → GUI =================

class Bridge(QObject):
    """Передаёт события из потоков Python в GUI через сигналы Qt.

    Анализ ссылок идёт через AnalyzeWorker.done/failed (прямые сигналы
    воркера), поэтому analyzeDone/analyzeFailed здесь не нужны.
    """

    itemChanged = Signal(object)
    queueChanged = Signal()


# ================= ПОЛЗУНОК ФРАГМЕНТА (1.0.5) =================


MIN_FRAGMENT_SECONDS = 1   # AC2: минимальный фрагмент


class RangeSlider(QWidget):
    """Ползунок с двумя ручками: начало/конец фрагмента (целые секунды).

    Инвариант: 0 <= start < end <= duration, длительность >= 1 с
    (клэмпы в _set_handle). Сигналы: rangeChanged(start, end) — по
    завершении перемещения (отпускание мыши / стрелка клавиатуры);
    fieldsMoved() — на каждом тике drag'а (поля точного ввода
    обновляются мгновенно, над ручками — подписи времени);
    resized() — после изменения размера (превью кадра над ручкой
    переставляется вслед за ней).
    Тесты: set_values() НЕ эмитит сигналы — программная установка из
    полей ввода не должна зацикливать связь слайдер <-> поля.
    """

    HANDLE_SIZE = 16
    TRACK_MARGIN = 12      # место под ручку у края

    rangeChanged = Signal(int, int)
    fieldsMoved = Signal()
    resized = Signal()

    def __init__(self, duration=0, parent=None):
        super().__init__(parent)
        self.duration = max(0, int(duration))
        self.start = 0
        self.end = self.duration
        self._active = None            # "start"/"end" при drag
        self._drag_offset = 0
        self.setMinimumHeight(36)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)

    # ---------- публичное API ----------

    def set_duration(self, duration):
        """Новая длина видео: сброс ручек на 0..duration."""
        self.duration = max(0, int(duration))
        self.start = 0
        self.end = self.duration
        self.update()

    def set_values(self, start, end):
        """Программная установка значений (из полей) — без сигналов."""
        self.start = int(start)
        self.end = int(end)
        self.update()

    def values(self):
        return self.start, self.end

    def active_handle(self):
        """«start»/«end» — какую ручку сейчас тянут (drag), иначе None."""
        return self._active

    def handle_x(self, which):
        """x центра ручки «start»/«end» в координатах слайдера."""
        return self._time_to_x(self.start if which == "start" else self.end)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()

    # ---------- геометрия и события ----------

    def _x_to_time(self, x):
        left = self.TRACK_MARGIN + self.HANDLE_SIZE // 2
        right = self.width() - left
        if right <= left:
            return 0
        t = (x - left) * self.duration / (right - left)
        return max(0, min(self.duration, int(t)))

    def _time_to_x(self, t):
        left = self.TRACK_MARGIN + self.HANDLE_SIZE // 2
        right = self.width() - left
        if self.duration <= 0:
            return left
        return left + (right - left) * t / self.duration

    def _handle_rect(self, which):
        t = self.start if which == "start" else self.end
        x = self._time_to_x(t)
        r = self.HANDLE_SIZE // 2
        return QRect(int(x) - r, (self.height() - self.HANDLE_SIZE) // 2,
                     self.HANDLE_SIZE, self.HANDLE_SIZE)

    def _nearest_handle(self, x):
        """Ручка под курсором («start» при равной удалённости)."""
        if abs(x - self._time_to_x(self.start)) <= self.HANDLE_SIZE / 2 + 2:
            return "start"
        if abs(x - self._time_to_x(self.end)) <= self.HANDLE_SIZE / 2 + 2:
            return "end"
        return None

    def mousePressEvent(self, event):
        if self.duration <= 0:
            return
        which = self._nearest_handle(event.position().x())
        if which:
            self._active = which
            self._drag_offset = int(event.position().x() - self._time_to_x(
                self.start if which == "start" else self.end))
            self.fieldsMoved.emit()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._active is None:
            self.setCursor(
                Qt.SizeHorCursor if self._nearest_handle(event.position().x())
                else Qt.ArrowCursor
            )
            return
        t = self._x_to_time(int(event.position().x()) - self._drag_offset)
        self._set_handle(self._active, t, emit=True)

    def mouseReleaseEvent(self, event):
        if self._active is not None:
            self._active = None
            self.rangeChanged.emit(self.start, self.end)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        # Стрелки двигают ближайшую к центру ручку (клавиатурная
        # доступность); += 1 секунда с соблюдением инварианта.
        if self.duration <= 0:
            super().keyPressEvent(event)
            return
        cx = self.width() / 2
        step = 1
        if event.key() not in (Qt.Key_Left, Qt.Key_Right):
            super().keyPressEvent(event)
            return
        if self._time_to_x(self.start) > cx or (
                self._time_to_x(self.start) == cx
                and event.key() == Qt.Key_Left):
            which, delta = "start", (-step if event.key() == Qt.Key_Left
                                     else step)
        else:
            which, delta = "end", (-step if event.key() == Qt.Key_Left
                                   else step)
        self._set_handle(which, (self.start if which == "start" else self.end)
                         + delta, emit=False)
        self.rangeChanged.emit(self.start, self.end)

    def _set_handle(self, which, t, emit=False):
        """Установить ручку с клэмпами: конец не раньше начала + 1 с,
        границы [0..duration]; вторая ручка не сдвигается (значение
        просто ограничивается инвариантом)."""
        t = max(0, min(self.duration, int(t)))
        if which == "start":
            self.start = max(0, min(t, self.end - MIN_FRAGMENT_SECONDS))
        else:
            self.end = min(self.duration,
                           max(t, self.start + MIN_FRAGMENT_SECONDS))
        if emit:
            self.fieldsMoved.emit()
        self.update()

    # ---------- отрисовка ----------

    def paintEvent(self, event):
        from PySide6.QtGui import (
            QColor,
            QLinearGradient,
            QPainter,
            QPen,
        )

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        track_y = self.height() // 2
        track_left = self.TRACK_MARGIN
        track_right = self.width() - self.TRACK_MARGIN

        # дорожка (цвет адаптируется к теме: тёмная/светлая)
        track_color = QColor(255, 255, 255, 60) if self._is_dark() else \
            QColor(0, 0, 0, 40)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(
            QRect(track_left, track_y - 3, track_right - track_left, 6), 3, 3
        )

        # выделенный фрагмент (start..end)
        if self.duration > 0:
            x1 = self._time_to_x(self.start)
            x2 = self._time_to_x(self.end)
            grad = QLinearGradient(x1, 0, x2, 0)
            grad.setColorAt(0, QColor("#4cc2ff"))
            grad.setColorAt(1, QColor("#2b88d8"))
            painter.setBrush(grad)
            painter.drawRoundedRect(
                QRect(int(x1), track_y - 3, max(1, int(x2 - x1)), 6), 3, 3
            )

        # ручки: синий круг с белой окантовкой
        for which in ("start", "end"):
            rect = self._handle_rect(which)
            painter.setBrush(QColor("#0078d4"))
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.drawEllipse(rect)

        painter.end()

    def _is_dark(self):
        """Тёмная ли тема сейчас (для цвета дорожки)."""
        from qfluentwidgets import isDarkTheme
        return isDarkTheme()


class AnalyzeWorker(QThread):
    """Анализ ссылки в фоне."""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, url, playlist, parent=None):
        super().__init__(parent)
        self.url = url
        self.playlist = playlist

    def run(self):
        try:
            info = fetch_info(self.url, playlist=self.playlist)
            self.done.emit(info)
        except Exception as exc:
            self.failed.emit(str(exc))


class ThumbWorker(QThread):
    """Загрузка миниатюры в фоне."""

    loaded = Signal(QImage)

    def __init__(self, url, size, parent=None):
        super().__init__(parent)
        self.url = url
        self.size = size

    def run(self):
        image = QImage()
        if self.url:
            try:
                data = urllib.request.urlopen(self.url, timeout=10).read()
                image = QImage()
                image.loadFromData(data)
                if not image.isNull():
                    image = image.scaled(
                        self.size[0], self.size[1],
                        Qt.KeepAspectRatio, Qt.SmoothTransformation,
                    )
            except Exception:
                image = QImage()
        self.loaded.emit(image)


def _ffmpeg_path():
    """ffmpeg: рядом с exe в собранной версии (PyInstaller), иначе PATH
    (dev-режим — тот же принцип, что и у downloader.py при sys.frozen)."""
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "ffmpeg.exe")
    return "ffmpeg"


class FramePreviewWorker(QThread):
    """Кадр видео на заданной секунде через ffmpeg — превью при
    перетаскивании ручки слайдера фрагмента (1.0.6).

    Тянет кадр напрямую из прямого URL формата (preview_format из
    fetch_info, см. downloader._pick_preview_format), без скачивания
    видео целиком. Сеть/декод — в фоне; при любой ошибке (нет ffmpeg,
    таймаут, площадка не даёт прямой URL) просто emit'ит пустой QImage —
    превью тихо не показывается, интерфейс не блокируется и не падает.

    ffmpeg запускается через Popen (не run) специально ради stop():
    на VK-формате (HLS) кадр иногда тянется ~17с — если пользователь
    закрыл окно или сменил видео раньше, run() должен
    освободить поток НЕМЕДЛЕННО, а не ждать таймаут до конца. Без
    этого процесс Python не завершался бы, пока ffmpeg сам не
    досчитает (проверено: закрытие окна во время VK-превью держало
    процесс живым ~14с вместо мгновенного выхода). Во время drag
    ffmpeg НЕ убивается (см. DownloadPage._request_frame_preview).
    """

    # (воркер, кадр): по воркеру страница отличает актуальный результат
    # от устаревшего (сброс фрагмента / смена видео)
    loaded = Signal(object, QImage)

    def __init__(self, url, headers, second, handle=None, parent=None):
        super().__init__(parent)
        self.url = url
        self.headers = headers or {}
        self.second = max(0, int(second))
        self.handle = handle       # «start»/«end» — чей это кадр
        self._proc = None
        self._stopped = False

    def stop(self):
        """Прервать досрочно: устаревший запрос (новее уже в пути) или
        закрытие окна. Убивает ffmpeg, если он ещё жив — run() после
        этого разблокируется сразу же на communicate()."""
        self._stopped = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    def run(self):
        image = QImage()
        try:
            cmd = [_ffmpeg_path(), "-y", "-ss", str(self.second)]
            if self.headers:
                cmd += ["-headers",
                        "".join(f"{k}: {v}\r\n" for k, v in self.headers.items())]
            cmd += [
                "-i", self.url,
                "-frames:v", "1", "-f", "image2", "-vcodec", "png",
                "pipe:1",
            ]
            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                **kwargs,
            )
            try:
                stdout, _ = self._proc.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                stdout, _ = self._proc.communicate()
            if self._proc.returncode == 0 and stdout and not self._stopped:
                image.loadFromData(stdout)
        except Exception:
            image = QImage()
        self.loaded.emit(self, image)


class UpdateWorker(QThread):
    """Проверка/скачивание обновления в фоне (по образцу AnalyzeWorker).

    mode='check': fetch_manifest + is_newer.
    mode='download': download_file с прогрессом и отменой + sha256.
    """

    manifestReady = Signal(object)   # manifest / None (нет обновлений)
    manifestError = Signal(str)      # не удалось проверить (сеть/формат)
    downloadProgress = Signal(float, int, int)
    downloadDone = Signal(str)        # путь
    downloadFailed = Signal(str)

    def __init__(self, mode, manifest_url=None, manifest=None,
                 dest=None, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.manifest_url = manifest_url
        self.manifest = manifest
        self.dest = dest
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    def run(self):
        try:
            if self.mode == "check":
                manifest = updater.fetch_manifest(self.manifest_url)
                if manifest and updater.is_newer(
                    manifest.get("version", ""), config.APP_VERSION
                ):
                    self.manifestReady.emit(manifest)
                else:
                    self.manifestReady.emit(None)

            elif self.mode == "download":
                url = self.manifest.get("url")
                dest = self.dest

                def on_progress(percent, downloaded, total):
                    self.downloadProgress.emit(percent, downloaded, total)

                updater.download_file(
                    url, dest, progress_cb=on_progress,
                    cancel_event=self.cancel_event,
                )
                if not updater.verify_file(
                    dest, self.manifest.get("sha256", "")
                ):
                    raise RuntimeError(
                        "Контрольная сумма не совпала — файл повреждён"
                    )
                self.downloadDone.emit(dest)
        except updater.DownloadCancelled:
            pass  # отмена: тихо, без ошибок
        except updater.ManifestError as exc:
            # Отличаем «проверить не вышло» от «обновлений нет» (BUG-10)
            if self.mode == "check":
                self.manifestError.emit(str(exc))
            else:
                self.downloadFailed.emit(str(exc))
        except Exception as exc:
            if self.mode == "download":
                self.downloadFailed.emit(str(exc))
            else:
                self.manifestError.emit(str(exc))


# ================= СТРАНИЦА: ЗАГРУЗКА =================

class DownloadPage(QWidget):
    def __init__(self, bridge, manager, settings, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.manager = manager
        self.settings = settings
        self.preview_info = None
        self._analyze_worker = None
        self._thumb_worker = None
        self.current_cards = {}

        self._build_ui()

        bridge.itemChanged.connect(self._on_current_item)
        bridge.queueChanged.connect(self._rebuild_current)

    # ---------- UI ----------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(SP_WINDOW, SP_WINDOW, SP_WINDOW, SP_WINDOW)
        root.setSpacing(SP_BLOCK)

        # Заголовок страницы (28pt)
        root.addWidget(TitleLabel("Загрузка"))

        # Блок URL
        url_card = CardWidget(self)
        url_layout = QVBoxLayout(url_card)
        url_layout.setContentsMargins(SP_GROUP * 2, SP_GROUP * 2,
                                     SP_GROUP * 2, SP_GROUP * 2)
        url_layout.setSpacing(SP_GROUP)

        root_url_row = QHBoxLayout()
        root_url_row.setSpacing(SP_GROUP)
        self.url_edit = LineEdit(url_card)
        self.url_edit.setPlaceholderText(
            "Вставьте ссылку: YouTube, VK, Instagram, TikTok…"
        )
        self.url_edit.setClearButtonEnabled(True)
        self.url_edit.textChanged.connect(self._on_url_changed)
        root_url_row.addWidget(self.url_edit, stretch=1)

        paste_btn = PushButton("Вставить", url_card)
        paste_btn.setIcon(FIF.PASTE)
        paste_btn.clicked.connect(self._paste_url)
        root_url_row.addWidget(paste_btn)

        self.analyze_btn = PrimaryPushButton("Анализ", url_card)
        self.analyze_btn.setIcon(FIF.SEARCH)
        self.analyze_btn.clicked.connect(self._analyze)
        root_url_row.addWidget(self.analyze_btn)
        url_layout.addLayout(root_url_row)

        # Статус анализа (empty/analyzing/error)
        self.status_row = QHBoxLayout()
        self.status_row.setSpacing(SP_GROUP)
        self.status_icon = QLabel(url_card)
        self.status_icon.setFixedWidth(20)
        self.status_icon.hide()
        self.status_label = BodyLabel("", url_card)
        self.status_label.hide()
        self.status_row.addWidget(self.status_icon)
        self.status_row.addWidget(self.status_label)
        self.status_row.addStretch()
        url_layout.addLayout(self.status_row)

        # Прогресс анализа
        self.analyze_ring = IndeterminateProgressRing(url_card)
        self.analyze_ring.setFixedSize(24, 24)
        self.analyze_ring.hide()
        self.status_row.insertWidget(0, self.analyze_ring)

        root.addWidget(url_card)

        # Пустое состояние
        self.empty_card = self._make_empty_card("Ссылка не добавлена",
                                                "Вставьте ссылку выше и нажмите «Анализ»")
        root.addWidget(self.empty_card, stretch=1)

        # Карточка превью (анализ успешен)
        self.preview_card = CardWidget(self)
        pv = QVBoxLayout(self.preview_card)
        pv.setContentsMargins(SP_GROUP * 2, SP_GROUP * 2,
                             SP_GROUP * 2, SP_GROUP * 2)
        pv.setSpacing(SP_GROUP)

        head = QHBoxLayout()
        head.setSpacing(SP_GROUP * 2)
        self.thumb_label = ImageLabel(self.preview_card)
        self.thumb_label.setFixedSize(168, 96)
        self.thumb_label.scaledToHeight(96)
        head.addWidget(self.thumb_label)

        pv_text = QVBoxLayout()
        pv_text.setSpacing(SP_GROUP)
        self.preview_title = StrongBodyLabel("", self.preview_card)
        self.preview_title.setWordWrap(True)
        pv_text.addWidget(self.preview_title)

        self.preview_meta = BodyLabel("", self.preview_card)
        self.preview_meta.setWordWrap(True)
        pv_text.addWidget(self.preview_meta)
        pv_text.addStretch()
        head.addLayout(pv_text, stretch=1)
        pv.addLayout(head)

        download_row = QHBoxLayout()
        download_row.setSpacing(SP_GROUP)
        self.download_btn = PrimaryPushButton("Загрузить", self.preview_card)
        self.download_btn.setIcon(FIF.DOWNLOAD)
        self.download_btn.clicked.connect(self._start_download)
        download_row.addWidget(self.download_btn)
        download_row.addStretch()
        pv.addLayout(download_row)

        self.preview_card.hide()
        root.addWidget(self.preview_card)

        # Панель формата
        self.format_card = self._build_format_card()
        self.format_card.hide()
        root.addWidget(self.format_card)

        # Текущие загрузки (живая секция)
        self.current_header = StrongBodyLabel("Загрузки", self)
        root.addWidget(self.current_header)

        self.current_container = QWidget(self)
        self.current_vbox = QVBoxLayout(self.current_container)
        self.current_vbox.setContentsMargins(0, 0, 0, 0)
        self.current_vbox.setSpacing(SP_GROUP)
        root.addWidget(self.current_container)

        self._rebuild_current()

        root.addStretch(1)

    def _make_empty_card(self, title, subtitle):
        card = CardWidget(self)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(SP_GROUP * 2, 32, SP_GROUP * 2, 32)
        lay.setSpacing(SP_GROUP)
        lay.addStretch(1)
        icon = IconWidget(FIF.VIDEO, card)
        icon.setFixedSize(48, 48)
        icon_row = QHBoxLayout()
        icon_row.addStretch()
        icon_row.addWidget(icon)
        icon_row.addStretch()
        lay.addLayout(icon_row)
        t = StrongBodyLabel(title, card)
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        s = BodyLabel(subtitle, card)
        s.setAlignment(Qt.AlignCenter)
        lay.addWidget(s)
        lay.addStretch(1)
        return card

    def _build_format_card(self):
        card = CardWidget(self)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(SP_GROUP * 2, SP_GROUP * 2,
                               SP_GROUP * 2, SP_GROUP * 2)
        lay.setSpacing(SP_GROUP)

        row = QHBoxLayout()
        row.setSpacing(SP_GROUP * 2)

        # Формат
        mode_box = QVBoxLayout()
        mode_box.setSpacing(4)
        mode_box.addWidget(BodyLabel("Формат"))
        self.mode_combo = ComboBox(card)
        self.mode_combo.addItems(list(MODE_LABELS.values()))
        self.mode_combo.setCurrentIndex(
            list(MODE_LABELS.keys()).index(self.settings["default_mode"])
        )
        self.mode_combo.currentIndexChanged.connect(self._render_quality)
        mode_box.addWidget(self.mode_combo)
        row.addLayout(mode_box)

        # Качество
        q_box = QVBoxLayout()
        q_box.setSpacing(4)
        q_box.addWidget(BodyLabel("Качество"))
        self.quality_combo = ComboBox(card)
        q_box.addWidget(self.quality_combo)
        row.addLayout(q_box)

        # Папка
        dir_box = QVBoxLayout()
        dir_box.setSpacing(4)
        dir_box.addWidget(BodyLabel("Папка"))
        dir_row = QHBoxLayout()
        dir_row.setSpacing(SP_GROUP)
        self.dir_edit = EditableComboBox(card)
        self.dir_edit.addItems([self.settings["default_folder"]])
        self.dir_edit.setCurrentText(self.settings["default_folder"])
        self.dir_edit.setMinimumWidth(240)
        dir_row.addWidget(self.dir_edit, stretch=1)
        browse_btn = PushButton("Обзор…", card)
        browse_btn.setIcon(FIF.FOLDER)
        browse_btn.clicked.connect(self._choose_dir)
        dir_row.addWidget(browse_btn)
        dir_box.addLayout(dir_row)
        row.addLayout(dir_box, stretch=2)

        lay.addLayout(row)

        self.playlist_check = CheckBox("Загрузить весь плейлист", card)
        self.playlist_check.stateChanged.connect(lambda: self._analyze_if_ready())
        lay.addWidget(self.playlist_check)

        # ---- Фрагмент (1.0.5) ----
        self.fragment_check = CheckBox("Скачать только фрагмент", card)
        self.fragment_check.setChecked(False)   # по умолчанию выкл (AC1)
        self.fragment_check.stateChanged.connect(self._on_fragment_check)
        lay.addWidget(self.fragment_check)

        # Блок управления фрагментом: слайдер + подписи + поля + точная
        # обрезка. Скрыт, пока галочка выключена; при выключенной галочке
        # поведение и опции — прежние (AC1).
        self.fragment_box = QWidget(card)
        fb = QVBoxLayout(self.fragment_box)
        fb.setContentsMargins(0, SP_GROUP, 0, 0)
        fb.setSpacing(SP_GROUP)

        # Поля времени над ручками (оба редактируемые, мм:сс / ч:мм:сс,
        # связаны со слайдером в обе стороны) + длительность фрагмента
        # посередине. Раньше здесь были label-подписи (только для чтения)
        # и отдельный ряд с полями ввода ниже слайдера — задваивали одно
        # и то же и накладывались друг на друга при перерисовке; теперь
        # ряд один, поля точного ввода и подписи над ручками — одно и то
        # же (1.0.6).
        lab_row = QHBoxLayout()
        self.frag_start_edit = LineEdit(self.fragment_box)
        self.frag_start_edit.setPlaceholderText("мм:сс")
        self.frag_start_edit.setFixedWidth(90)
        self.frag_start_edit.editingFinished.connect(self._on_fields_done)
        self.frag_range_label = BodyLabel("", self.fragment_box)
        self.frag_range_label.setAlignment(Qt.AlignCenter)
        self.frag_end_edit = LineEdit(self.fragment_box)
        self.frag_end_edit.setPlaceholderText("мм:сс")
        self.frag_end_edit.setFixedWidth(90)
        self.frag_end_edit.editingFinished.connect(self._on_fields_done)
        lab_row.addWidget(self.frag_start_edit)
        lab_row.addStretch()
        lab_row.addWidget(self.frag_range_label, stretch=1)
        lab_row.addStretch()
        lab_row.addWidget(self.frag_end_edit)
        fb.addLayout(lab_row)

        # Превью кадра над ручкой, которую тянут: полоса фиксированной
        # высоты прямо над слайдером, кадр ставится в ней по x ручки.
        # Высота зарезервирована заранее — появление кадра не сдвигает
        # слайдер, а оверлей закрыл бы поля времени (кадр остаётся на
        # экране и после отпускания). Полоса скрыта, если площадка не
        # дала прямой формат для превью (см. _init_fragment_ui).
        self.frag_preview_strip = QWidget(self.fragment_box)
        self.frag_preview_strip.setFixedHeight(90)
        self.frag_preview_label = QLabel(self.frag_preview_strip)
        self.frag_preview_label.setFixedSize(160, 90)
        self.frag_preview_label.setAlignment(Qt.AlignCenter)
        self.frag_preview_label.setStyleSheet(
            "background-color: rgba(0, 0, 0, 40); border-radius: 4px;"
        )
        self.frag_preview_label.hide()
        fb.addWidget(self.frag_preview_strip)

        self.range_slider = RangeSlider(0, self.fragment_box)
        self.range_slider.rangeChanged.connect(self._on_slider_changed)
        self.range_slider.fieldsMoved.connect(self._sync_fields_from_slider)
        self.range_slider.fieldsMoved.connect(self._on_slider_dragging)
        self.range_slider.resized.connect(self._place_frame_preview)
        fb.addWidget(self.range_slider)

        # ffmpeg тянет кадр в фоне (см. FramePreviewWorker) с throttle:
        # не больше одного процесса за раз и не чаще раза в 250 мс.
        self._preview_worker = None
        self._preview_handle = None      # ручка, чей кадр показываем
        self._preview_requested = None   # (ручка, секунда) последнего запуска
        self._preview_throttle = QTimer(self)
        self._preview_throttle.setSingleShot(True)
        self._preview_throttle.setInterval(250)
        self._preview_throttle.timeout.connect(self._request_frame_preview)

        # Точная обрезка (медленнее)
        self.precise_check = CheckBox("Точная обрезка (медленнее)", card)
        self.precise_check.setChecked(False)
        self.precise_check.setToolTip(
            "Вкл: начало ровно в заданной секунде, но видео "
            "перекодируется — это долго.\n"
            "Выкл: быстро, но начало может сдвинуться на несколько "
            "секунд к ближайшему ключевому кадру."
        )
        fb.addWidget(self.precise_check)

        self.fragment_box.hide()
        lay.addWidget(self.fragment_box)

        return card

    # ---------- действия ----------

    def _on_url_changed(self):
        if self.url_edit.text().strip():
            self.empty_card.hide()
        else:
            self._reset_preview()

    def _paste_url(self):
        text = QGuiApplication.clipboard().text()
        if text:
            self.url_edit.setText(text.strip())
            self._analyze()

    def _reset_preview(self):
        self.preview_info = None
        self.preview_card.hide()
        self.format_card.hide()
        self.empty_card.show()
        self._set_status("", "")
        self._reset_fragment_state()

    def _reset_fragment_state(self):
        """Сброс UI фрагмента (новая ссылка / сброс превью): галочка
        выключена, блок скрыт, значения — 0..0 (до следующего анализа)."""
        self.fragment_check.setChecked(False)
        self.fragment_check.setVisible(False)
        self.fragment_box.hide()
        self.range_slider.set_duration(0)
        self._frag_inited_duration = None
        self._preview_throttle.stop()
        self.frag_preview_label.hide()
        self._stop_preview_worker()
        self._preview_handle = None
        self._preview_requested = None

    def _stop_preview_worker(self):
        """Убить фоновый ffmpeg превью (сброс фрагмента / смена видео) и
        забыть воркер: его поздний loaded отбросится проверкой
        worker is self._preview_worker. Во время drag не зовётся — там
        устаревший ffmpeg дорабатывает (см. _request_frame_preview)."""
        worker = self._preview_worker
        self._preview_worker = None
        if worker is not None and worker.isRunning():
            worker.stop()

    def _set_status(self, text, kind="info"):
        """kind: info / error / ok; '' — скрыть."""
        if not text:
            self.status_label.hide()
            self.status_icon.hide()
            self.analyze_ring.hide()
            return
        self.status_label.setText(text)
        self.status_label.show()
        if kind == "error":
            self.analyze_ring.hide()
            self.status_icon.setText("✕")
            self.status_icon.show()
        elif kind == "analyzing":
            self.status_icon.hide()
            self.analyze_ring.show()
        else:
            self.analyze_ring.hide()
            self.status_icon.setText("✓")
            self.status_icon.show()

    def _analyze_if_ready(self):
        if self.url_edit.text().strip():
            self._analyze()

    def _analyze(self):
        url = self.url_edit.text().strip()
        if not url:
            self._set_status("Вставьте ссылку", "error")
            return
        # Guard: не запускаем второй анализ, пока первый не завершился
        # (раньше «Вставить» в момент работы воркера плодила потоки).
        if (self._analyze_worker is not None
                and self._analyze_worker.isRunning()):
            return

        self.analyze_btn.setEnabled(False)
        self.download_btn.setEnabled(False)
        self._set_status("Анализ ссылки…", "analyzing")

        self._analyze_worker = AnalyzeWorker(
            url, self.playlist_check.isChecked(), self
        )
        self._analyze_worker.done.connect(self._on_analyze_ok)
        self._analyze_worker.failed.connect(self._on_analyze_fail)
        self._analyze_worker.start()

    def _on_analyze_ok(self, info):
        self.analyze_btn.setEnabled(True)
        self.preview_info = info

        self.empty_card.hide()
        self.preview_card.show()
        self.format_card.show()

        self.preview_title.setText(info["title"])
        meta_parts = [
            p for p in (info["uploader"], fmt_duration(info["duration"])) if p
        ]
        if info["is_playlist"]:
            meta_parts.append(f"{len(info['entries'])} видео")
        self.preview_meta.setText("  •  ".join(meta_parts))

        self._set_status("Ссылка поддерживается", "ok")
        self._render_quality()
        # Фрагмент (1.0.5): галочка доступна только для одиночного видео
        # с известной длительностью; при плейлисте — скрыта (допущение).
        self._reset_fragment_state()
        self.fragment_check.setVisible(self._fragment_available())
        self.download_btn.setEnabled(True)

        self._load_thumb(info["thumbnail"])

    def _on_analyze_fail(self, error):
        self.analyze_btn.setEnabled(True)
        self._set_status(f"Не удалось: {error}", "error")

    def _load_thumb(self, url):
        self._thumb_worker = ThumbWorker(url, (168, 96), self)
        self._thumb_worker.loaded.connect(self._on_thumb)
        self._thumb_worker.start()

    def _on_thumb(self, image):
        if not image.isNull():
            self.thumb_label.setImage(image)
        else:
            self.thumb_label.setImage(QImage())

    def _render_quality(self):
        if not self.preview_info:
            return
        if self.mode_combo.currentText() == MODE_LABELS["audio"]:
            self.quality_combo.clear()
            self.quality_combo.addItem("—")
            self.quality_combo.setCurrentIndex(0)
            self.quality_combo.setEnabled(False)
            return
        heights = self.preview_info.get("video_qualities") or []
        sizes = self.preview_info.get("quality_sizes") or {}
        if not heights:
            self.quality_combo.clear()
            self.quality_combo.addItem(QUALITY_LABEL)
            self.quality_combo.setCurrentIndex(0)
            self.quality_combo.setEnabled(False)
            return
        self.quality_combo.setEnabled(True)
        self.quality_combo.clear()
        # «Лучшее» — высшее доступное качество с его размером
        best_height = heights[0]
        best_size = sizes.get(best_height)
        best_text = QUALITY_LABEL
        if best_size:
            best_text = f"{QUALITY_LABEL} ({quality_text(best_height)}) • {fmt_mb(best_size)}"
        self.quality_combo.addItem(best_text)
        for h in heights:
            text = quality_text(h)
            if h in sizes:
                text = f"{text} • {fmt_mb(sizes[h])}"
            self.quality_combo.addItem(text)
        self.quality_combo.setCurrentIndex(0)

    # ---------- фрагмент (1.0.5) ----------

    def _on_fragment_check(self):
        """Галочка «Скачать только фрагмент»: показать/скрыть блок."""
        show = self.fragment_check.isChecked()
        # фрагмент доступен только для одиночного видео с длительностью
        if show and self._fragment_available():
            self.fragment_box.show()
            self._init_fragment_ui()
        else:
            self.fragment_box.hide()
            if show and not self._fragment_available():
                # недоступно (плейлист/нет длительности): сбросить, чтобы
                # не отправить в очередь невалидный диапазон
                self.fragment_check.setChecked(False)

    def _fragment_available(self):
        """Фрагмент возможен: одиночное видео с известной длительностью
        (плейлисты не поддерживаем — допущение из плана)."""
        info = self.preview_info or {}
        duration = info.get("duration") or 0
        return bool(info) and not info.get("is_playlist") and duration >= 2

    def _init_fragment_ui(self):
        """Инициализация блока фрагмента после анализа (или повторная).

        При повторном показе (галочку сняли и вернули) выбор сохраняется;
        сброс — только при смене видео (другая длительность).
        """
        duration = int(self.preview_info.get("duration") or 0)
        if getattr(self, "_frag_inited_duration", None) != duration:
            self._frag_inited_duration = duration
            self.range_slider.set_duration(duration)
            # Дефолт: 0 .. duration (всё видео)
            self._sync_fields_from_slider()
        self.frag_preview_strip.setVisible(self._preview_format() is not None)
        placeholder = "ч:мм:сс" if duration >= 3600 else "мм:сс"
        self.frag_start_edit.setPlaceholderText(placeholder)
        self.frag_end_edit.setPlaceholderText(placeholder)
        self._update_fragment_summary()

    def _on_slider_changed(self, start, end):
        """Слайдер отпущен (или сдвинут стрелкой) — обновить поля и
        сводку. Превью кадра не скрываем: оно остаётся над ручкой до
        следующего касания, кадр дозапрашивается для финальной позиции."""
        self._sync_fields_from_slider()
        self._update_fragment_summary()
        self._refresh_frame_preview()

    def _sync_fields_from_slider(self):
        """Поля точного ввода над ручками <- слайдер (включая drag без
        отпускания: fieldsMoved)."""
        start, end = self.range_slider.values()
        self.frag_start_edit.setText(fmt_timecode(start))
        self.frag_end_edit.setText(fmt_timecode(end))

    def _on_slider_dragging(self):
        """Тик drag'а (fieldsMoved, в т.ч. само нажатие на ручку): кадр
        едет за ручкой, новый запрашивается сразу (throttle — в
        _request_frame_preview)."""
        which = self.range_slider.active_handle()
        if which is None:
            return
        if which != self._preview_handle:
            self._preview_handle = which
            self.frag_preview_label.hide()   # кадр другой ручки не показываем
        self._place_frame_preview()
        self._request_frame_preview()

    def _refresh_frame_preview(self):
        """Значения сменились без drag (отпускание, стрелки, поля ввода):
        если кадр уже показывали — переставить и обновить его."""
        if self._preview_handle is None:
            return
        self._place_frame_preview()
        self._request_frame_preview()

    def _preview_format(self):
        """Прямой формат для превью кадра или None (не все площадки его
        отдают, см. downloader._pick_preview_format)."""
        fmt = (self.preview_info or {}).get("preview_format")
        return fmt if fmt and fmt.get("url") else None

    def _request_frame_preview(self):
        """Кадр для текущей позиции ручки _preview_handle, с throttle.

        Пока ffmpeg работает или не прошло 250 мс с прошлого запуска —
        ничего: актуальная позиция подхватится по таймеру или по
        завершении ffmpeg (_on_frame_preview_loaded). Работающий ffmpeg
        не убиваем: на HLS YouTube кадр идёт 1-2 с, и при убийстве на
        каждом движении/отпускании он не доезжал до экрана ни разу.
        """
        which = self._preview_handle
        fmt = self._preview_format()
        if which is None or fmt is None:
            return
        if self._preview_worker is not None or self._preview_throttle.isActive():
            return
        start, end = self.range_slider.values()
        second = start if which == "start" else end
        # ffmpeg -ss <длительность> не отдаёт ни одного кадра (rc 69),
        # а ручка конца по умолчанию стоит ровно на конце видео
        duration = int((self.preview_info or {}).get("duration") or 0)
        second = max(0, min(second, duration - 1))
        if (which, second) == self._preview_requested:
            return
        self._preview_requested = (which, second)
        worker = FramePreviewWorker(
            fmt["url"], fmt.get("http_headers"), second, which, self
        )
        worker.loaded.connect(self._on_frame_preview_loaded)
        worker.finished.connect(worker.deleteLater)
        self._preview_worker = worker
        self._preview_throttle.start()
        worker.start()

    def _on_frame_preview_loaded(self, worker, image):
        """Кадр готов (пустой QImage — не удался). Результат устаревшего
        воркера (сброс фрагмента / смена видео) отбрасывается; после
        актуального — запрос свежей позиции: ручка могла уехать, пока
        ffmpeg работал."""
        if worker is not self._preview_worker:
            return
        self._preview_worker = None
        if not image.isNull() and worker.handle == self._preview_handle:
            pix = QPixmap.fromImage(image).scaled(
                self.frag_preview_label.width(),
                self.frag_preview_label.height(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation,
            )
            self.frag_preview_label.setPixmap(pix)
            self._place_frame_preview()
            self.frag_preview_label.show()
        self._request_frame_preview()

    def _place_frame_preview(self):
        """Кадр по центру над ручкой _preview_handle; у краёв прижат,
        чтобы не обрезался."""
        if self._preview_handle is None:
            return
        slider = self.range_slider
        width = self.frag_preview_label.width()
        left = int(slider.handle_x(self._preview_handle) - width / 2)
        left = max(0, min(left, slider.width() - width))
        self.frag_preview_label.move(
            left + slider.x() - self.frag_preview_strip.x(), 0
        )

    def _on_fields_done(self):
        """Поля (Enter/потеря фокуса) -> слайдер: с валидацией и клэмпами.

        Invalid ввод не применяется (поля вернут слайдерные значения при
        следующей синхронизации). Клэмпы те же, что у слайдера: конец не
        раньше начала + 1 с, границы [0..duration].
        """
        start_s = parse_timecode(self.frag_start_edit.text())
        end_s = parse_timecode(self.frag_end_edit.text())
        duration = int((self.preview_info or {}).get("duration") or 0)

        if start_s is None or end_s is None or duration <= 0:
            self._sync_fields_from_slider()   # invalid — вернуть как есть
            return
        # правила к границам и минимальному фрагменту (как в слайдере)
        start_s = max(0, min(start_s, duration))
        end_s = max(0, min(end_s, duration))
        if end_s - start_s < MIN_FRAGMENT_SECONDS:
            self._sync_fields_from_slider()
            return
        self.range_slider.set_values(start_s, end_s)
        self._sync_fields_from_slider()
        self._update_fragment_summary()
        self._refresh_frame_preview()

    def _update_fragment_summary(self):
        """Длительность фрагмента + примерный размер (видео)."""
        start, end = self.range_slider.values()
        self.frag_range_label.setText(
            f"{fmt_timecode(end - start)}"
            + (f" • ~{fmt_mb(self._fragment_size(start, end))}"
               if self._fragment_size(start, end) else "")
        )

    def _fragment_size(self, start, end):
        """Примерный размер фрагмента = размер выбранного качества × доля.

        Для аудио не показываем (нет данных в preview_info — допущение
        из плана). Возвращает байты или None, если размер неизвестен.
        """
        info = self.preview_info or {}
        if info.get("is_playlist") or not info.get("duration"):
            return None
        mode = "audio" if (self.mode_combo.currentText()
                           == MODE_LABELS["audio"]) else "video"
        if mode == "audio":
            return None
        heights = info.get("video_qualities") or []
        sizes = info.get("quality_sizes") or {}
        # выбранное качество — та же функция, что в _start_download
        quality = self._selected_quality(mode)
        if not heights:
            return None
        if quality == "best" or not quality.isdigit():
            height = heights[0]
        else:
            height = int(quality)
        size = sizes.get(height)
        if not size:
            return None
        share = (end - start) / float(info["duration"])
        return int(size * share)

    def _choose_dir(self):
        from PySide6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(
            self, "Выбрать папку", self.dir_edit.currentText()
        )
        if path:
            self._set_combo_dir(self.dir_edit, path)

    @staticmethod
    def _set_combo_dir(combo, path):
        """Установить папку в EditableComboBox (добавляя её в список).

        setCurrentText у qfluentwidgets игнорирует текст, которого
        нет в item list — поэтому сначала addItem.
        """
        if combo.findText(path) < 0:
            combo.addItem(path)
        combo.setCurrentText(path)

    def _start_download(self):
        url = self.url_edit.text().strip()
        output_dir = (self.dir_edit.currentText().strip()
                      or self.settings["default_folder"])
        if not url:
            return
        if not os.path.isdir(output_dir):
            self._set_status("Папка не существует", "error")
            return

        mode = "audio" if self.mode_combo.currentText() == MODE_LABELS["audio"] else "video"
        quality = self._selected_quality(mode)

        # Фрагмент (1.0.5): передаём диапазон при включённой галочке и
        # валидном состоянии. isVisible() НЕ проверяем: видимость зависит
        # от родительских виджетов (сворачивание/переключение страниц), и
        # при False валидный выбор молча отбросился бы — скачалось бы
        # полное видео вместо выбранных 30 секунд.
        time_range = None
        precise_cut = False
        if self.fragment_check.isChecked() and self._fragment_available():
            start, end = self.range_slider.values()
            if end - start >= MIN_FRAGMENT_SECONDS:
                time_range = (start, end)
                precise_cut = self.precise_check.isChecked()

        self.manager.add(
            url, mode=mode, quality=quality, output_dir=output_dir,
            playlist=self.playlist_check.isChecked(),
            time_range=time_range, precise_cut=precise_cut,
        )
        self._reset_preview()
        self.url_edit.clear()
        self._rebuild_current()

    def _selected_quality(self, mode):
        """Выбранное качество («best» или высота в пикселях) — единая
        точка для _start_download и _fragment_size (иначе расчётный
        размер показался бы не от того качества)."""
        if mode == "audio" or not self.preview_info:
            return "best"
        heights = self.preview_info.get("video_qualities") or []
        index = self.quality_combo.currentIndex()
        if index == 0:
            return "best"
        if 0 < index <= len(heights):
            return str(heights[index - 1])
        # fallback: цифры из подписи (как было в _start_download)
        label = self.quality_combo.currentText()
        digits = "".join(ch for ch in label if ch.isdigit())
        return digits if digits else "best"

    # ---------- текущие загрузки ----------

    def _rebuild_current(self):
        """Перестроить секцию загрузок (все задачи, включая завершённые)."""
        while self.current_vbox.count():
            child = self.current_vbox.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self.current_cards = {}

        with self.manager._lock:
            ids = list(self.manager.order)

        self.current_header.setVisible(bool(ids))

        for item_id in ids:
            with self.manager._lock:
                item = self.manager.items.get(item_id)
            if item is None:
                continue
            card = QueueCard(item, self.manager, self.bridge, self)
            self.current_cards[item_id] = card
            self.current_vbox.addWidget(card)

    def _on_current_item(self, item):
        card = self.current_cards.get(item.id)
        if card is None:
            self._rebuild_current()
            return
        card.update_state(item)
        self.current_header.setVisible(True)


# ================= КАРТОЧКА ЗАДАЧИ =================

class QueueCard(CardWidget):
    def __init__(self, item, manager, bridge, parent=None):
        super().__init__(parent)
        self.item = item
        self.manager = manager

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP_GROUP * 2, SP_GROUP * 2,
                               SP_GROUP * 2, SP_GROUP * 2)
        lay.setSpacing(SP_GROUP)

        head = QHBoxLayout()
        head.setSpacing(SP_GROUP * 2)

        self.thumb = ImageLabel(self) if ImageLabel is not QLabel else QLabel(self)
        if ImageLabel is not QLabel:
            self.thumb.setFixedSize(120, 68)
        else:
            self.thumb.setFixedSize(120, 68)
            self.thumb.setAlignment(Qt.AlignCenter)
            self.thumb.setText("🎬")
        head.addWidget(self.thumb)

        text_lay = QVBoxLayout()
        text_lay.setSpacing(4)
        self.title_label = StrongBodyLabel(item.label, self)
        self.title_label.setWordWrap(True)
        text_lay.addWidget(self.title_label)
        self.meta_label = BodyLabel("", self)
        self.meta_label.setWordWrap(True)
        text_lay.addWidget(self.meta_label)
        text_lay.addStretch()
        head.addLayout(text_lay, stretch=1)

        self.actions_widget = QWidget(self)
        acts = QHBoxLayout(self.actions_widget)
        acts.setContentsMargins(0, 0, 0, 0)
        acts.setSpacing(SP_GROUP)
        lay.addLayout(head)

        self.bar = ProgressBar(self)
        self.bar.setRange(0, 100)
        self.bar.hide()
        lay.addWidget(self.bar)

        # Кэш статуса: кнопки/тексты статуса пересоздаём только при смене
        # статуса, а на тиках прогресса обновляем только бар и мету (BUG-11).
        self._last_status = None

        self.update_state(item)

    def _clear_actions(self):
        while self.actions_widget.layout().count():
            child = self.actions_widget.layout().takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def _add_action(self, text, slot, accent=False):
        btn = PrimaryPushButton(text, self.actions_widget) if accent else \
            PushButton(text, self.actions_widget)
        btn.clicked.connect(slot)
        self.actions_widget.layout().addWidget(btn)

    def update_state(self, item):
        status_text = {
            STATUS_QUEUED: "В очереди",
            STATUS_ANALYZING: "Анализ…",
            STATUS_DOWNLOADING: "Скачивается",
            STATUS_PAUSED: "Пауза",
            STATUS_PROCESSING: "Обработка…",
            STATUS_COMPLETED: "Готово",
            STATUS_ERROR: "Ошибка",
        }.get(item.status, item.status)

        # 1.0.5: фрагмент качается через ffmpeg-процесс (FFmpegFD) без
        # progress-hooks — процента нет, но статус честный: «Скачивание
        # фрагмента…» вместо зависшего «Анализ…». При точной обрезке
        # (перекодирование) это может занять несколько минут.
        if (item.time_range and item.status == STATUS_DOWNLOADING):
            status_text = "Скачивание фрагмента…"
            if item.precise_cut:
                status_text += " Точная обрезка — может занять несколько минут"

        parts = [status_text]
        if item.time_range:
            # Фрагмент (1.0.5): метка в карточке задачи
            start, end = item.time_range
            parts.insert(0, f"фрагмент {fmt_timecode(start)}–{fmt_timecode(end)}")
        if item.status == STATUS_DOWNLOADING:
            self.bar.show()
            self.bar.setValue(int(item.progress))
            parts.append(f"{item.progress:.0f}%")
            if item.total_bytes:
                downloaded_mb = fmt_mb(item.downloaded_bytes)
                total_mb = fmt_mb(item.total_bytes)
                parts.append(f"{downloaded_mb} из {total_mb}")
            if item.speed:
                parts.append(fmt_speed(item.speed))
            if item.eta:
                parts.append(f"осталось {fmt_eta(item.eta)}")
        elif item.status == STATUS_PAUSED:
            self.bar.show()
            self.bar.setValue(int(item.progress))
            if item.total_bytes:
                parts.append(
                    f"{fmt_mb(item.downloaded_bytes)} из {fmt_mb(item.total_bytes)}"
                )
        elif item.status == STATUS_COMPLETED:
            self.bar.show()
            self.bar.setValue(100)
        elif item.status == STATUS_ERROR:
            self.bar.hide()
            if item.error:
                parts.append(item.error[:120])
        else:
            self.bar.hide()

        self.meta_label.setText("  •  ".join(parts))

        # Кнопки пересоздаём только при смене статуса: раньше это
        # происходило на каждом тике прогресса (десятки раз/сек).
        if item.status == self._last_status:
            return
        self._last_status = item.status

        self._clear_actions()
        # 1.0.5: у фрагмента пауза скрыта — yt-dlp качает секции через
        # FFmpegFD (ffmpeg-процесс), progress-hooks не дергаются и
        # посреди скачивания пауза/докачка .part неприменимы. «Отмена»
        # остаётся и срабатывает сразу — её обрабатывает сторож
        # (_fragment_watch в downloader.py), не дожидаясь конца ffmpeg.
        can_pause = not item.time_range
        if item.status in (STATUS_DOWNLOADING, STATUS_QUEUED,
                           STATUS_ANALYZING):
            if can_pause:
                self._add_action("Пауза", lambda: self.manager.pause(item.id))
            self._add_action("Отмена", lambda: self.manager.cancel(item.id))
        elif item.status == STATUS_PAUSED:
            self._add_action("Продолжить",
                             lambda: self.manager.resume(item.id), accent=True)
            self._add_action("Отмена", lambda: self.manager.cancel(item.id))
        elif item.status == STATUS_ERROR:
            self._add_action("Повторить",
                             lambda: self.manager.resume(item.id), accent=True)
            self._add_action("Убрать", lambda: self.manager.remove(item.id))
        elif item.status == STATUS_COMPLETED:
            self._add_action("Открыть папку", lambda: self._open_folder(item))
            self._add_action("Убрать", lambda: self.manager.remove(item.id))

        head = self.layout().itemAt(0).layout()
        # actions в head
        self.actions_widget.setParent(self)
        head.addWidget(self.actions_widget)

    def _open_folder(self, item):
        if os.path.isdir(item.output_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(item.output_dir))


# ================= СТРАНИЦА: БИБЛИОТЕКА =================

class TransparentScrollArea(ScrollArea):
    """ScrollArea с корректной прозрачностью для обеих тем.

    Прозрачность через селектор по objectName — не задевает дочерние
    карточки; применяется после setWidget.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._inner = None

    def setWidget(self, widget):
        super().setWidget(widget)
        self._inner = widget
        # Прозрачность только для скролл-области и внутреннего контейнера
        widget.setStyleSheet("QWidget#scrollInner{background: transparent}")
        self.setStyleSheet("QScrollArea{border: none; background: transparent}")


def source_from_url(url):
    """Определяет источник по URL: youtube / vk / instagram / tiktok / …"""
    try:
        host = url.split("/")[2].lower()
    except (IndexError, AttributeError):
        return "Другое"
    if "youtube" in host or "youtu.be" in host:
        return "YouTube"
    if _is_vk_url(url):
        return "VK"
    if "instagram" in host:
        return "Instagram"
    if "tiktok" in host:
        return "TikTok"
    if "rutube" in host:
        return "Rutube"
    if "twitter" in host or "x.com" in host:
        return "X (Twitter)"
    return host


class LibraryThumbWorker(QThread):
    """Миниатюра для карточки библиотеки."""

    loaded = Signal(str, QImage)

    def __init__(self, url, size, parent=None):
        super().__init__(parent)
        self.url = url
        self.size = size

    def run(self):
        image = QImage()
        if self.url:
            try:
                data = urllib.request.urlopen(self.url, timeout=8).read()
                img = QImage()
                img.loadFromData(data)
                if not img.isNull():
                    image = img.scaled(
                        self.size[0], self.size[1],
                        Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation,
                    )
            except Exception:
                image = QImage()
        self.loaded.emit(self.url, image)


class LibraryRow(CardWidget):
    """Строка библиотеки: миниатюра, название, детали, кнопки."""

    def __init__(self, entry, page=None):
        super().__init__(page)
        self.entry = entry

        lay = QHBoxLayout(self)
        lay.setContentsMargins(SP_GROUP * 2, SP_GROUP * 2,
                               SP_GROUP * 2, SP_GROUP * 2)
        lay.setSpacing(SP_GROUP * 2)

        # 1.0.3: выбор записи для удаления (кнопка «Удалить» на панели).
        # Колбэк на страницу: LibraryRow не знает про выбор других строк.
        self.select_check = CheckBox(self)
        self.select_check.stateChanged.connect(
            lambda: page._on_selection_changed() if page else None
        )
        lay.addWidget(self.select_check)

        # Миниатюра (фрагмент видео) слева. Воркер парентится к СТРАНИЦЕ,
        # а не к карточке: refresh() удаляет карточки deleteLater'ом, и
        # живой QThread, привязанный к карточке, крашил бы приложение
        # (Qt6: qFatal «QThread destroyed while thread is still running»).
        self.thumb = ImageLabel(self)
        self.thumb.setFixedSize(120, 68)
        self._thumb_url = entry.get("thumbnail")
        if self._thumb_url and page is not None:
            cached = page._get_cached_thumb(self._thumb_url)
            if cached is not None:
                # Кеш сессии: без сети и без нового потока
                self.thumb.setImage(cached)
                self.thumb.scaledToHeight(68)
            else:
                worker = LibraryThumbWorker(
                    self._thumb_url, (120, 68), page
                )
                worker.loaded.connect(self._on_thumb)
                page._track_thumb(worker)
                worker.start()
        self.thumb.scaledToHeight(68)
        lay.addWidget(self.thumb)

        # Название + детали
        text = QVBoxLayout()
        text.setSpacing(4)
        title = StrongBodyLabel(entry.get("title") or
                                os.path.basename(entry.get("path", "")), self)
        title.setWordWrap(True)
        text.addWidget(title)

        detail_parts = []
        for key, value in (
            ("quality", entry.get("quality")),
            ("size", entry.get("size_text")),
            ("duration", entry.get("duration_text")),
            ("fragment", entry.get("fragment")),
            ("format", entry.get("format")),
            ("source", entry.get("source")),
        ):
            if value:
                label = {"quality": "Качество", "size": "Размер",
                         "duration": "Длительность", "fragment": "Фрагмент",
                         "format": "Формат",
                         "source": "Источник"}.get(key)
                detail_parts.append(f"{label}: {value}")
        details = BodyLabel("  •  ".join(detail_parts) if detail_parts else "",
                            self)
        details.setWordWrap(True)
        text.addWidget(details)
        text.addStretch()
        lay.addLayout(text, stretch=1)

        # Кнопки справа
        open_btn = PushButton("Открыть", self)
        open_btn.setIcon(FIF.PLAY)
        open_btn.clicked.connect(self._open_file)
        lay.addWidget(open_btn)

        dir_btn = PushButton("Открыть папку", self)
        dir_btn.setIcon(FIF.FOLDER)
        dir_btn.clicked.connect(self._open_folder)
        lay.addWidget(dir_btn)

    def _on_thumb(self, url, image):
        if not image.isNull():
            # Сохранить в кеш страницы (сессии)
            page = self.parent()
            if isinstance(page, LibraryPage):
                page._store_cached_thumb(url, image)
            self.thumb.setImage(image)

    def _open_file(self):
        path = self.entry.get("path")
        if path and os.path.isfile(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_folder(self):
        path = self.entry.get("path")
        if path:
            folder = os.path.dirname(path)
            if os.path.isdir(folder):
                QDesktopServices.openUrl(QUrl.fromLocalFile(folder))


# ================= ДИАЛОГ ПОДТВЕРЖДЕНИЯ УДАЛЕНИЯ =================


class ConfirmDeleteDialog(MessageBoxBase):
    """«Удалить N записей?» с галочкой «Удалить также файлы с диска».

    Галочка по умолчанию выключена (удаляем только из Библиотеки).
    С галочкой файлы удаляются навсегда (os.remove), не в Корзину —
    поэтому под галочкой предупреждение. exec() возвращает True при
    подтверждении, выбранное состояние — в атрибуте delete_files.
    """

    def __init__(self, count, parent=None):
        super().__init__(parent)
        self.delete_files = False

        self.viewLayout.addWidget(SubtitleLabel(
            f"Удалить {ru_records(count)}?", self))
        self.viewLayout.addWidget(BodyLabel(
            "Записи будут убраны из Библиотеки.", self))
        self.files_check = CheckBox("Удалить также файлы с диска", self)
        self.files_check.setChecked(False)
        self.files_check.stateChanged.connect(
            lambda: self._on_files_check())
        self.viewLayout.addWidget(self.files_check)
        self.warn_label = CaptionLabel("Файлы будут удалены навсегда", self)
        self.warn_label.setVisible(False)
        self.viewLayout.addWidget(self.warn_label)

        self.yesButton.setText("Удалить")
        self.cancelButton.setText("Отмена")

    def _on_files_check(self):
        self.delete_files = self.files_check.isChecked()
        self.warn_label.setVisible(self.delete_files)


# ================= СТРАНИЦА: БИБЛИОТЕКА =================


class LibraryPage(TransparentScrollArea):
    def __init__(self, bridge, manager, settings, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.manager = manager
        self.settings = settings
        # Живые воркеры миниатюр (чтобы не терять при refresh и закрытии)
        self._thumb_workers = []

        self.setViewportMargins(SP_WINDOW, SP_WINDOW, SP_WINDOW, SP_WINDOW)
        self.setWidgetResizable(True)

        inner = QWidget()
        inner.setObjectName("scrollInner")
        self.vbox = QVBoxLayout(inner)
        self.vbox.setSpacing(SP_BLOCK)
        self.vbox.setContentsMargins(0, 0, 0, 0)
        self.vbox.addWidget(TitleLabel("Библиотека"))

        # Панель: поиск + фильтр по источнику
        filters = QHBoxLayout()
        filters.setSpacing(SP_GROUP)

        self.search = SearchLineEdit()
        self.search.setPlaceholderText("Поиск по названию…")
        self.search.textChanged.connect(lambda: self.refresh())
        filters.addWidget(self.search, stretch=1)

        source_label = BodyLabel("Источник:", self)
        filters.addWidget(source_label)
        self.source_filter = ComboBox(self)
        self.source_filter.currentIndexChanged.connect(lambda: self.refresh())
        filters.addWidget(self.source_filter)

        # 1.0.3: управление записями — удаление выбранных и очистка списка.
        # «Удалить» активна при выбранной записи, «Очистить данные
        # библиотеки» — при непустой истории (обновляется в refresh()).
        self.delete_btn = PushButton("Удалить", self)
        self.delete_btn.setIcon(FIF.DELETE)
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._delete_selected)
        filters.addWidget(self.delete_btn)

        self.clear_btn = PushButton("Очистить данные библиотеки", self)
        self.clear_btn.setIcon(FIF.BROOM)
        self.clear_btn.setEnabled(False)
        self.clear_btn.clicked.connect(self._clear_library_data)
        filters.addWidget(self.clear_btn)

        self.vbox.addLayout(filters)

        self.empty_label = BodyLabel("Здесь появятся завершённые загрузки")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.vbox.addWidget(self.empty_label, stretch=1)

        # Контейнер строк: при refresh очищаем ТОЛЬКО его — постоянные
        # элементы (заголовок, фильтры, empty_label, stretch) не трогаем.
        # Раньше цикл «while vbox.count() > 3» по индексам съедал empty_label
        # и stretch → краш на 3-м refresh (RuntimeError: C++ object deleted).
        self.rows_container = QWidget(inner)
        self.rows_vbox = QVBoxLayout(self.rows_container)
        self.rows_vbox.setContentsMargins(0, 0, 0, 0)
        self.rows_vbox.setSpacing(SP_GROUP)
        self.vbox.addWidget(self.rows_container)

        self.vbox.addStretch(1)
        self.setWidget(inner)

    def _entries(self):
        """Записи истории (только они: чужие файлы из папки загрузок
        не показываем — 1.0.3). get_history() оставляет только
        существующие на диске файлы, дубли путей исключены
        (дедуп в config.load/add_history).
        """
        import config as cfg
        return list(cfg.get_history(self.settings))

    # ---------- 1.0.3: выбор, удаление записей, очистка списка ----------

    def _selected_rows(self):
        """Выбранные строки (в порядке отображения)."""
        rows = []
        for i in range(self.rows_vbox.count()):
            item = self.rows_vbox.itemAt(i)
            widget = item.widget() if item else None
            if isinstance(widget, LibraryRow) and widget.select_check.isChecked():
                rows.append(widget)
        return rows

    def _on_selection_changed(self):
        """Чекбокс строки переключён — пересчитать «Удалить»."""
        self.delete_btn.setEnabled(bool(self._selected_rows()))

    def _confirm_delete(self, count):
        """Подтверждение удаления (вынесено отдельно для тестируемости).

        Возвращает (ok, delete_files): ok — подтверждено; delete_files —
        стоит ли удалять файлы с диска (галочка, по умолчанию выключена).
        """
        dialog = ConfirmDeleteDialog(count, self.window())
        ok = bool(dialog.exec())
        return ok, (dialog.delete_files if ok else False)

    def _delete_selected(self):
        """Удалить выбранные записи из Библиотеки (по подтверждению).

        Без галочки — только записи. С галочкой — файлы удаляются навсегда
        (os.remove ровно по пути записи; папки, маски и соседние файлы
        вроде .part не затрагиваются). Не удалившийся файл (занят, нет
        прав) остаётся в Библиотеке, его имя — в InfoBar; файла уже нет на
        диске — не ошибка, запись удаляется.
        """
        rows = self._selected_rows()
        if not rows:
            return
        ok, delete_files = self._confirm_delete(len(rows))
        if not ok:
            return

        failed = []           # файлы, которые не удалось удалить с диска
        paths_to_remove = []  # записи, уходящие из Библиотеки

        for row in rows:
            path = row.entry.get("path") or ""
            if delete_files and path and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    failed.append(os.path.basename(path))
                    continue
            paths_to_remove.append(path)

        config.remove_history(self.settings, paths_to_remove)
        config.save(self.settings)
        self.refresh()

        if failed:
            self._notify("warning", "Не удалены файлы: " + ", ".join(failed))
        elif paths_to_remove:
            self._notify("success",
                         "Удалено: " + ru_records(len(paths_to_remove)))

    def _confirm_clear(self):
        """Подтверждение очистки списка (отдельно для тестируемости)."""
        from qfluentwidgets import MessageBox
        box = MessageBox(
            "Очистить данные библиотеки",
            "Список загрузок будет очищен. Файлы на диске останутся.",
            self.window(),
        )
        return bool(box.exec())

    def _clear_library_data(self):
        """Очистить список записей (файлы на диске не трогаются)."""
        if not self.settings.get("history"):
            return
        if not self._confirm_clear():
            return
        config.clear_history(self.settings)
        config.save(self.settings)
        self.refresh()
        self._notify("success", "Данные библиотеки очищены")

    def _notify(self, kind, text):
        """Итог операции в InfoBar (в offscreen-тестах подменяется)."""
        bar = InfoBar.warning if kind == "warning" else InfoBar.success
        bar(
            title="Библиотека",
            content=text,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=-1 if kind == "warning" else 4000,
            parent=self.window(),
        )

    def refresh(self):
        # Очистить ТОЛЬКО строки в rows_vbox (постоянные элементы страницы
        # не трогаем — см. комментарий в __init__).
        while self.rows_vbox.count():
            item = self.rows_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        entries = self._entries()

        # Собрать уникальные источники для фильтра
        sources = []
        for e in entries:
            src = e.get("source") or "Другое"
            if src not in sources:
                sources.append(src)
        sources.sort()

        # Обновить фильтр без повторного вызова refresh
        self.source_filter.blockSignals(True)
        current = self.source_filter.currentText()
        self.source_filter.clear()
        self.source_filter.addItem("Все")
        self.source_filter.addItems(sources)
        if current in ["Все"] + sources:
            self.source_filter.setCurrentText(current)
        else:
            self.source_filter.setCurrentIndex(0)
        self.source_filter.blockSignals(False)

        # Фильтрация
        selected_source = self.source_filter.currentText()
        query = self.search.text().strip().lower()

        rows = []
        for e in entries:
            src = e.get("source") or "Другое"
            if selected_source != "Все" and src != selected_source:
                continue
            title = (e.get("title") or "").lower()
            if query and query not in title:
                continue
            rows.append(e)

        if rows:
            self.empty_label.hide()
        else:
            self.empty_label.show()

        for e in rows:
            card = self._make_row(e)
            self.rows_vbox.addWidget(card)

        # 1.0.3: строки пересозданы — выбор сброшен. «Удалить» неактивна
        # без выбранных записей; очистка — при непустой истории.
        self.delete_btn.setEnabled(False)
        self.clear_btn.setEnabled(bool(self.settings.get("history")))

    def _make_row(self, entry):
        # Дополнить запись метаданными файла
        path = entry.get("path", "")
        if os.path.isfile(path):
            try:
                size_mb = os.path.getsize(path) / 1024 / 1024
                entry["size_text"] = f"{size_mb:.1f} МБ"
            except OSError:
                pass
            if not entry.get("format"):
                entry["format"] = os.path.splitext(path)[1].upper().lstrip(".")
        if entry.get("duration"):
            entry["duration_text"] = fmt_duration(entry["duration"])

        return LibraryRow(entry, self)

    # Кеш миниатюр на сессию: url -> QImage. Общий для страницы, живёт
    # между refresh (раньше каждый refresh перекачивал все картинки).
    _thumb_cache = {}
    _thumb_cache_max = 100

    def _track_thumb(self, worker):
        """Учесть живой воркер миниатюры (удаляется по finished).

        Нужно, чтобы при refresh/закрытии не остались висящие потоки.
        """
        self._thumb_workers.append(worker)
        worker.finished.connect(lambda: self._thumb_workers.remove(worker)
                                if worker in self._thumb_workers else None)

    def _get_cached_thumb(self, url):
        if not url:
            return None
        img = self.__class__._thumb_cache.get(url)
        return img if (img is not None and not img.isNull()) else None

    def _store_cached_thumb(self, url, image):
        if url and not image.isNull():
            cache = self.__class__._thumb_cache
            cache[url] = image
            # Простой LRU-обрезка по размеру
            if len(cache) > self._thumb_cache_max:
                for key in list(cache.keys())[:len(cache) - self._thumb_cache_max]:
                    del cache[key]

    def stop_workers(self):
        """Остановить все воркеры миниатюр (при закрытии окна)."""
        for worker in list(self._thumb_workers):
            try:
                worker.quit()
                worker.wait(1500)
            except Exception:
                pass
        self._thumb_workers.clear()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()


# ================= СТРАНИЦА: НАСТРОЙКИ =================

class SettingsPage(TransparentScrollArea):
    themeChanged = Signal(str)
    folderChanged = Signal(str)
    maxConcurrentChanged = Signal(int)

    def __init__(self, settings, bridge=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.bridge = bridge
        self._update_worker = None
        self._update_manifest = None
        self._update_dest = None

        self.setViewportMargins(SP_WINDOW, SP_WINDOW, SP_WINDOW, SP_WINDOW)
        self.setWidgetResizable(True)

        inner = QWidget()
        inner.setObjectName("scrollInner")
        self.vbox = QVBoxLayout(inner)
        self.vbox.setSpacing(SP_BLOCK)
        self.vbox.setContentsMargins(0, 0, 0, 0)
        self.vbox.addWidget(TitleLabel("Настройки"))

        # Внешний вид
        group = SettingCardGroup("Внешний вид", self)
        group.addSettingCard(self._theme_card(inner))
        self.vbox.addWidget(group)

        # Загрузки
        group2 = SettingCardGroup("Загрузки", self)
        for card in self._download_cards(inner):
            group2.addSettingCard(card)
        self.vbox.addWidget(group2)

        # Обновления
        group3 = SettingCardGroup("Обновления", self)
        for card in self._update_cards(inner):
            group3.addSettingCard(card)
        self.vbox.addWidget(group3)

        self.vbox.addStretch(1)
        self.setWidget(inner)

    def _update_cards(self, parent):
        cards = []

        # Текущая версия + кнопка «Проверить»
        version_card = SettingCard(
            FIF.SYNC, "Версия", config.APP_VERSION, parent,
        )
        self.check_update_btn = PushButton("Проверить обновления", version_card)
        self.check_update_btn.clicked.connect(self.check_updates)
        version_card.hBoxLayout.addWidget(self.check_update_btn)
        version_card.hBoxLayout.addSpacing(SP_GROUP)
        self.update_status_label = BodyLabel("", version_card)
        self.update_status_label.hide()
        version_card.hBoxLayout.addWidget(self.update_status_label)
        cards.append(version_card)

        # Автопроверка при старте
        auto_card = SettingCard(
            FIF.UPDATE, "Проверять автоматически",
            "Тихая проверка при запуске", parent,
        )
        auto_check = CheckBox(auto_card)
        auto_check.setChecked(bool(self.settings.get("check_updates", True)))
        auto_check.stateChanged.connect(
            lambda state: self.settings.__setitem__(
                "check_updates", bool(state)
            )
        )
        auto_card.hBoxLayout.addWidget(auto_check)
        auto_card.hBoxLayout.addSpacing(SP_GROUP)
        cards.append(auto_card)

        return cards

    def _theme_card(self, parent):
        card = SettingCard(
            FIF.BRUSH, "Тема оформления",
            "Светлая, тёмная или системная", parent,
        )
        self.theme_seg = SegmentedWidget(card)
        self.theme_seg.addItem("light", "Светлая")
        self.theme_seg.addItem("dark", "Тёмная")
        self.theme_seg.addItem("system", "Системная")
        self.theme_seg.setCurrentItem(self.settings["theme"])
        self.theme_seg.currentItemChanged.connect(
            lambda key: self.themeChanged.emit(key)
        )
        card.hBoxLayout.addWidget(self.theme_seg)
        card.hBoxLayout.addSpacing(SP_GROUP)
        return card

    def _download_cards(self, parent):
        cards = []

        mode_card = SettingCard(
            FIF.VIDEO, "Формат по умолчанию", "Видео или аудио", parent,
        )
        mode_combo = ComboBox(mode_card)
        mode_combo.addItems(list(MODE_LABELS.values()))
        mode_combo.setCurrentIndex(
            list(MODE_LABELS.keys()).index(self.settings["default_mode"])
        )
        mode_combo.currentIndexChanged.connect(
            lambda i: self.settings.__setitem__(
                "default_mode", list(MODE_LABELS.keys())[i]
            )
        )
        mode_card.hBoxLayout.addWidget(mode_combo)
        mode_card.hBoxLayout.addSpacing(SP_GROUP)
        cards.append(mode_card)

        q_card = SettingCard(
            FIF.QUALITY_HIGH if hasattr(FIF, "QUALITY_HIGH") else FIF.SETTING,
            "Качество по умолчанию", "Если доступно", parent,
        )
        q_combo = ComboBox(q_card)
        q_combo.addItems(["Лучшее", "1080p", "720p", "480p"])
        current = self.settings["default_quality"]
        q_combo.setCurrentText(
            "Лучшее" if current == "best" else f"{current}p"
        )
        q_combo.currentTextChanged.connect(
            lambda text: self.settings.__setitem__(
                "default_quality",
                "best" if text == "Лучшее" else "".join(
                    c for c in text if c.isdigit()
                ),
            )
        )
        q_card.hBoxLayout.addWidget(q_combo)
        q_card.hBoxLayout.addSpacing(SP_GROUP)
        cards.append(q_card)

        folder_card = SettingCard(
            FIF.FOLDER, "Папка по умолчанию", "Куда сохранять файлы", parent,
        )
        self.folder_edit = EditableComboBox(folder_card)
        self.folder_edit.addItems([self.settings["default_folder"]])
        self.folder_edit.setCurrentText(self.settings["default_folder"])
        self.folder_edit.setMinimumWidth(260)
        self.folder_edit.currentTextChanged.connect(
            lambda text: self.settings.__setitem__("default_folder", text)
        )
        self.folder_edit.currentTextChanged.connect(self.folderChanged.emit)
        folder_card.hBoxLayout.addWidget(self.folder_edit)
        folder_card.hBoxLayout.addSpacing(SP_GROUP)
        browse = PushButton("Обзор…", folder_card)
        browse.clicked.connect(self._browse_folder)
        folder_card.hBoxLayout.addWidget(browse)
        folder_card.hBoxLayout.addSpacing(SP_GROUP)
        cards.append(folder_card)

        conc_card = SettingCard(
            FIF.SPEED_HIGH if hasattr(FIF, "SPEED_HIGH") else FIF.SETTING,
            "Одновременных загрузок", "От 1 до 5", parent,
        )
        self.conc_spin = SpinBox(conc_card)
        self.conc_spin.setRange(1, 5)
        self.conc_spin.setValue(int(self.settings["max_concurrent"]))
        self.conc_spin.valueChanged.connect(
            lambda v: self.settings.__setitem__("max_concurrent", int(v))
        )
        # BUG-5: применение на лету, не только после перезапуска
        self.conc_spin.valueChanged.connect(
            lambda v: self.maxConcurrentChanged.emit(int(v))
        )
        conc_card.hBoxLayout.addWidget(self.conc_spin)
        conc_card.hBoxLayout.addSpacing(SP_GROUP)
        cards.append(conc_card)

        return cards

    def sync_theme(self, mode):
        """Синхронизировать SegmentedWidget темы."""
        self.theme_seg.blockSignals(True)
        self.theme_seg.setCurrentItem(mode)
        self.theme_seg.blockSignals(False)

    def _browse_folder(self):
        from PySide6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(
            self, "Выбрать папку", self.folder_edit.currentText()
        )
        if path:
            # setCurrentText у qfluentwidgets игнорирует текст не из списка
            if self.folder_edit.findText(path) < 0:
                self.folder_edit.addItem(path)
            self.folder_edit.setCurrentText(path)
            self.settings["default_folder"] = path

    # ---------- обновления ----------

    def check_updates(self, silent=False):
        """Проверить обновления (кнопкой или тихо при старте)."""
        if self._update_worker is not None and self._update_worker.isRunning():
            return
        self._update_silent = silent
        self._update_worker = UpdateWorker(
            "check", manifest_url=self.settings.get("update_manifest_url"),
            parent=self,
        )
        self._update_worker.manifestReady.connect(self._on_manifest)
        self._update_worker.manifestError.connect(self._on_manifest_error)
        self._update_worker.start()

    def _on_manifest_error(self, error):
        """Проверка не удалась (сеть/таймаут) — не «нет обновлений»."""
        self._update_manifest = None
        if not self._update_silent:
            self.show_update_info(f"Не удалось проверить обновления: {error}")

    def _on_manifest(self, manifest):
        self._update_manifest = manifest
        if manifest is None:
            if not self._update_silent:
                self.show_update_info(
                    f"У вас последняя версия ({config.APP_VERSION})"
                )
            return

        version = manifest.get("version", "?")
        notes = manifest.get("notes", "")
        # InfoBar с кнопкой «Обновить»
        bar = InfoBar.info(
            title=f"Доступна версия {version}",
            content=notes or "Нажмите «Обновить» для установки",
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=-1,   # не закрывать сама
            parent=self.window(),
        )
        bar.addWidget(PushButton("Обновить"))
        button = [w for w in bar.findChildren(PushButton)][0]
        button.clicked.connect(self._start_update_download)
        bar.show()

    def show_update_info(self, text):
        InfoBar.info(
            title="Обновления",
            content=text,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4000,
            parent=self.window(),
        ).show()

    def _start_update_download(self):
        """Подтверждение → скачивание установщика с прогрессом."""
        if not self._update_manifest:
            return

        # подтверждение перезапуска
        from qfluentwidgets import MessageBox
        box = MessageBox(
            "Обновление",
            "Программа закроется, обновится и перезапустится. Продолжить?",
            self.window(),
        )
        if not box.exec():
            return
        import tempfile
        self._update_dest = os.path.join(
            tempfile.gettempdir(),
            f"VideoDownloader-Setup-{self._update_manifest.get('version', 'x')}.exe",
        )
        self._update_worker = UpdateWorker(
            "download",
            manifest=self._update_manifest,
            dest=self._update_dest,
            parent=self,
        )
        self._update_worker.downloadProgress.connect(self._on_download_progress)
        self._update_worker.downloadDone.connect(self._on_download_done)
        self._update_worker.downloadFailed.connect(self._on_download_failed)
        self._update_worker.start()

    def _on_download_progress(self, percent, downloaded, total):
        # Прогресс показываем в статус-лейбле карточки версии
        mb_down = downloaded / 1024 / 1024
        mb_total = total / 1024 / 1024 if total else 0
        self.update_status_label.setText(
            f"Скачивание: {percent:.0f}% ({mb_down:.0f} из {mb_total:.0f} МБ)"
        )
        self.update_status_label.show()

    def _on_download_done(self, path):
        self.update_status_label.hide()
        # Запуск тихой переустановки + перезапуск, выход приложения
        app_exe = sys.executable
        try:
            updater.apply_update(path, app_exe)
        except RuntimeError as exc:
            self.show_update_info(f"Ошибка обновления: {exc}")
            return
        # немедленный выход — установщик ждёт 3 сек
        self.window().close()

    def _on_download_failed(self, error):
        self.update_status_label.hide()
        self.show_update_info(f"Ошибка обновления: {error}")

    def save(self):
        config.save(self.settings)


# ================= ГЛАВНОЕ ОКНО =================

class MainWindow(FluentWindow):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings

        self.setWindowTitle(APP_NAME)
        self.resize(1000, 680)
        self.setMinimumSize(900, 600)

        self.bridge = Bridge()
        self.manager = DownloadManager(
            max_concurrent=self.settings["max_concurrent"],
            on_change=lambda item: self.bridge.itemChanged.emit(item),
            on_queue_change=lambda: self.bridge.queueChanged.emit(),
        )
        self.manager.start()

        self.download_page = DownloadPage(
            self.bridge, self.manager, self.settings, self
        )
        self.download_page.setObjectName("downloadInterface")
        self.library_page = LibraryPage(
            self.bridge, self.manager, self.settings, self
        )
        self.library_page.setObjectName("libraryInterface")
        self.settings_page = SettingsPage(self.settings, self.bridge, self)
        self.settings_page.setObjectName("settingsInterface")

        self.download_page.parent_window = self

        self._setup_navigation()
        self._apply_theme(self.settings["theme"], save=False)

        self.settings_page.themeChanged.connect(self._apply_theme)
        # Смена папки в настройках — синхронизировать поле на «Загрузке»
        self.settings_page.folderChanged.connect(self._on_default_folder_changed)
        # Число одновременных загрузок применяется к живому менеджеру сразу
        self.settings_page.maxConcurrentChanged.connect(
            self.manager.set_max_concurrent
        )

        # Запись завершённых загрузок в историю
        self.bridge.itemChanged.connect(self._on_item_finished)

        self.navigationInterface.itemSelectIfNoneInit = True

        # Тихая автопроверка обновлений при старте (отложенная, 2.5с):
        # оффлайн / нет обновлений — ничего не показываем.
        if self.settings.get("check_updates", True):
            QTimer.singleShot(2500, lambda: self.settings_page.check_updates(silent=True))

    def show_config_warning(self, warning):
        """Предупреждение о повреждённом settings.json (п.9 «Что осталось»).

        warning — словарь из config.consume_load_warning() (см. run()):
        {"skip_saving": bool, "corrupt_path": str|None}. Вызывается ровно
        один раз при старте — сам config гарантирует, что второй вызов
        consume_load_warning() в этой сессии вернёт None.
        """
        if warning is None:
            return
        corrupt_path = warning.get("corrupt_path")
        if warning.get("skip_saving"):
            title = "Настройки не сохранятся"
            content = ("Файл настроек повреждён, и восстановить резервную "
                       "копию не удалось. Все изменения в этом запуске "
                       "программы (настройки, история загрузок) пропадут "
                       "при закрытии — перезапустите программу.")
            bar = InfoBar.error(
                title=title, content=content, orient=Qt.Horizontal,
                isClosable=True, position=InfoBarPosition.TOP,
                duration=-1, parent=self,
            )
        else:
            title = "Настройки были повреждены"
            content = ("Файл настроек нельзя было прочитать, поэтому "
                       "использованы значения по умолчанию. Ваши прежние "
                       "настройки и история сохранены в отдельном файле — "
                       "их можно восстановить вручную.")
            bar = InfoBar.warning(
                title=title, content=content, orient=Qt.Horizontal,
                isClosable=True, position=InfoBarPosition.TOP,
                duration=-1, parent=self,
            )
            if corrupt_path:
                folder = os.path.dirname(corrupt_path)
                folder_btn = PushButton("Открыть папку", bar)
                folder_btn.clicked.connect(
                    lambda: os.path.isdir(folder) and QDesktopServices.openUrl(
                        QUrl.fromLocalFile(folder)
                    )
                )
                bar.addWidget(folder_btn)
                copy_btn = PushButton("Скопировать путь", bar)
                copy_btn.clicked.connect(
                    lambda: QGuiApplication.clipboard().setText(corrupt_path)
                )
                bar.addWidget(copy_btn)
        bar.show()

    # ---------- навигация ----------

    def _setup_navigation(self):
        self.addSubInterface(
            self.download_page, FIF.DOWNLOAD, "Загрузка",
            position=NavigationItemPosition.TOP,
        )
        self.addSubInterface(
            self.library_page, FIF.LIBRARY, "Библиотека",
            position=NavigationItemPosition.TOP,
        )
        self.addSubInterface(
            self.settings_page, FIF.SETTING, "Настройки",
            position=NavigationItemPosition.BOTTOM,
        )

        self.navigationInterface.setExpandWidth(200)
        self.navigationInterface.setMenuButtonVisible(True)
        self.navigationInterface.setCollapsible(True)

    def switch_to(self, route_key):
        """Переключиться на страницу (виджет напрямую)."""
        pages = {
            "download": self.download_page,
            "library": self.library_page,
            "settings": self.settings_page,
        }
        widget = pages.get(route_key)
        if widget is not None:
            self.stackedWidget.setCurrentWidget(widget)

    # ---------- темы ----------

    def _apply_theme(self, mode, save=True):
        theme_enum = {
            "light": Theme.LIGHT, "dark": Theme.DARK, "system": Theme.AUTO
        }.get(mode, Theme.AUTO)
        setTheme(theme_enum)

        self.settings["theme"] = mode
        self.settings_page.sync_theme(mode)

        if save:
            config.save(self.settings)

    def _on_default_folder_changed(self, path):
        """Смена папки по умолчанию в настройках — обновить «Загрузку»."""
        if path and self.download_page.dir_edit.currentText() != path:
            DownloadPage._set_combo_dir(self.download_page.dir_edit, path)

    def _on_item_finished(self, item):
        """Сохранить завершённую загрузку в историю (для Библиотеки)."""
        if item.status != STATUS_COMPLETED or not item.files:
            return
        # Плейлист: раньше в историю попадал только первый файл (BUG-14) —
        # остальные видео терялись из Библиотеки. Пишем каждый скачанный файл.
        for path in item.files:
            if not os.path.isfile(path):
                continue
            # 1.0.5: фрагмент помечается в записи истории — Библиотека
            # показывает, что это фрагмент (AC7); путь уникален за счёт
            # суффикса [clip Ns-Ms], записи не вытесняют друг друга.
            fragment = None
            if item.time_range:
                fragment = fmt_timecode(item.time_range[0]) + "-" + \
                    fmt_timecode(item.time_range[1])
            config.add_history(self.settings, {
                "url": item.url,
                "source": source_from_url(item.url),
                "title": item.title or os.path.basename(path),
                "quality": item.quality if item.quality != "best" else "Лучшее",
                "duration": item.duration,
                "mode": item.mode,
                "fragment": fragment,
                "thumbnail": item.thumbnail,
                "path": path,
            })
        config.save(self.settings)

    # ---------- завершение ----------

    def closeEvent(self, event):
        # Остановить фоновые QThread'ы ДО выхода — иначе крах
        # «QThread destroyed while thread is still running».
        # Сначала politely: cancel() у воркеров с отменой, затем wait().
        threads = []
        for attr in ("_analyze_worker", "_thumb_worker", "_preview_worker"):
            thread = getattr(self.download_page, attr, None)
            if thread is not None:
                threads.append(thread)
        update_worker = getattr(self.settings_page, "_update_worker", None)
        if update_worker is not None:
            update_worker.cancel()          # тихая остановка проверок/скачивания
            threads.append(update_worker)
        for thread in threads:
            try:
                # FramePreviewWorker: quit()/wait() тут бессильны — поток
                # блокирован на ffmpeg (Popen), а не в цикле событий. Без
                # явного stop() (kill процесса) выход держался бы до конца
                # таймаута ffmpeg (замечено — до ~17-20с на VK).
                if hasattr(thread, "stop"):
                    thread.stop()
                thread.quit()
                thread.wait(2000)
            except Exception:
                pass
        # Миниатюры Библиотеки: воркеры парентятся к странице и живут
        # дольше карточек — останавливаем их явно
        try:
            self.library_page.stop_workers()
        except Exception:
            pass
        self.settings["window_geometry"] = (
            f"{self.width()}x{self.height()}"
        )
        config.save(self.settings)
        super().closeEvent(event)


class _SingleInstanceSignal(QObject):
    show_requested = Signal()


def run():
    """Точка входа GUI."""
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    # _mutex_handle держим живым до конца процесса — иначе мьютекс
    # освободится досрочно и второй экземпляр решит, что первого нет.
    _mutex_handle, already_running = single_instance.acquire_mutex()
    if already_running:
        if single_instance.send_show_request():
            sys.exit(0)
        # Первый экземпляр не отвечает (завис) — не зависаем сами,
        # показываем собственное окно вместо тихого выхода.

    settings = config.load()
    config_warning = config.consume_load_warning()
    window = MainWindow(settings)

    signal_holder = _SingleInstanceSignal()

    def _bring_to_front():
        window.showNormal()
        window.activateWindow()
        window.raise_()
        single_instance.force_foreground(int(window.winId()))

    signal_holder.show_requested.connect(_bring_to_front)
    single_instance.start_pipe_server(signal_holder.show_requested.emit)

    window.show()
    window.show_config_warning(config_warning)

    geometry = settings.get("window_geometry")
    if geometry:
        try:
            w, h = geometry.split("x")
            window.resize(int(w), int(h))
        except (ValueError, AttributeError):
            pass

    sys.exit(app.exec())
