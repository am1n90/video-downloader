"""Режим «Torrent» — страницы (торрент-стриминг, Этапы 1-2).

Страница «Торренты» поверх torrent_engine: добавление magnet/.torrent,
выбор файлов раздачи, карточки очереди, пауза/продолжение/удаление
(1.3) и «Смотреть» — просмотр во время закачки во внешнем плеере
(2.1: torrent_stream + player). В 2.2 к просмотру добавились диалог
«что смотреть» для раздач с несколькими видеофайлами и индикатор
подготовки плеера. «Библиотека» режима — заглушка.

Импортируется из gui.MainWindow.__init__, а не с верхнего уровня gui.py:
модуль сам берёт общие виджеты и отступы из gui.

Колбэки движка приходят ИЗ ФОНОВОГО ПОТОКА — страница получает их только
через сигналы Bridge (тот же приём, что в режиме Video Downloader).
"""

import os
import time

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, CheckBox,
                            InfoBar, InfoBarPosition, LineEdit, MessageBoxBase,
                            PrimaryPushButton, ProgressBar, PushButton,
                            StrongBodyLabel, SubtitleLabel, TitleLabel)

import player
import torrent_engine as te
import torrent_stream as ts
from downloader import fmt_eta, fmt_speed
from gui import SP_BLOCK, SP_GROUP, SP_WINDOW, TransparentScrollArea, fmt_mb

STATE_TEXT = {
    te.STATE_METADATA: "Получение сведений о раздаче…",
    te.STATE_CHECKING: "Проверка файлов…",
    te.STATE_DOWNLOADING: "Скачивается",
    te.STATE_SEEDING: "Раздаётся",
    te.STATE_FINISHED: "Готово",
    te.STATE_PAUSED: "Пауза",
    te.STATE_ERROR: "Ошибка",
}

# Состояния, в которых раздача ещё «в работе» (есть что ставить на паузу)
ACTIVE_STATES = (te.STATE_METADATA, te.STATE_CHECKING, te.STATE_DOWNLOADING)
DONE_STATES = (te.STATE_SEEDING, te.STATE_FINISHED)

PRIORITY_ON = 4      # обычный приоритет; 0 — файл не качать

# Подготовка плеера (2.2). Между запуском плеера и первым его
# обращением к нашему серверу бывает ~20 с: Защитник проверяет файлы
# плеера при первом запуске после установки (находка 11 Этапа 0.2).
# Кнопка «Смотреть» без отклика в это время выглядит как «ничего не
# произошло», поэтому показываем подготовку, а через PREPARE_HINT_S —
# и её причину.
PREPARE_TICK_MS = 500
PREPARE_HINT_S = 8
PREPARE_TIMEOUT_S = 45

WATCH_STARTING = "starting"   # плеер запущен, к серверу ещё не обращался
WATCH_READY = "ready"         # первый запрос пришёл — плеер читает поток
WATCH_SILENT = "silent"       # не отозвался: сказать честно, поток не рвать


def fmt_size(size):
    """Размер файла раздачи.

    fmt_mb округляет до целых мегабайт, и мелкие файлы (субтитры, nfo)
    показывались как «0 МБ» — ниже мегабайта считаем в килобайтах.
    """
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} КБ"
    return fmt_mb(size)


class _PlaceholderPage(TransparentScrollArea):
    """Заголовок + пояснение, что появится на странице."""

    def __init__(self, title, text, parent=None):
        super().__init__(parent)
        self.setViewportMargins(SP_WINDOW, SP_WINDOW, SP_WINDOW, SP_WINDOW)
        self.setWidgetResizable(True)

        inner = QWidget()
        inner.setObjectName("scrollInner")
        vbox = QVBoxLayout(inner)
        vbox.setSpacing(SP_BLOCK)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.addWidget(TitleLabel(title))
        self.placeholder_label = BodyLabel(text)
        self.placeholder_label.setWordWrap(True)
        vbox.addWidget(self.placeholder_label)
        vbox.addStretch(1)
        self.setWidget(inner)


def _build_folders(tree, files):
    """Построить папки раздачи в дереве.

    Общая часть двух деревьев — «что качать» (TorrentFilesDialog) и «что
    смотреть» (WatchFileDialog): разбор пути по разделителям и узлы
    папок. Листья каждый диалог делает свои: у первого галочки и сумма
    выбранного, у второго одиночный выбор и «сколько скачано», — общего
    там нет, а иерархия одна и та же.

    Возвращает ([(родитель, имя файла)] по порядку files, [узлы папок]).
    """
    folders = {}
    parents = []
    for file in files:
        parts = file.path.replace("\\", "/").split("/")
        parent = tree.invisibleRootItem()
        for depth in range(len(parts) - 1):
            key = tuple(parts[:depth + 1])
            node = folders.get(key)
            if node is None:
                node = QTreeWidgetItem(parent, [parts[depth], ""])
                folders[key] = node
            parent = node
        parents.append((parent, parts[-1]))
    return parents, list(folders.values())


class TorrentFilesDialog(MessageBoxBase):
    """Дерево файлов раздачи с галочками.

    Отдаёт приоритеты списком по индексам файлов (0 — не качать, 4 —
    качать): движок ждёт в set_files именно такой список. Показывает сумму
    ВЫБРАННЫХ файлов, а не wanted_size: последний libtorrent считает по
    целым кускам, и для пользователя он выглядит странно (находка 1.2).
    """

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self._files = list(item.files)
        self._leaves = {}          # индекс файла -> лист дерева
        self._guard = False        # защита от рекурсии в itemChanged

        self.viewLayout.addWidget(SubtitleLabel("Файлы раздачи", self))
        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Файл", "Размер"])
        self.tree.setColumnWidth(0, 380)
        self.tree.setMinimumSize(560, 320)
        self._build_tree()
        self.tree.expandAll()
        self.tree.itemChanged.connect(self._on_item_changed)
        self.viewLayout.addWidget(self.tree)

        self.size_label = BodyLabel("", self)
        self.viewLayout.addWidget(self.size_label)

        self.yesButton.setText("Применить")
        self.cancelButton.setText("Отмена")
        self._update_size()

    def _build_tree(self):
        self._guard = True
        try:
            parents, folders = _build_folders(self.tree, self._files)
            for node in folders:
                node.setFlags(node.flags() | Qt.ItemIsUserCheckable)
                node.setCheckState(0, Qt.Unchecked)
            for file, (parent, name) in zip(self._files, parents):
                leaf = QTreeWidgetItem(parent, [name, fmt_size(file.size)])
                leaf.setFlags(leaf.flags() | Qt.ItemIsUserCheckable)
                leaf.setData(0, Qt.UserRole, file.index)
                leaf.setCheckState(
                    0, Qt.Checked if file.priority > 0 else Qt.Unchecked)
                self._leaves[file.index] = leaf
            for leaf in self._leaves.values():
                self._refresh_parents(leaf.parent())
        finally:
            self._guard = False

    def _on_item_changed(self, node, column):
        if self._guard or column != 0:
            return
        self._guard = True
        try:
            self._set_subtree(node, node.checkState(0))
            self._refresh_parents(node.parent())
        finally:
            self._guard = False
        self._update_size()

    def _set_subtree(self, node, state):
        for i in range(node.childCount()):
            child = node.child(i)
            child.setCheckState(0, state)
            self._set_subtree(child, state)

    def _refresh_parents(self, node):
        while node is not None:
            states = {node.child(i).checkState(0)
                      for i in range(node.childCount())}
            if states == {Qt.Checked}:
                node.setCheckState(0, Qt.Checked)
            elif states == {Qt.Unchecked}:
                node.setCheckState(0, Qt.Unchecked)
            else:
                node.setCheckState(0, Qt.PartiallyChecked)
            node = node.parent()

    def priorities(self):
        return [PRIORITY_ON
                if self._leaves[f.index].checkState(0) == Qt.Checked else 0
                for f in self._files]

    def selected_size(self):
        return sum(f.size for f, p in zip(self._files, self.priorities()) if p)

    def _update_size(self):
        total = sum(f.size for f in self._files)
        selected = self.selected_size()
        self.size_label.setText(
            f"Выбрано: {fmt_size(selected)} из {fmt_size(total)}")
        # Снять все галочки — значит не качать ничего: раздача просто
        # встанет, поэтому «Применить» в этом случае недоступна
        self.yesButton.setEnabled(selected > 0)


class WatchFileDialog(MessageBoxBase):
    """Какой файл раздачи смотреть (2.2, многофайловые раздачи).

    То же дерево, что у выбора «что качать» (общий _build_folders), но
    выбор ОДИНОЧНЫЙ: смотрят один файл за раз, и галочки с частично
    отмеченными папками здесь только мешали бы. Невидеофайлы и папки
    показываем — так видно устройство раздачи, — но выбрать нельзя.

    Предвыбран самый большой видеофайл: это правило 2.1, и для раздачи
    «фильм + трейлер» Enter сразу даёт то, что нужно.
    """

    def __init__(self, item, targets, progress=(), parent=None):
        super().__init__(parent)
        self._files = list(item.files)
        self._targets = {f.index: f for f in targets}
        self._nodes = {}            # индекс файла -> лист дерева
        self._chosen = None

        self.viewLayout.addWidget(SubtitleLabel("Что смотреть", self))
        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Файл", "Размер", "Скачано"])
        self.tree.setColumnWidth(0, 340)
        self.tree.setColumnWidth(1, 100)
        self.tree.setMinimumSize(560, 320)
        self.tree.setRootIsDecorated(True)
        self._build_tree(progress)
        self.tree.expandAll()
        self.tree.itemSelectionChanged.connect(self._on_selection)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.viewLayout.addWidget(self.tree)

        self.hint_label = CaptionLabel(
            "Файл можно смотреть, не дожидаясь конца закачки — "
            "нужные куски программа запросит первыми", self)
        self.hint_label.setWordWrap(True)
        self.viewLayout.addWidget(self.hint_label)

        self.yesButton.setText("Смотреть")
        self.cancelButton.setText("Отмена")
        self._preselect(targets)

    def _build_tree(self, progress):
        parents, _folders = _build_folders(self.tree, self._files)
        for file, (parent, name) in zip(self._files, parents):
            done = progress[file.index] if file.index < len(progress) else 0
            share = ""
            if file.index in self._targets and file.size:
                share = ("скачан" if done >= file.size
                         else f"{done * 100 // file.size}%")
            node = QTreeWidgetItem(parent, [name, fmt_size(file.size), share])
            if file.index in self._targets:
                node.setData(0, Qt.UserRole, file.index)
                self._nodes[file.index] = node
            else:
                # Не видео или снята галочка «качать» — показываем, но
                # выбрать нельзя: смотреть там нечего
                node.setDisabled(True)

    def _preselect(self, targets):
        biggest = max(targets, key=lambda f: f.size) if targets else None
        if biggest is not None:
            node = self._nodes.get(biggest.index)
            if node is not None:
                self.tree.setCurrentItem(node)
        self._on_selection()

    def _current_file(self):
        node = self.tree.currentItem()
        if node is None or node.isDisabled():
            return None
        index = node.data(0, Qt.UserRole)
        return self._targets.get(index)

    def select(self, index):
        """Выбрать файл по индексу — для offscreen-тестов и живых
        проверок: мышью в них никто не кликает."""
        node = self._nodes.get(index)
        if node is None:
            return False
        self.tree.setCurrentItem(node)
        self._on_selection()
        return True

    def _on_selection(self):
        self.yesButton.setEnabled(self._current_file() is not None)

    def _on_double_click(self, node, column):
        if node is not None and not node.isDisabled():
            self.accept()

    def chosen(self):
        """Выбранный файл или None (диалог закрыли/отменили)."""
        return self._chosen

    def exec(self):
        ok = super().exec()
        self._chosen = self._current_file() if ok else None
        return ok


class ConfirmRemoveTorrentDialog(MessageBoxBase):
    """«Убрать раздачу?» с галочкой «Удалить также файлы с диска».

    По образцу ConfirmDeleteDialog Библиотеки: галочка выключена по
    умолчанию, файлы удаляются навсегда — под галочкой предупреждение.
    Служебный .parts движок убирает сам.
    """

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.delete_files = False

        self.viewLayout.addWidget(SubtitleLabel("Убрать раздачу?", self))
        self.viewLayout.addWidget(BodyLabel(name, self))
        self.files_check = CheckBox("Удалить также файлы с диска", self)
        self.files_check.setChecked(False)
        self.files_check.stateChanged.connect(lambda: self._on_files_check())
        self.viewLayout.addWidget(self.files_check)
        self.warn_label = CaptionLabel("Файлы будут удалены навсегда", self)
        self.warn_label.setVisible(False)
        self.viewLayout.addWidget(self.warn_label)

        self.yesButton.setText("Убрать")
        self.cancelButton.setText("Отмена")

    def _on_files_check(self):
        self.delete_files = self.files_check.isChecked()
        self.warn_label.setVisible(self.delete_files)


class TorrentCard(CardWidget):
    """Карточка раздачи.

    Образец — QueueCard режима Video Downloader: кнопки пересоздаются
    только при СМЕНЕ состояния, на тиках прогресса обновляются лишь бар и
    подпись (движок шлёт снимки дважды в секунду).
    """

    def __init__(self, item, page, parent=None):
        super().__init__(parent)
        self.item = item
        self.page = page

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP_GROUP * 2, SP_GROUP * 2,
                               SP_GROUP * 2, SP_GROUP * 2)
        lay.setSpacing(SP_GROUP)

        head = QHBoxLayout()
        head.setSpacing(SP_GROUP * 2)
        text_lay = QVBoxLayout()
        text_lay.setSpacing(4)
        self.title_label = StrongBodyLabel(self._title(item), self)
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
        head.addWidget(self.actions_widget)
        lay.addLayout(head)

        self.bar = ProgressBar(self)
        self.bar.setRange(0, 100)
        self.bar.hide()
        lay.addWidget(self.bar)

        self._last_state = None
        self._last_watching = None
        self.update_state(item)

    @staticmethod
    def _title(item):
        return item.name or item.id[:12]

    def _clear_actions(self):
        """Убрать прежние кнопки.

        setParent(None) обязателен: takeAt() вынимает виджет из
        РАСКЛАДКИ, но он остаётся ребёнком actions_widget и продолжает
        рисоваться на прежнем месте, пока не отработает deleteLater, —
        а события отложенного удаления разбирает только цикл событий,
        не processEvents(). Видно глазами на живой проверке 2.1:
        «Смотреть» поверх «Остановить просмотр» и два комплекта
        «Пауза/Файлы/Удалить» (снимок 16.09.2026).
        """
        layout = self.actions_widget.layout()
        while layout.count():
            widget = layout.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _add_action(self, text, slot, accent=False):
        btn = PrimaryPushButton(text, self.actions_widget) if accent else \
            PushButton(text, self.actions_widget)
        btn.clicked.connect(slot)
        self.actions_widget.layout().addWidget(btn)

    def update_state(self, item):
        self.item = item
        self.title_label.setText(self._title(item))
        self.meta_label.setText("  •  ".join(self._meta_parts(item)))

        if item.state in DONE_STATES:
            self.bar.show()
            self.bar.setValue(100)
        elif item.has_metadata and item.state != te.STATE_ERROR:
            self.bar.show()
            self.bar.setValue(int(item.progress * 100))
        else:
            self.bar.hide()

        # Кнопки пересоздаём только при смене состояния, не на каждом тике
        # (просмотр — тоже смена набора кнопок, хотя state тот же)
        watching = self.page.is_watching(item.id)
        if item.state == self._last_state and watching == self._last_watching:
            return
        self._last_state = item.state
        self._last_watching = watching
        self._clear_actions()
        tid = item.id
        if watching:
            self._add_action("Остановить просмотр",
                             lambda: self.page.stop_watch())
        elif self.page.can_watch(item):
            # Акцент не ставим там, где он уже занят «Продолжить»/«Повторить»
            self._add_action("Смотреть", lambda: self.page.watch(tid),
                             accent=item.state not in (te.STATE_PAUSED,
                                                       te.STATE_ERROR))
        if item.state in ACTIVE_STATES:
            self._add_action("Пауза", lambda: self.page.pause(tid))
        elif item.state == te.STATE_PAUSED:
            self._add_action("Продолжить",
                             lambda: self.page.resume(tid), accent=True)
        elif item.state == te.STATE_ERROR:
            # Без retry() libtorrent молчит 10 минут (находка 2)
            self._add_action("Повторить",
                             lambda: self.page.retry(tid), accent=True)
        elif item.state in DONE_STATES:
            self._add_action("Открыть папку",
                             lambda: self.page.open_folder(tid))
        if item.has_metadata:
            self._add_action("Файлы", lambda: self.page.choose_files(tid))
        self._add_action("Удалить", lambda: self.page.remove(tid))

    def _meta_parts(self, item):
        parts = [STATE_TEXT.get(item.state, item.state)]
        watch = self.page.watch_status(item.id)
        if watch:
            parts.append(watch)
        if item.state == te.STATE_ERROR:
            if item.error:
                parts.append(item.error[:120])
            if item.error_file:
                parts.append(os.path.basename(item.error_file))
            return parts
        if not item.has_metadata:
            return parts

        parts.append(f"{item.progress * 100:.0f}%")
        if item.selected_size:
            # wanted_done считается по целым кускам и у готовой раздачи
            # бывает больше суммы выбранных файлов — не пугаем «6.3 из 6.0»
            done = min(item.wanted_done, item.selected_size)
            parts.append(
                f"{fmt_size(done)} из {fmt_size(item.selected_size)}")
        if item.download_rate:
            parts.append(fmt_speed(item.download_rate))
            remaining = item.wanted_size - item.wanted_done
            if item.state == te.STATE_DOWNLOADING and remaining > 0:
                parts.append(
                    f"осталось {fmt_eta(remaining / item.download_rate)}")
        if item.state == te.STATE_SEEDING:
            parts.append(f"раздача {fmt_speed(item.upload_rate)}")
        if item.num_peers:
            parts.append(f"пиров: {item.num_peers}")
        return parts


class TorrentPage(TransparentScrollArea):
    """Очередь раздач: добавление, выбор файлов, пауза, удаление."""

    def __init__(self, engine, bridge, settings, parent=None, stream=None):
        super().__init__(parent)
        self.engine = engine
        self.settings = settings
        # Сервис просмотра создаёт MainWindow (он же его и закрывает в
        # closeEvent). Если его не передали — поднимем свой при первом
        # «Смотреть»: сервер всё равно ленивый, порт заранее не занимаем.
        self._stream = stream
        self._player_offered = False
        self._cards = {}
        # Подготовка плеера: состояние, момент запуска и сам процесс —
        # если он закроется, не открыв поток, ждать 45 с незачем
        self._watch_state = ""
        self._watch_started = 0.0
        self._watch_url = ""
        self._hint_shown = False
        self._player_proc = None
        self._prepare_timer = QTimer(self)
        self._prepare_timer.setInterval(PREPARE_TICK_MS)
        self._prepare_timer.timeout.connect(self._poll_player)

        self.setViewportMargins(SP_WINDOW, SP_WINDOW, SP_WINDOW, SP_WINDOW)
        self.setWidgetResizable(True)

        inner = QWidget()
        inner.setObjectName("scrollInner")
        self.vbox = QVBoxLayout(inner)
        self.vbox.setSpacing(SP_BLOCK)
        self.vbox.setContentsMargins(0, 0, 0, 0)
        self.vbox.addWidget(TitleLabel("Торренты"))

        add_row = QHBoxLayout()
        add_row.setSpacing(SP_GROUP)
        self.magnet_edit = LineEdit(inner)
        self.magnet_edit.setPlaceholderText("magnet:?xt=urn:btih:…")
        self.magnet_edit.setClearButtonEnabled(True)
        self.magnet_edit.returnPressed.connect(lambda: self.add_magnet())
        add_row.addWidget(self.magnet_edit, stretch=1)
        self.add_btn = PrimaryPushButton("Добавить", inner)
        self.add_btn.clicked.connect(lambda: self.add_magnet())
        add_row.addWidget(self.add_btn)
        self.file_btn = PushButton("Открыть .torrent…", inner)
        self.file_btn.clicked.connect(lambda: self.add_torrent_file())
        add_row.addWidget(self.file_btn)
        self.vbox.addLayout(add_row)

        self.empty_label = BodyLabel(
            "Здесь появятся раздачи: вставьте magnet-ссылку "
            "или откройте .torrent-файл")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.vbox.addWidget(self.empty_label, stretch=1)

        self.rows_container = QWidget(inner)
        self.rows_vbox = QVBoxLayout(self.rows_container)
        self.rows_vbox.setContentsMargins(0, 0, 0, 0)
        self.rows_vbox.setSpacing(SP_GROUP)
        self.vbox.addWidget(self.rows_container)

        self.vbox.addStretch(1)
        self.setWidget(inner)

        bridge.itemChanged.connect(self._on_item_changed)
        bridge.queueChanged.connect(self.refresh)

    # ------------------------------------------------------------ жизнь

    def showEvent(self, event):
        """Движок поднимаем лениво — при первом показе страницы.

        Сессия libtorrent занимает порт и вызывает запрос брандмауэра;
        тем, кто торрентами не пользуется, это ни к чему. start()
        идемпотентен, повторный показ ничего не создаёт заново.
        """
        super().showEvent(event)
        if not self.start_engine():
            return
        self.refresh()

    def start_engine(self):
        try:
            self.engine.start()
        except Exception as exc:
            self._notify("warning", f"Торрент-движок не запустился: {exc}")
            return False
        return True

    # --------------------------------------------------------- действия

    def add_magnet(self, uri=None):
        uri = (uri or self.magnet_edit.text()).strip()
        if not uri:
            self._notify("warning", "Вставьте magnet-ссылку")
            return False
        if self._add(lambda: self.engine.add_magnet(uri, self.save_path())):
            self.magnet_edit.clear()
            return True
        return False

    def add_torrent_file(self, path=None):
        if not path:
            from PySide6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(
                self, "Выберите .torrent", self.save_path(),
                "Торренты (*.torrent)")
        if not path:
            return False
        return self._add(
            lambda: self.engine.add_torrent_file(path, self.save_path()))

    def _add(self, call):
        if not self.start_engine():
            return False
        try:
            call()
        except Exception as exc:
            self._notify("warning", f"Не удалось добавить раздачу: {exc}")
            return False
        self.refresh()
        return True

    def pause(self, tid):
        self._engine_call(lambda: self.engine.pause(tid))

    def resume(self, tid):
        self._engine_call(lambda: self.engine.resume(tid))

    def retry(self, tid):
        self._engine_call(lambda: self.engine.retry(tid))

    def remove(self, tid):
        item = self.engine.get(tid)
        if item is None:
            return False
        ok, delete_files = self.confirm_remove(TorrentCard._title(item))
        if not ok:
            return False
        if not self._engine_call(
                lambda: self.engine.remove(tid, delete_files=delete_files)):
            return False
        self.refresh()
        return True

    def choose_files(self, tid):
        item = self.engine.get(tid)
        if item is None or not item.files:
            return False
        priorities = self.ask_files(item)
        if priorities is None:
            return False
        # prioritize_files асинхронный: сразу после set_files снимок ещё
        # показывает старый выбор (находка 21) — карточку обновит движок
        return self._engine_call(
            lambda: self.engine.set_files(tid, priorities))

    def open_folder(self, tid):
        item = self.engine.get(tid)
        if item is not None and os.path.isdir(item.save_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(item.save_path))

    # ------------------------------------------------------- просмотр 2.1

    def stream(self):
        if self._stream is None:
            self._stream = ts.StreamService(self.engine)
        return self._stream

    def is_watching(self, tid):
        return self._stream is not None and self._stream.is_watching(tid)

    @staticmethod
    def can_watch(item):
        """Есть что смотреть: метаданные получены, раздача не в разборе
        и среди выбранных файлов есть видео."""
        if not item.has_metadata or item.state in (te.STATE_METADATA,
                                                   te.STATE_CHECKING,
                                                   te.STATE_ERROR):
            return False
        return ts.choose_video_file(item.files) is not None

    def watch(self, tid):
        """Открыть видео раздачи во внешнем плеере.

        Видео одно — открываем сразу (клик в один шаг для обычного
        фильма); два и более (сериал) — спрашиваем диалогом (2.2).
        Скачанный целиком файл открываем напрямую: HTTP-сервер и кэш
        кусков для него не нужны.

        С 2.5 выбор серии здесь ещё и переключает на неё закачку
        (engine.focus_file): раньше у сериала качались все серии сразу.
        """
        item = self.engine.get(tid)
        if item is None:
            return False
        targets = ts.watchable_files(item.files)
        if not targets:
            self._notify("warning", "В раздаче нет видеофайла для просмотра")
            return False
        if len(targets) == 1:
            target = targets[0]
        else:
            target = self.ask_watch_file(item, targets)
            if target is None:
                return False
        local = self.local_path(item, target)
        if local:
            self._focus(tid, target.index)
            return self._launch(local, os.path.basename(local), url="")
        try:
            url = self.stream().watch(tid, target.index)
        except Exception as exc:
            self._notify("warning", f"Не удалось начать просмотр: {exc}")
            return False
        # ПОСЛЕ watch: он поднимает и потоки субтитров-спутников, а
        # focus_file бережёт файлы именно с открытым потоком
        self._focus(tid, target.index)
        subtitles = self.stream().subtitles
        started = self._launch(url, os.path.basename(target.path), url=url,
                               subtitles=subtitles)
        if started:
            self._begin_prepare(url)
        elif not self._player_offered:
            self.stop_watch()        # плеер не запустился — поток не нужен
        self.refresh()
        return started

    def _focus(self, tid, index):
        """Качать только выбранную серию (2.5). Не вышло — не беда:
        просмотр важнее, раздача просто продолжит качаться целиком."""
        try:
            self.engine.focus_file(tid, index)
        except Exception as exc:
            te.log.warning("focus_file %s#%s: %r", tid, index, exc)

    def stop_watch(self):
        if self._stream is None or not self._stream.stop():
            return False
        self._end_prepare()
        self.refresh()
        return True

    # ------------------------------------------------ подготовка плеера

    def _begin_prepare(self, url):
        """Ждать, пока плеер обратится к потоку (см. PREPARE_* выше)."""
        self._watch_state = WATCH_STARTING
        self._watch_started = time.monotonic()
        self._watch_url = url
        self._hint_shown = False
        self._prepare_timer.start()

    def _end_prepare(self):
        self._prepare_timer.stop()
        self._watch_state = ""
        self._watch_url = ""
        self._player_proc = None

    def _poll_player(self):
        """Тик подготовки. Отдельный таймер, а не тики движка: те идут,
        только пока раздача качается, а ждать плеер приходится и у
        готовой раздачи."""
        stats = self._stream.stats() if self._stream is not None else None
        if stats is None:                   # просмотр уже сняли
            self._end_prepare()
        elif stats.requests:
            self._watch_state = WATCH_READY
            self._prepare_timer.stop()
        elif self._player_proc is not None and self._player_proc.poll() \
                is not None:
            self._watch_state = WATCH_SILENT
            self._prepare_timer.stop()
            self._notify("warning", "Плеер закрылся, не открыв поток. "
                                    "Проверьте плеер в Настройках")
        elif time.monotonic() - self._watch_started >= PREPARE_TIMEOUT_S:
            self._watch_state = WATCH_SILENT
            self._prepare_timer.stop()
            # Поток не рвём: ссылка рабочая, её можно открыть чем угодно
            self._copy_link(self._watch_url)
            self._notify(
                "warning",
                f"Плеер не обратился к потоку за {PREPARE_TIMEOUT_S} с. "
                "Проверьте плеер в Настройках; ссылка на просмотр "
                "скопирована в буфер обмена")
        elif not self._hint_shown \
                and time.monotonic() - self._watch_started >= PREPARE_HINT_S:
            # Один раз объясняем, почему плеер молчит: разовая задержка
            # после установки, а не зависшая кнопка (находка 11)
            self._hint_shown = True
            self._notify("info",
                         "Плеер ещё открывается. Первый запуск после "
                         "установки бывает долгим: Windows проверяет его "
                         "файлы. Просмотр начнётся сам")
        self.refresh()

    def watch_status(self, tid):
        """Подпись просмотра для карточки; "" — просмотра нет."""
        if not self.is_watching(tid):
            return ""
        if self._watch_state == WATCH_SILENT:
            return "плеер не отозвался"
        if self._watch_state != WATCH_STARTING:
            return "идёт просмотр"
        waiting = time.monotonic() - self._watch_started
        if waiting < PREPARE_HINT_S:
            return "запускаем плеер…"
        # Причина задержки — в отдельном сообщении (_poll_player), иначе
        # подпись карточки разрасталась бы на три строки
        return f"готовим плеер… {waiting:.0f} с"

    def local_path(self, item, target):
        """Путь к файлу, если он уже скачан ЦЕЛИКОМ, иначе ""."""
        try:
            done = self.engine.file_progress(item.id)
        except Exception:
            return ""
        if target.index >= len(done) or done[target.index] < target.size:
            return ""
        # libtorrent отдаёт путь внутри раздачи с разделителем платформы —
        # приводим оба варианта, как в дереве файлов
        parts = target.path.replace("\\", "/").split("/")
        path = os.path.join(item.save_path, *parts)
        return path if os.path.isfile(path) else ""

    def _launch(self, target, name, url="", subtitles=()):
        """Запуск плеера. Процесс не ждём и при закрытии окна не убиваем."""
        self._player_offered = False
        configured = self.settings.get("torrent_player", "")
        try:
            exe = player.resolve(configured)
        except player.PlayerNotFound as exc:
            self._offer_link(str(exc), url)
            return False
        urls = [sub_url for _, sub_url in subtitles]
        try:
            self._player_proc = player.launch(target, exe, subtitles=urls)
        except OSError as exc:
            self._notify("warning", f"Плеер не запустился: {exc}")
            return False
        text = (f"Открываем «{name}» в {player.label_for(exe)}. "
                f"Первый кадр появится через несколько секунд.")
        if urls:
            shown = len(player.subtitle_args(exe, urls))
            if shown:
                text += (f" Подключаем субтитры: "
                         f"{', '.join(n for n, _ in subtitles[:shown])}.")
        self._notify("success", text)
        return True

    def _offer_link(self, reason, url):
        """Плеера нет: ссылку не теряем — кладём в буфер обмена, поток
        продолжает работать, её можно открыть чем угодно."""
        self._player_offered = bool(url)
        text = f"{reason}. Укажите плеер в Настройках"
        if self._copy_link(url):
            text += ". Ссылка на просмотр скопирована в буфер обмена"
        self._notify("warning", text)

    @staticmethod
    def _copy_link(url):
        if not url:
            return False
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(url)
        return True

    def _engine_call(self, call):
        try:
            call()
        except Exception as exc:
            self._notify("warning", f"Не получилось: {exc}")
            return False
        return True

    # ----------------------------------------------- диалоги и вывод
    # Вынесены отдельными методами, чтобы подменяться в offscreen-тестах
    # (тот же приём, что у Библиотеки: _confirm_delete / _notify)

    def confirm_remove(self, name):
        dialog = ConfirmRemoveTorrentDialog(name, self.window())
        ok = bool(dialog.exec())
        return ok, (dialog.delete_files if ok else False)

    def ask_files(self, item):
        dialog = TorrentFilesDialog(item, self.window())
        return dialog.priorities() if dialog.exec() else None

    def ask_watch_file(self, item, targets):
        """Какой файл смотреть; None — отменили. Показываем, сколько уже
        скачано у каждого: по этому видно, что пойдёт быстрее."""
        try:
            progress = self.engine.file_progress(item.id)
        except Exception:
            progress = ()
        dialog = WatchFileDialog(item, targets, progress, self.window())
        dialog.exec()
        return dialog.chosen()

    def _notify(self, kind, text):
        bar = {"warning": InfoBar.warning, "info": InfoBar.info}.get(
            kind, InfoBar.success)
        # Предупреждение висит, пока его не закроют; подсказку про
        # подготовку плеера читать дольше, чем «открываем в VLC»
        duration = {"warning": -1, "info": 6000}.get(kind, 4000)
        bar(
            title="Торренты", content=text, orient=Qt.Horizontal,
            isClosable=True, position=InfoBarPosition.TOP,
            duration=duration, parent=self.window(),
        )

    # ----------------------------------------------------------- список

    def save_path(self):
        return (self.settings.get("torrent_folder")
                or self.settings["default_folder"])

    def refresh(self):
        items = {item.id: item for item in self.engine.items()}
        # Раздачу удалили во время просмотра — снимаем его сами, иначе
        # сервер держал бы закрытый поток
        active = self._stream.active if self._stream is not None else None
        if active is not None and active[0] not in items:
            self._stream.stop()
            self._end_prepare()
        for tid in list(self._cards):
            if tid not in items:
                card = self._cards.pop(tid)
                self.rows_vbox.removeWidget(card)
                card.setParent(None)
                card.deleteLater()
        for tid, item in items.items():
            card = self._cards.get(tid)
            if card is None:
                card = TorrentCard(item, self, self.rows_container)
                self._cards[tid] = card
                self.rows_vbox.addWidget(card)
            else:
                card.update_state(item)
        self.empty_label.setVisible(not items)
        self.rows_container.setVisible(bool(items))

    def _on_item_changed(self, item):
        card = self._cards.get(item.id)
        if card is not None:
            card.update_state(item)


class TorrentLibraryPage(_PlaceholderPage):
    def __init__(self, parent=None):
        super().__init__(
            "Библиотека",
            "Раздел в разработке. Здесь появятся скачанные раздачи.",
            parent,
        )
