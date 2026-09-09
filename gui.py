"""UI-слой: PySide6 + qfluentwidgets (Fluent Design).

Страницы: Загрузка / Очередь / Библиотека / Настройки.
Логика загрузок (downloader.py) — стабильное API, вызывается отсюда.

Точки интеграции с менеджером:
    Bridge (QObject) принимает колбэки из фоновых потоков и через
    сигналы Qt (auto-connection = queued) безопасно передаёт их в GUI.
"""

import os
import subprocess
import sys
import threading
import urllib.request

from PySide6.QtCore import (
    QObject,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel
from PySide6.QtWidgets import QVBoxLayout, QWidget, QSizePolicy

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    EditableComboBox,
    FluentIconBase,
    IconWidget,
    IndeterminateProgressRing,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    RoundMenu,
    ScrollArea,
    SearchLineEdit,
    SegmentedWidget,
    SettingCard,
    SettingCardGroup,
    SpinBox,
    StateToolTip,
    StrongBodyLabel,
    SubtitleLabel,
    Theme,
    TitleLabel,
    ToggleToolButton,
    ToolButton,
    FluentWindow,
    NavigationItemPosition,
    FluentIcon as FIF,
    isDarkTheme,
    setTheme,
    setThemeColor,
    themeColor,
)

import config
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
    fetch_info,
    fmt_eta,
    fmt_speed,
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

MEDIA_EXTS = {".mp4", ".mkv", ".webm", ".mp3", ".m4a"}


def fmt_duration(seconds):
    if not seconds:
        return ""
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


# ================= МОСТ: фоновые потоки → GUI =================

class Bridge(QObject):
    """Передаёт события из потоков Python в GUI через сигналы Qt."""

    itemChanged = Signal(object)
    queueChanged = Signal()
    analyzeDone = Signal(object)
    analyzeFailed = Signal(str)


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


class UpdateWorker(QThread):
    """Проверка/скачивание обновления в фоне (по образцу AnalyzeWorker).

    mode='check': fetch_manifest + is_newer.
    mode='download': download_file с прогрессом и отменой + sha256.
    """

    manifestReady = Signal(object)   # manifest или None
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
        except Exception as exc:
            if self.mode == "download":
                self.downloadFailed.emit(str(exc))
            else:
                self.manifestReady.emit(None)


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

        bridge.analyzeDone.connect(self._on_analyze_ok)
        bridge.analyzeFailed.connect(self._on_analyze_fail)
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
        quality = "best"
        if mode == "video" and self.preview_info:
            heights = self.preview_info.get("video_qualities") or []
            index = self.quality_combo.currentIndex()
            if index == 0:
                quality = "best"
            elif 0 < index <= len(heights):
                quality = str(heights[index - 1])
            else:
                # fallback: парсим подпись
                label = self.quality_combo.currentText()
                digits = "".join(ch for ch in label if ch.isdigit())
                quality = digits if digits else "best"

        self.manager.add(
            url, mode=mode, quality=quality, output_dir=output_dir,
            playlist=self.playlist_check.isChecked(),
        )
        self._reset_preview()
        self.url_edit.clear()
        self._rebuild_current()

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
        lay_acts = self.actions_widget
        lay.addLayout(head)

        self.bar = ProgressBar(self)
        self.bar.setRange(0, 100)
        self.bar.hide()
        lay.addWidget(self.bar)

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

        parts = [status_text]
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

        self._clear_actions()
        if item.status in (STATUS_DOWNLOADING, STATUS_QUEUED,
                           STATUS_ANALYZING):
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
    if "vk.com" in host or "vkvideo" in host:
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

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self.entry = entry

        lay = QHBoxLayout(self)
        lay.setContentsMargins(SP_GROUP * 2, SP_GROUP * 2,
                               SP_GROUP * 2, SP_GROUP * 2)
        lay.setSpacing(SP_GROUP * 2)

        # Миниатюра (фрагмент видео) слева
        self.thumb = ImageLabel(self)
        self.thumb.setFixedSize(120, 68)
        if entry.get("thumbnail"):
            worker = LibraryThumbWorker(
                entry["thumbnail"], (120, 68), self
            )
            worker.loaded.connect(self._on_thumb)
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
            ("format", entry.get("format")),
            ("source", entry.get("source")),
        ):
            if value:
                label = {"quality": "Качество", "size": "Размер",
                         "duration": "Длительность", "format": "Формат",
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


class LibraryPage(TransparentScrollArea):
    def __init__(self, bridge, manager, settings, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.manager = manager
        self.settings = settings

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

        self.vbox.addLayout(filters)

        self.empty_label = BodyLabel("Здесь появятся завершённые загрузки")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.vbox.addWidget(self.empty_label, stretch=1)

        self.vbox.addStretch(1)
        self.setWidget(inner)

    def _entries(self):
        """История + файлы из папки, которых нет в истории."""
        import config as cfg

        entries = []
        seen_paths = set()

        for e in cfg.get_history(self.settings):
            entries.append(e)
            seen_paths.add(os.path.normcase(e.get("path", "")))

        folder = self.settings.get("default_folder")
        if folder and os.path.isdir(folder):
            try:
                names = os.listdir(folder)
            except OSError:
                names = []
            for name in sorted(names):
                path = os.path.join(folder, name)
                if not os.path.isfile(path):
                    continue
                if os.path.splitext(name)[1].lower() not in MEDIA_EXTS:
                    continue
                if os.path.normcase(path) in seen_paths:
                    continue
                ext = os.path.splitext(name)[1].upper().lstrip(".")
                entries.append({
                    "path": path,
                    "title": os.path.splitext(name)[0],
                    "format": ext,
                    "source": "Папка загрузок",
                })

        return entries

    def refresh(self):
        # Очистить карточки (всё после фильтров: empty_label + stretch)
        while self.vbox.count() > 3:
            item = self.vbox.takeAt(3)
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
            self.vbox.insertWidget(self.vbox.count() - 1, card)

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

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()


# ================= СТРАНИЦА: НАСТРОЙКИ =================

class SettingsPage(TransparentScrollArea):
    themeChanged = Signal(str)
    folderChanged = Signal(str)

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
        self._update_worker.start()

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

        # Запись завершённых загрузок в историю
        self.bridge.itemChanged.connect(self._on_item_finished)

        self.navigationInterface.itemSelectIfNoneInit = True

        # Тихая автопроверка обновлений при старте (отложенная, 2.5с):
        # оффлайн / нет обновлений — ничего не показываем.
        if self.settings.get("check_updates", True):
            QTimer.singleShot(2500, lambda: self.settings_page.check_updates(silent=True))

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
        path = item.files[0]
        if not os.path.isfile(path):
            return
        config.add_history(self.settings, {
            "url": item.url,
            "source": source_from_url(item.url),
            "title": item.title or os.path.basename(path),
            "quality": item.quality if item.quality != "best" else "Лучшее",
            "duration": item.duration,
            "mode": item.mode,
            "thumbnail": item.thumbnail,
            "path": path,
        })
        config.save(self.settings)

    # ---------- завершение ----------

    def closeEvent(self, event):
        # Остановить фоновые QThread'ы страницы ДО выхода — иначе крах
        for attr in ("_analyze_worker", "_thumb_worker"):
            thread = getattr(self.download_page, attr, None)
            if thread is not None:
                try:
                    thread.quit()
                    thread.wait(2000)
                except Exception:
                    pass
        self.settings["window_geometry"] = (
            f"{self.width()}x{self.height()}"
        )
        config.save(self.settings)
        super().closeEvent(event)


def run():
    """Точка входа GUI."""
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    settings = config.load()
    window = MainWindow(settings)
    window.show()

    geometry = settings.get("window_geometry")
    if geometry:
        try:
            w, h = geometry.split("x")
            window.resize(int(w), int(h))
        except (ValueError, AttributeError):
            pass

    sys.exit(app.exec())
