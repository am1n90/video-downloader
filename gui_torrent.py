"""Режим «Torrent» — страницы (торрент-стриминг, Этапы 1-2).

Страница «Торренты» поверх torrent_engine: добавление magnet/.torrent,
выбор файлов раздачи, карточки очереди, пауза/продолжение/удаление
(1.3) и «Смотреть» — просмотр во время закачки во внешнем плеере
(2.1: torrent_stream + player). В 2.2 к просмотру добавились индикатор
подготовки плеера и отдельное окно «Что смотреть».

«Библиотека» режима (1.4) — TorrentLibraryPage на общей основе с
Библиотекой видео (gui.LibraryPageBase). Список — своя история в
settings.json (torrent_history), живое состояние подмешивается из движка.

С 2.6 добавление идёт через ОДНО окно выбора (TorrentFilesDialog):
раздача добавляется отложенно и не качает ничего, пока пользователь не
ответил — «Отмена» (убрать с диска), «Скачать» (всё отмеченное сразу)
или «Посмотреть» (строго одна выделенная серия через focus_file). То же
окно открывает кнопка «Файлы» на карточке — им же продолжают сериал.
Отдельного окна «Что смотреть» больше нет: галочки («что скачать») и
выделение строки («что смотреть») живут в одном дереве.

Импортируется из gui.MainWindow.__init__, а не с верхнего уровня gui.py:
модуль сам берёт общие виджеты и отступы из gui.

Колбэки движка приходят ИЗ ФОНОВОГО ПОТОКА — страница получает их только
через сигналы Bridge (тот же приём, что в режиме Video Downloader).
"""

import dataclasses
import os
import time

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QStyle, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, CheckBox,
                            InfoBar, InfoBarPosition, LineEdit, MessageBoxBase,
                            PrimaryPushButton, ProgressBar, PushButton,
                            StrongBodyLabel, SubtitleLabel, TitleLabel)

import config
import player
import torrent_engine as te
import torrent_stream as ts
from downloader import fmt_eta, fmt_speed
from gui import (SP_BLOCK, SP_GROUP, SP_WINDOW, LibraryPageBase,
                 TransparentScrollArea, fmt_mb, ru_records)

STATE_TEXT = {
    te.STATE_METADATA: "Получаем список файлов…",
    te.STATE_PENDING: "Ожидает выбора файлов",
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

# Окно выбора (2.6): контексты и ответ пользователя
MODE_ADD = "add"        # раздачу только что добавили, она ещё не качает
MODE_MANAGE = "manage"  # раздача в списке, кнопка «Файлы» на карточке

ACTION_CLOSE = "close"        # закрыли окно (Esc/крестик) — ничего не делать
ACTION_CANCEL = "cancel"      # «Отмена» — убрать раздачу вместе с кусками
ACTION_DOWNLOAD = "download"  # «Скачать» — всё отмеченное галочками
ACTION_WATCH = "watch"        # «Посмотреть» — только выделенная строка


@dataclasses.dataclass(frozen=True)
class FilesChoice:
    """Ответ окна выбора файлов (см. TorrentFilesDialog)."""
    action: str
    priorities: tuple = ()
    video: object = None        # TorrentFile — что смотреть
    save_path: str = ""

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


def _build_folders(tree, files):
    """Построить папки раздачи в дереве.

    Разбор пути по разделителям и узлы папок — отдельно от листьев: их
    окно выбора собирает само (галочка, размер, сколько скачано).

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


class _FilesTree(QTreeWidget):
    """Дерево окна выбора: клик по галочке не трогает выделение строки.

    В окне 2.6 это два независимых ответа — галочка «скачать этот файл»
    и выделение «смотреть эту серию». QTreeWidget по умолчанию выделяет
    строку при любом клике по ней, в том числе по квадратику галочки, и
    тогда «Посмотреть» оживала бы от простой расстановки галочек.
    Событие делегату отдаём как есть (иначе галочка не переключится), а
    выделение возвращаем на место после него.
    """

    def _hits_check(self, index, pos):
        if index.column() != 0:
            return False
        rect = self.visualRect(index)
        style = self.style()
        width = (style.pixelMetric(QStyle.PM_IndicatorWidth, None, self)
                 + 2 * style.pixelMetric(QStyle.PM_FocusFrameHMargin,
                                         None, self))
        return rect.left() <= pos.x() <= rect.left() + width

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        index = self.indexAt(pos)
        restore = bool(index.isValid()) and self._hits_check(index, pos)
        keep = self.currentItem() if restore else None
        super().mousePressEvent(event)
        if restore:
            self.setCurrentItem(keep)
            if keep is None:
                self.clearSelection()


class TorrentFilesDialog(MessageBoxBase):
    """Одно окно выбора для раздачи: что скачать и что смотреть (2.6).

    Галочки — состав закачки; движок ждёт в set_files список приоритетов
    по индексам файлов (0 — не качать, 4 — качать). Показываем сумму
    ВЫБРАННЫХ файлов, а не wanted_size: последний libtorrent считает по
    целым кускам, и для пользователя он выглядит странно (находка 1.2).

    Выделение строки — отдельный ответ «что смотреть»: «Посмотреть»
    качает СТРОГО одну серию (focus_file), галочки при этом не в счёт.
    Единственное видео раздачи (фильм) выделять не нужно — берём его.

    Контексты (mode):
      MODE_ADD    — раздачу только что добавили, она не качает ничего:
                    сверху папка сохранения с «Изменить», галочки стоят
                    у всех файлов, «Отмена» убирает раздачу;
      MODE_MANAGE — кнопка «Файлы» уже добавленной раздачи: галочки по
                    текущим приоритетам, «Отмена» просто закрывает.

    Ответ — FilesChoice (см. choice()); Esc и крестик дают ACTION_CLOSE,
    то есть «ничего не делать»: случайное закрытие окна не должно
    удалять раздачу.
    """

    def __init__(self, item, mode=MODE_MANAGE, progress=(), parent=None):
        super().__init__(parent)
        self._item = item
        self._mode = mode
        self._files = list(item.files)
        self._videos = {f.index: f for f in ts.watchable_files(item.files)}
        self._leaves = {}          # индекс файла -> лист дерева
        self._guard = False        # защита от рекурсии в itemChanged
        self._action = ACTION_CLOSE
        self._choice = FilesChoice(ACTION_CLOSE)
        self._save_path = item.save_path

        self.viewLayout.addWidget(SubtitleLabel(
            "Что скачать" if mode == MODE_ADD else "Файлы раздачи", self))
        if mode == MODE_ADD:
            self.name_label = BodyLabel(item.name or item.id[:12], self)
            self.name_label.setWordWrap(True)
            self.viewLayout.addWidget(self.name_label)
            self.viewLayout.addLayout(self._folder_row())

        self.tree = _FilesTree(self)
        self.tree.setHeaderLabels(["Файл", "Размер", "Скачано"])
        self.tree.setColumnWidth(0, 340)
        self.tree.setColumnWidth(1, 100)
        self.tree.setMinimumSize(560, 320)
        # «Скачано» показываем, когда есть что показывать: у только что
        # добавленной раздачи столбец нулей — пустой шум
        self._show_done = bool(progress) and (mode != MODE_ADD
                                              or any(progress))
        self._build_tree(progress)
        self.tree.expandAll()
        self.tree.setColumnHidden(2, not self._show_done)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemSelectionChanged.connect(self._update_watch)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.viewLayout.addWidget(self.tree)

        self.size_label = BodyLabel("", self)
        self.viewLayout.addWidget(self.size_label)
        self.hint_label = CaptionLabel(
            "Галочки — что скачать. Чтобы смотреть, выделите строку с "
            "видео: качаться будет только она, ждать конца закачки не "
            "нужно", self)
        self.hint_label.setWordWrap(True)
        self.viewLayout.addWidget(self.hint_label)

        self.watch_btn = PushButton("Посмотреть", self.buttonGroup)
        self.watch_btn.setAttribute(Qt.WA_LayoutUsesWidgetRect)
        self.watch_btn.clicked.connect(self._on_watch)
        self.buttonLayout.insertWidget(0, self.watch_btn, 1, Qt.AlignVCenter)

        self.yesButton.setText("Скачать" if mode == MODE_ADD else "Применить")
        self.yesButton.clicked.connect(
            lambda: self._set_action(ACTION_DOWNLOAD))
        self.cancelButton.setText("Отмена")
        self.cancelButton.clicked.connect(
            lambda: self._set_action(ACTION_CANCEL))
        self._update_size()
        self._update_watch()

    def _folder_row(self):
        row = QHBoxLayout()
        row.setSpacing(SP_GROUP)
        self.folder_label = BodyLabel("", self)
        self.folder_label.setWordWrap(True)
        row.addWidget(self.folder_label, stretch=1)
        self.folder_btn = PushButton("Изменить", self)
        self.folder_btn.clicked.connect(self._choose_folder)
        row.addWidget(self.folder_btn)
        self._show_folder()
        return row

    def _show_folder(self):
        self.folder_label.setText(f"Папка: {self._save_path}")

    def _choose_folder(self):
        from PySide6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(
            self, "Папка для раздачи", self._save_path)
        if path:
            self._save_path = path
            self._show_folder()

    def _build_tree(self, progress):
        self._guard = True
        try:
            parents, folders = _build_folders(self.tree, self._files)
            for node in folders:
                node.setFlags(node.flags() | Qt.ItemIsUserCheckable)
                node.setCheckState(0, Qt.Unchecked)
            for file, (parent, name) in zip(self._files, parents):
                done = (progress[file.index]
                        if file.index < len(progress) else 0)
                share = ""
                # Доля — только у видео: по ней выбирают, какая серия
                # пойдёт быстрее (приём 2.2)
                if self._show_done and file.index in self._videos \
                        and file.size:
                    share = ("скачан" if done >= file.size
                             else f"{done * 100 // file.size}%")
                leaf = QTreeWidgetItem(
                    parent, [name, fmt_size(file.size), share])
                leaf.setFlags(leaf.flags() | Qt.ItemIsUserCheckable)
                leaf.setData(0, Qt.UserRole, file.index)
                checked = (self._mode == MODE_ADD or file.priority > 0)
                leaf.setCheckState(
                    0, Qt.Checked if checked else Qt.Unchecked)
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
        # встанет, поэтому «Скачать» в этом случае недоступна
        self.yesButton.setEnabled(selected > 0)

    # ------------------------------------------------- «что смотреть»

    def watch_target(self):
        """Что пойдёт в просмотр: выделенное видео, а если видео в
        раздаче одно — оно само (фильм выделять не нужно). Иначе None."""
        node = self.tree.currentItem()
        if node is not None and node.isSelected():
            video = self._videos.get(node.data(0, Qt.UserRole))
            if video is not None:
                return video
        if len(self._videos) == 1:
            return next(iter(self._videos.values()))
        return None

    def _update_watch(self):
        self.watch_btn.setEnabled(self.watch_target() is not None)

    def select(self, index):
        """Выделить строку файла — для offscreen-тестов и живых проверок:
        мышью в них никто не кликает."""
        node = self._leaves.get(index)
        if node is None:
            return False
        self.tree.setCurrentItem(node)
        self._update_watch()
        return True

    def set_checked(self, index, checked):
        """Поставить/снять галочку — тоже для проверок без мыши."""
        node = self._leaves.get(index)
        if node is None:
            return False
        node.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
        return True

    def _on_watch(self):
        if self.watch_target() is None:
            return
        self._set_action(ACTION_WATCH)
        self.accept()

    def _on_double_click(self, node, column):
        """Двойной клик по видео — сразу «Посмотреть» (приём 2.2)."""
        if node is not None and self._videos.get(node.data(0, Qt.UserRole)):
            self._on_watch()

    # ------------------------------------------------------- ответ окна

    def _set_action(self, action):
        self._action = action

    def make_choice(self, action):
        """Ответ окна для этого действия. Отдельно от exec() — живые
        проверки показывают окно и отвечают за пользователя сами."""
        return FilesChoice(
            action=action,
            priorities=tuple(self.priorities()),
            video=self.watch_target() if action == ACTION_WATCH else None,
            save_path=self._save_path)

    def choice(self):
        """Ответ пользователя (FilesChoice). Вызывать после exec()."""
        return self._choice

    def exec(self):
        ok = super().exec()
        action = self._action
        if ok and action not in (ACTION_DOWNLOAD, ACTION_WATCH):
            action = ACTION_DOWNLOAD        # Enter на «Скачать»
        elif not ok and action != ACTION_CANCEL:
            action = ACTION_CLOSE           # Esc/крестик — ничего не делаем
        self._choice = self.make_choice(action)
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

        if item.state == te.STATE_PENDING:
            # Раздача ещё ничего не качала — показывать ей проценты не о
            # чем (а у magnet бывает проскок в момент метаданных)
            self.bar.hide()
        elif item.state in DONE_STATES:
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
        if self.page.is_pending(tid):
            # Раздача ещё ничего не качает и ждёт ответа в окне выбора
            # (2.6): пауза и просмотр тут бессмысленны
            if item.has_metadata:
                self._add_action("Выбрать файлы",
                                 lambda: self.page.choose_pending(tid),
                                 accent=True)
            self._add_action("Удалить", lambda: self.page.remove(tid))
            return
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
        if not item.has_metadata or item.state == te.STATE_PENDING:
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
        # Раздачи, добавленные в этом сеансе и ждущие метаданных: как
        # только список файлов придёт, окно выбора откроется само (2.6).
        # _dialog_busy — окно уже на экране: колбэки движка идут потоком,
        # и без него второе окно легло бы поверх первого
        self._awaiting = []
        self._dialog_busy = False
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
        if self._add(lambda: self.engine.add_magnet(
                uri, self.save_path(), defer=True)):
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
        return self._add(lambda: self.engine.add_torrent_file(
            path, self.save_path(), defer=True))

    def _add(self, call):
        """Добавить раздачу отложенно: качать ничего не начинаем, ждём
        список файлов и показываем окно выбора (2.6)."""
        if not self.start_engine():
            return False
        try:
            tid = call()
        except Exception as exc:
            self._notify("warning", f"Не удалось добавить раздачу: {exc}")
            return False
        if tid and tid not in self._awaiting:
            self._awaiting.append(tid)
        self.refresh()          # refresh откроет окно, если файлы уже есть
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
        # Библиотека (1.4): без файлов запись остаётся («Убрана из
        # Торрентов») — запоминаем, что успело скачаться, пока движок это
        # знает; с файлами показывать больше нечего
        self._sync_record(item)
        if not self._engine_call(
                lambda: self.engine.remove(tid, delete_files=delete_files)):
            return False
        if delete_files:
            self._forget(tid)
        self.refresh()
        return True

    # ------------------------------------------------- Библиотека 1.4

    def _remember(self, tid):
        """Записать раздачу в Библиотеку: пользователь ответил «Скачать»
        или «Посмотреть». Не раньше — раздача после «Отмена» в
        Библиотеку попадать не должна."""
        item = self.engine.get(tid)
        if item is None or not item.files:
            return
        done = done_indexes(self.engine, item) or ()
        config.add_torrent_history(self.settings, library_record(item, done))
        config.save(self.settings)

    def _sync_record(self, item):
        """Обновить запись раздачи: какие файлы скачаны целиком.

        Пока раздача в движке, это знает file_progress; после снятия
        раздачи — только запись. settings.json пишем лишь при изменении.
        """
        if not item.has_metadata or item.state in (te.STATE_METADATA,
                                                   te.STATE_PENDING,
                                                   te.STATE_CHECKING):
            return
        record = config.find_torrent_record(self.settings, item.id)
        if record is None:
            return
        changes = {"name": TorrentCard._title(item),
                   "save_path": item.save_path}
        done = done_indexes(self.engine, item)
        if done is not None:
            # Индексы за пределами сохранённых файлов не хранимы — см.
            # LIBRARY_FILES_MAX
            files_len = len(record.get("files") or ())
            changes["done"] = [i for i in done if i < files_len]
        if config.update_torrent_history(self.settings, item.id, **changes):
            config.save(self.settings)

    def _forget(self, tid):
        if config.remove_torrent_history(self.settings, [tid]):
            config.save(self.settings)

    def watching_index(self, tid):
        """Какой файл раздачи сейчас смотрят; None — никакой."""
        active = self._stream.active if self._stream is not None else None
        if active is None or active[0] != tid:
            return None
        return active[1]

    # ------------------------------------------------- выбор файлов 2.6

    def is_pending(self, tid):
        """Раздача добавлена, но выбор ещё не сделан — она не качает."""
        try:
            return self.engine.is_pending(tid)
        except Exception:
            return False

    def _check_pending(self):
        """Открыть окно выбора у раздач, дождавшихся списка файлов.

        Зовётся из refresh и из колбэка движка: у magnet метаданные
        приходят через секунды, у .torrent — сразу при добавлении.
        """
        if self._dialog_busy:
            return
        while self._awaiting:
            tid = self._awaiting[0]
            item = self.engine.get(tid)
            if item is None:                    # раздачу уже убрали
                self._awaiting.pop(0)
                continue
            if not item.has_metadata:
                # Очередь по порядку добавления: ждём список файлов
                return
            self._awaiting.pop(0)
            self._ask_new_torrent(item)

    def choose_pending(self, tid):
        """Кнопка «Выбрать файлы» на карточке: то же окно, что при
        добавлении. Нужна, если окно закрыли крестиком или раздача
        осталась ждать с прошлого запуска программы."""
        item = self.engine.get(tid)
        if item is None or not item.files:
            return False
        return self._ask_new_torrent(item)

    def _ask_new_torrent(self, item):
        """Окно выбора для только что добавленной раздачи и разбор ответа.

        «Отмена» убирает раздачу целиком, вместе с кусками, успевшими
        прийти, пока шли метаданные; «Скачать» качает всё отмеченное
        обычным порядком; «Посмотреть» переключает закачку на одну
        выделенную серию (focus_file внутри watch).
        """
        tid = item.id
        self._dialog_busy = True
        try:
            choice = self.ask_choice(item, MODE_ADD)
        finally:
            self._dialog_busy = False
        if choice.action == ACTION_CLOSE:
            self.refresh()          # карточка остаётся с «Выбрать файлы»
            return False
        if choice.action == ACTION_CANCEL:
            ok = self._engine_call(
                lambda: self.engine.remove(tid, delete_files=True))
            if ok:
                # Файлы удалены — старая запись той же раздачи (если её
                # добавляли повторно) указывала бы в пустоту
                self._forget(tid)
            self.refresh()
            return ok
        if choice.save_path and choice.save_path != item.save_path:
            self._engine_call(
                lambda: self.engine.set_save_path(tid, choice.save_path))
        if choice.action == ACTION_WATCH and choice.video is not None:
            # Приоритеты галочек НЕ применяем: просмотр качает строго
            # одну серию, и явный заказ файлов ему только мешал бы.
            # focus внутри begin_download — чтобы раздача не успела
            # потянуть всё подряд в момент разрешения качать
            if not self._engine_call(lambda: self.engine.begin_download(
                    tid, focus=choice.video.index)):
                return False
            self._remember(tid)
            return self.watch(tid, index=choice.video.index)
        ok = self._engine_call(lambda: self.engine.begin_download(
            tid, list(choice.priorities)))
        if ok:
            self._remember(tid)
        self.refresh()
        return ok

    def choose_files(self, tid):
        """Кнопка «Файлы» уже добавленной раздачи: то же окно.

        «Применить» меняет состав закачки (как и до 2.6), «Посмотреть»
        переключает закачку на выделенную серию — этим и продолжают
        сериал после того, как предыдущая серия скачалась.
        """
        item = self.engine.get(tid)
        if item is None or not item.files:
            return False
        if self.is_pending(tid):
            return self.choose_pending(tid)
        self._dialog_busy = True
        try:
            choice = self.ask_choice(item, MODE_MANAGE)
        finally:
            self._dialog_busy = False
        if choice.action == ACTION_WATCH and choice.video is not None:
            return self.watch(tid, index=choice.video.index)
        if choice.action != ACTION_DOWNLOAD:
            return False
        # prioritize_files асинхронный: сразу после set_files снимок ещё
        # показывает старый выбор (находка 21) — карточку обновит движок
        return self._engine_call(
            lambda: self.engine.set_files(tid, list(choice.priorities)))

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
                                                   te.STATE_PENDING,
                                                   te.STATE_CHECKING,
                                                   te.STATE_ERROR):
            return False
        return ts.choose_video_file(item.files) is not None

    def watch(self, tid, index=None):
        """Открыть видео раздачи во внешнем плеере.

        index задан (его выбрали в окне файлов) — открываем этот файл;
        иначе одно видео открываем сразу (клик в один шаг для обычного
        фильма), а при нескольких показываем окно выбора — там же и
        галочки. Скачанный целиком файл открываем напрямую: HTTP-сервер
        и кэш кусков для него не нужны.

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
        if index is not None:
            target = next((f for f in targets if f.index == index), None)
            if target is None:
                self._notify("warning", "Этот файл нельзя смотреть")
                return False
        elif len(targets) == 1:
            target = targets[0]
        else:
            return self.choose_files(tid)
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

    def ask_choice(self, item, mode):
        """Окно выбора файлов; возвращает FilesChoice.

        Показываем, сколько уже скачано у каждого файла: по этому видно,
        какая серия пойдёт быстрее (приём 2.2).
        """
        try:
            progress = self.engine.file_progress(item.id)
        except Exception:
            progress = ()
        dialog = TorrentFilesDialog(item, mode, progress, self.window())
        dialog.exec()
        return dialog.choice()

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
        self._check_pending()

    def _on_item_changed(self, item):
        card = self._cards.get(item.id)
        if card is not None:
            card.update_state(item)
        if item.id in self._awaiting and item.has_metadata:
            self._check_pending()
        self._sync_record(item)


# ================= Библиотека торрентов (1.4) =================
#
# Список — своя история в settings.json (config.torrent_history), а не
# список раздач движка: «Очистить данные библиотеки» не должен трогать
# раздачи, а раздача, снятая с «Торрентов» без удаления файлов, остаётся
# в Библиотеке — фильм-то скачан. Живое состояние (качается, раздаётся,
# сколько скачано) подмешивается из движка, пока раздача в нём есть.
#
# Процент раздачи здесь НЕ показываем: после focus_file (2.5) libtorrent
# считает раздачу по одной выбранной серии — посмотрели серию 3, и
# сериал «100%, Раздаётся», хотя из пяти серий есть одна. Считаем по
# файлам: «Скачано 1 из 5 видеофайлов».

LIB_DONE = "Скачано"
LIB_PARTIAL = "Не докачано"
LIB_REMOVED = "Убраны из Торрентов"
LIB_FILTERS = (LIB_DONE, LIB_PARTIAL, LIB_REMOVED)

FILE_OPEN = "open"      # скачан целиком — открыть системным плеером
FILE_WATCH = "watch"    # не докачан, раздача в движке — смотреть потоком
FILE_STOP = "stop"      # этот файл сейчас смотрят

CHECK_WIDTH = 29        # галочка без текста: уже стиль qfluentwidgets не даёт
NBSP = " "         # «+ 1 файл» и даты не рвём переносом строки

# Раздачи с сотнями файлов (например, сборник серий по одной на файл)
# не должны раздувать settings.json: храним пути только первых
# LIBRARY_FILES_MAX файлов, остальные — только счётчиком и суммой
# размера (files_more / files_more_size в записи). Обычно видео стоят
# в начале списка раздачи, поэтому урезание хвоста задевает в первую
# очередь служебные файлы; если видео всё же окажется за пределами
# первых 50, оно попадёт в общий счётчик «+N файлов» без кнопки
# «Смотреть» — известное ограничение, не разбор каждого случая.
# Готовность скрытых файлов тоже не отслеживается: они всегда
# учитываются в общем размере (files_more_size) и счётчике «+N файлов»,
# но не в «Скачано X из Y» и не влияют на категорию Скачано/Не докачано.
LIBRARY_FILES_MAX = 50


def ru_plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def library_record(item, done=()):
    """Запись Библиотеки по снимку раздачи (см. config.add_torrent_history).

    files обрезаны до LIBRARY_FILES_MAX (см. комментарий у константы);
    done — тоже, индексы за пределами сохранённых файлов не хранимы.
    """
    kept = item.files[:LIBRARY_FILES_MAX]
    extra = item.files[LIBRARY_FILES_MAX:]
    record = {
        "id": item.id,
        "name": TorrentCard._title(item),
        "save_path": item.save_path,
        "added": int(item.added_time or time.time()),
        "files": [[f.path, f.size] for f in kept],
        "done": sorted(i for i in done if i < len(kept)),
    }
    if extra:
        record["files_more"] = len(extra)
        record["files_more_size"] = sum(f.size for f in extra)
    return record


def done_indexes(engine, item):
    """Номера файлов, скачанных целиком; None — движок не ответил.

    По file_progress, а не по размеру файла на диске: libtorrent создаёт
    файлы сразу полного размера, недокачанный от готового не отличить.
    """
    try:
        progress = engine.file_progress(item.id)
    except Exception:
        return None
    if len(progress) < len(item.files):
        return None
    return sorted(f.index for f in item.files if progress[f.index] >= f.size)


def record_path(record, rel):
    """Путь файла записи на диске (разделитель — как в дереве файлов)."""
    parts = str(rel).replace("\\", "/").split("/")
    return os.path.join(record.get("save_path") or "", *parts)


def _inside(root, path):
    try:
        return os.path.commonpath([root, path]) == root and path != root
    except ValueError:              # разные диски
        return False


def remove_record_files(record):
    """Удалить с диска файлы раздачи, которой уже нет в движке.

    Ровно файлы записи, служебный .<id>.parts и опустевшие папки ВНУТРИ
    папки сохранения; саму папку сохранения и чужие файлы не трогаем.
    Пути вне папки сохранения (ручная правка settings.json) пропускаем.
    Возвращает имена файлов, которые удалить не удалось.
    """
    if not record.get("save_path"):
        return []
    root = os.path.abspath(record["save_path"])
    if not os.path.isdir(root):
        return []
    failed = []
    folders = set()
    for entry in record.get("files") or []:
        try:
            path = os.path.abspath(record_path(record, entry[0]))
        except (TypeError, IndexError):
            continue
        if not _inside(root, path):
            continue
        parent = os.path.dirname(path)
        while _inside(root, parent):
            folders.add(parent)
            parent = os.path.dirname(parent)
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                failed.append(os.path.basename(path))
    try:
        os.remove(os.path.join(root, f".{record['id']}.parts"))
    except OSError:
        pass
    for folder in sorted(folders, key=len, reverse=True):
        try:
            os.rmdir(folder)            # только пустые
        except OSError:
            pass
    return failed


@dataclasses.dataclass(frozen=True)
class LibraryFile:
    """Файл записи для показа."""
    index: int
    name: str
    size: int
    done: int           # байт скачано
    complete: bool
    queued: bool        # движок его сейчас качает (приоритет > 0)
    path: str           # на диске
    action: str = ""    # FILE_* или "" — кнопки нет


@dataclasses.dataclass(frozen=True)
class LibraryView:
    """Что показывает строка Библиотеки (запись + живое состояние)."""
    record: dict
    item: object            # TorrentItem или None — раздачи нет в движке
    units: tuple            # LibraryFile: видео раздачи (нет видео — все файлы)
    others: int             # сколько файлов не показано (субтитры, nfo…)
    state_text: str
    parts: tuple            # части подписи: состояние, сколько скачано…
    category: str           # LIB_DONE / LIB_PARTIAL

    @property
    def multi(self):
        return len(self.units) > 1

    @property
    def details(self):
        return "  •  ".join(self.parts)


def file_status(f, in_engine):
    """Подпись файла. Невыбранная серия — «Не скачано», а не ошибка."""
    if f.complete:
        return "Скачано"
    pct = int(f.done * 100 / f.size) if f.size else 0
    if in_engine and f.queued:
        return f"{pct}%"
    return f"Не скачано ({pct}%)" if pct >= 1 else "Не скачано"


class TorrentLibraryRow(CardWidget):
    """Строка Библиотеки торрентов: раздача, а у сериала — и её серии.

    Как у TorrentCard, кнопки пересоздаются только при смене их набора,
    на тиках движка обновляются подписи.
    """

    def __init__(self, view, page):
        super().__init__(page.rows_container)
        self.page = page
        self.record = view.record
        self.tid = view.record["id"]
        self._signature = None
        self._file_labels = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP_GROUP * 2, SP_GROUP * 2,
                               SP_GROUP * 2, SP_GROUP * 2)
        lay.setSpacing(SP_GROUP)

        head = QHBoxLayout()
        head.setSpacing(SP_GROUP * 2)
        self.select_check = CheckBox(self)
        # Без текста у CheckBox остаётся минимальная ширина под надпись —
        # название раздачи уезжало вправо на лишние 35 px
        self.select_check.setFixedWidth(CHECK_WIDTH)
        self.select_check.stateChanged.connect(
            lambda: page._on_selection_changed())
        head.addWidget(self.select_check)

        text = QVBoxLayout()
        text.setSpacing(4)
        self.title_label = StrongBodyLabel("", self)
        self.title_label.setWordWrap(True)
        text.addWidget(self.title_label)
        self.details_label = BodyLabel("", self)
        self.details_label.setWordWrap(True)
        text.addWidget(self.details_label)
        text.addStretch()
        head.addLayout(text, stretch=1)

        self.actions_widget = QWidget(self)
        acts = QHBoxLayout(self.actions_widget)
        acts.setContentsMargins(0, 0, 0, 0)
        acts.setSpacing(SP_GROUP)
        head.addWidget(self.actions_widget)
        lay.addLayout(head)

        self.files_widget = QWidget(self)
        files = QVBoxLayout(self.files_widget)
        # Серии — под названием раздачи, с отступом на ширину галочки
        files.setContentsMargins(CHECK_WIDTH + SP_GROUP * 2, 0, 0, 0)
        files.setSpacing(4)
        lay.addWidget(self.files_widget)

        self.update_view(view)

    @staticmethod
    def _clear(layout):
        """Убрать виджеты раскладки (setParent(None) — см. TorrentCard)."""
        while layout.count():
            widget = layout.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    @staticmethod
    def _button(parent, text, slot, accent=False):
        btn = PrimaryPushButton(text, parent) if accent else \
            PushButton(text, parent)
        btn.clicked.connect(slot)
        parent.layout().addWidget(btn)
        return btn

    def _file_button(self, parent, f):
        tid, index = self.tid, f.index
        if f.action == FILE_OPEN:
            self._button(parent, "Открыть",
                         lambda: self.page.open_file(tid, index))
        elif f.action == FILE_WATCH:
            self._button(parent, "Смотреть",
                         lambda: self.page.watch(tid, index), accent=True)
        elif f.action == FILE_STOP:
            self._button(parent, "Остановить просмотр",
                         lambda: self.page.stop_watch(tid))

    def buttons(self):
        """Надписи кнопок строки и серий (для тестов и живых проверок)."""
        return [btn.text() for btn in self.findChildren(PushButton)]

    def update_view(self, view):
        self.record = view.record
        self.title_label.setText(view.record.get("name") or self.tid[:12])
        # Внутри части пробелы неразрывные: «+ 1 файл» и дата не должны
        # рваться переносом, а разрывы — только между частями
        self.details_label.setText("  •  ".join(
            part.replace(" ", NBSP) for part in view.parts))

        expanded = self.page.is_expanded(self.tid)
        signature = (view.multi, expanded,
                     tuple((f.index, f.action) for f in view.units))
        if signature != self._signature:
            self._signature = signature
            self._rebuild(view, expanded)
        for f in view.units:
            label = self._file_labels.get(f.index)
            if label is not None:
                label.setText(file_status(f, view.item is not None))

    def _rebuild(self, view, expanded):
        self._clear(self.actions_widget.layout())
        self._clear(self.files_widget.layout())
        self._file_labels = {}
        tid = self.tid
        if not view.multi and view.units:
            self._file_button(self.actions_widget, view.units[0])
        self._button(self.actions_widget, "Открыть папку",
                     lambda: self.page.open_folder(tid))
        if not view.multi:
            self.files_widget.hide()
            return
        self._button(self.actions_widget,
                     "Скрыть файлы" if expanded else "Показать файлы",
                     lambda: self.page.toggle_expanded(tid))
        self.files_widget.setVisible(expanded)
        if not expanded:
            return
        for f in view.units:
            line = QWidget(self.files_widget)
            line_lay = QHBoxLayout(line)
            line_lay.setContentsMargins(0, 0, 0, 0)
            line_lay.setSpacing(SP_GROUP)
            name = BodyLabel(f"{f.name}  •  {fmt_size(f.size)}", line)
            name.setWordWrap(True)
            line_lay.addWidget(name, stretch=1)
            status = CaptionLabel("", line)
            line_lay.addWidget(status)
            self._file_labels[f.index] = status
            # Кнопка в слоте постоянного размера: иначе строки с кнопкой
            # выше и шире, и подписи «Скачано»/«Не скачано» не встают
            # столбцом
            slot = QWidget(line)
            slot_lay = QHBoxLayout(slot)
            slot_lay.setContentsMargins(0, 0, 0, 0)
            slot_lay.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            slot.setFixedSize(self.page.file_slot_size())
            self._file_button(slot, f)
            line_lay.addWidget(slot)
            self.files_widget.layout().addWidget(line)


class TorrentLibraryPage(LibraryPageBase):
    """Библиотека режима Torrent: что скачивали и смотрели через торренты.

    Удаление записи ВСЕГДА снимает раздачу и с «Торрентов» (решение
    владельца 17.09.2026): галочка решает только судьбу файлов. Раздача в
    движке снимается engine.remove — он же убирает .fastresume, .chosen,
    .pending и .parts, сирот в папке данных не остаётся.
    """

    CLEAR_TEXT = ("Список раздач будет очищен. Раздачи на странице "
                  "«Торренты» и файлы на диске останутся.")
    DELETE_TEXT = ("Раздачи будут убраны из Библиотеки и со страницы "
                   "«Торренты».")

    def __init__(self, torrent_page, bridge, settings, parent=None):
        super().__init__(settings, "Показать:",
                         "Здесь появятся раздачи, которые вы скачивали "
                         "или смотрели", parent)
        self.torrent_page = torrent_page
        self.engine = torrent_page.engine
        self._rows = {}
        self._expanded = set()
        self._slot_size = None
        bridge.itemChanged.connect(self._on_item_changed)
        bridge.queueChanged.connect(self._on_queue_changed)

    def showEvent(self, event):
        # Движок нужен и здесь: удалять раздачу из не запущенного движка
        # нельзя — remove() без сессии не тронул бы fastresume, и раздача
        # вернулась бы при следующем запуске
        self.torrent_page.start_engine()
        super().showEvent(event)

    # ---------- данные ----------

    def _has_records(self):
        return bool(config.get_torrent_history(self.settings))

    def _clear_records(self):
        config.clear_history(self.settings, "torrent_history")

    def view(self, record, item=None):
        """Собрать то, что показывает строка (без виджетов — для тестов)."""
        progress = None
        if item is not None and item.has_metadata:
            try:
                progress = self.engine.file_progress(item.id)
            except Exception:
                progress = None
        files = []
        for index, entry in enumerate(record.get("files") or []):
            try:
                files.append((index, str(entry[0]), int(entry[1])))
            except (TypeError, ValueError, IndexError):
                continue
        done_set = set(record.get("done") or ())
        live = progress is not None and len(progress) >= len(files)
        priorities = {f.index: f.priority for f in item.files} \
            if item is not None else {}
        watching = self.torrent_page.watching_index(record["id"]) \
            if item is not None else None
        can_watch = item is not None and self.torrent_page.can_watch(item)

        videos = [f for f in files if ts.is_video(f[1])]
        shown_ids = {f[0] for f in (videos or files)}
        units = []
        done_bytes = 0
        for index, rel, size in files:
            if live:
                done = min(int(progress[index]), size)
                complete = progress[index] >= size
            else:
                complete = index in done_set
                done = size if complete else 0
            done_bytes += done
            if index not in shown_ids:
                continue
            path = record_path(record, rel)
            if watching == index:
                action = FILE_STOP
            elif complete and os.path.isfile(path):
                action = FILE_OPEN
            elif can_watch and ts.is_video(rel):
                action = FILE_WATCH
            else:
                action = ""
            units.append(LibraryFile(
                index=index, name=rel.replace("\\", "/").split("/")[-1],
                size=size, done=done, complete=complete,
                queued=priorities.get(index, 0) > 0, path=path,
                action=action))

        all_done = bool(units) and all(u.complete for u in units)
        if item is None:
            state = "Убрана из Торрентов"
        elif item.state == te.STATE_FINISHED:
            state = "Скачано" if all_done else "Выбранное скачано"
        else:
            state = STATE_TEXT.get(item.state, item.state)

        parts = [state]
        watch = self.torrent_page.watch_status(record["id"]) \
            if item is not None else ""
        if watch:
            parts.append(watch)
        # files_more_size — сумма размеров файлов за пределами
        # LIBRARY_FILES_MAX (их путей мы не храним)
        total = sum(f[2] for f in files) + record.get("files_more_size", 0)
        if len(units) > 1:
            count = len(units)
            one, many = ("видеофайла", "видеофайлов") if videos \
                else ("файла", "файлов")
            parts.append(f"Скачано {sum(u.complete for u in units)} из "
                         f"{count} {ru_plural(count, one, many, many)}")
        elif units and not all_done and item is not None:
            unit = units[0]
            parts.append(
                f"{int(unit.done * 100 / unit.size) if unit.size else 0}%")
        if done_bytes >= total:
            parts.append(fmt_size(total))
        else:
            parts.append(f"{fmt_size(done_bytes)} из {fmt_size(total)}")
        if record.get("added"):
            try:
                parts.append("Добавлено " + time.strftime(
                    "%d.%m.%Y", time.localtime(int(record["added"]))))
            except (TypeError, ValueError, OverflowError, OSError):
                pass
        others = len(files) - len(units) + record.get("files_more", 0)
        if others:
            parts.append(f"+ {others} "
                         + ru_plural(others, "файл", "файла", "файлов"))
        return LibraryView(
            record=record, item=item, units=tuple(units), others=others,
            state_text=state, parts=tuple(parts),
            category=LIB_DONE if all_done else LIB_PARTIAL)

    @staticmethod
    def _on_disk(record):
        for entry in record.get("files") or []:
            try:
                if os.path.isfile(record_path(record, entry[0])):
                    return True
            except (TypeError, IndexError):
                continue
        return False

    def _items(self):
        try:
            return {item.id: item for item in self.engine.items()}
        except Exception:
            return {}

    # ---------- список ----------

    def refresh(self):
        self._clear_rows()
        self._rows = {}
        items = self._items()
        selected = self._set_filter_items(list(LIB_FILTERS))
        query = self.search.text().strip().lower()

        widgets = []
        for record in config.get_torrent_history(self.settings):
            item = items.get(record["id"])
            # Раздачи нет в движке и файлов на диске тоже — показывать
            # нечего (как get_history у видео)
            if item is None and not self._on_disk(record):
                continue
            if query and query not in str(record.get("name") or "").lower():
                continue
            if selected == LIB_REMOVED and item is not None:
                continue
            view = self.view(record, item)
            if selected in (LIB_DONE, LIB_PARTIAL) \
                    and view.category != selected:
                continue
            row = TorrentLibraryRow(view, self)
            self._rows[record["id"]] = row
            widgets.append(row)
        self._show_rows(widgets)

    def rows(self):
        """Строки по id раздачи (для тестов и живых проверок)."""
        return dict(self._rows)

    def update_row(self, tid):
        row = self._rows.get(tid)
        record = config.find_torrent_record(self.settings, tid)
        if row is None or record is None:
            return
        row.update_view(self.view(record, self.engine.get(tid)))

    def _on_item_changed(self, item):
        if self.isVisible() and item.id in self._rows:
            self.update_row(item.id)

    def _on_queue_changed(self):
        if self.isVisible():
            self.refresh()

    def file_slot_size(self):
        """Место под кнопку серии — по самой длинной надписи."""
        if self._slot_size is None:
            probe = PushButton("Остановить просмотр")
            self._slot_size = probe.sizeHint()
            probe.deleteLater()
        return self._slot_size

    def is_expanded(self, tid):
        return tid in self._expanded

    def toggle_expanded(self, tid):
        self._expanded ^= {tid}
        self.update_row(tid)

    # ---------- действия строки ----------

    def file_path(self, tid, index):
        """Путь скачанного файла записи или "" (нет записи/файла)."""
        record = config.find_torrent_record(self.settings, tid)
        if record is None or not 0 <= index < len(record["files"]):
            return ""
        path = record_path(record, record["files"][index][0])
        return path if os.path.isfile(path) else ""

    def open_file(self, tid, index):
        path = self.file_path(tid, index)
        return bool(path) and QDesktopServices.openUrl(
            QUrl.fromLocalFile(path))

    def folder_of(self, tid):
        """Папка раздачи: у многофайловой — её корневая папка."""
        record = config.find_torrent_record(self.settings, tid)
        if record is None:
            return ""
        folder = record.get("save_path") or ""
        files = record.get("files") or []
        if files:
            parts = str(files[0][0]).replace("\\", "/").split("/")
            root = os.path.join(folder, parts[0]) if len(parts) > 1 else ""
            if root and os.path.isdir(root):
                folder = root
        return folder if os.path.isdir(folder) else ""

    def open_folder(self, tid):
        folder = self.folder_of(tid)
        return bool(folder) and QDesktopServices.openUrl(
            QUrl.fromLocalFile(folder))

    def watch(self, tid, index):
        """Смотреть недокачанный файл — тот же путь, что у карточки
        (поток + focus_file: качаться начнёт именно эта серия)."""
        started = self.torrent_page.watch(tid, index=index)
        self.update_row(tid)
        return started

    def stop_watch(self, tid):
        stopped = self.torrent_page.stop_watch()
        self.update_row(tid)
        return stopped

    # ---------- удаление ----------

    def _delete_selected(self):
        """Удалить выбранные записи; раздачи снимаются и с «Торрентов».

        Раздача в движке — engine.remove(delete_files=галочка). Раздачи в
        движке нет — с галочкой удаляем файлы записи сами
        (remove_record_files). Не удалось — запись остаётся, имя в InfoBar.
        """
        rows = self._selected_rows()
        if not rows:
            return
        # Без сессии remove() не убрал бы fastresume — лучше не удалять
        if not self.torrent_page.start_engine():
            return
        ok, delete_files = self._confirm_delete(len(rows))
        if not ok:
            return
        # Записи — до первого remove: он сам вызовет refresh (queueChanged),
        # и строки пересоздадутся
        records = [row.record for row in rows]

        failed = []
        removed = []
        for record in records:
            tid = record["id"]
            if self.engine.get(tid) is not None:
                try:
                    self.engine.remove(tid, delete_files=delete_files)
                except Exception as exc:
                    te.log.warning("library remove %s: %r", tid, exc)
                    failed.append(record.get("name") or tid[:12])
                    continue
            elif delete_files:
                bad = remove_record_files(record)
                if bad:
                    failed.extend(bad)
                    continue
            removed.append(tid)

        config.remove_torrent_history(self.settings, removed)
        config.save(self.settings)
        self.torrent_page.refresh()
        self.refresh()

        if failed:
            self._notify("warning", "Не удалены: " + ", ".join(failed))
        elif removed:
            self._notify("success", "Удалено: " + ru_records(len(removed)))
