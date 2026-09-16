"""Режим «Torrent» — страницы (торрент-стриминг, Этап 1, сессия 1.3).

Страница «Торренты» поверх torrent_engine: добавление magnet/.torrent,
выбор файлов раздачи, карточки очереди, пауза/продолжение/удаление.
«Библиотека» режима — заглушка до сессии 1.4.

Импортируется из gui.MainWindow.__init__, а не с верхнего уровня gui.py:
модуль сам берёт общие виджеты и отступы из gui.

Колбэки движка приходят ИЗ ФОНОВОГО ПОТОКА — страница получает их только
через сигналы Bridge (тот же приём, что в режиме Video Downloader).
"""

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QHBoxLayout, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, CheckBox,
                            InfoBar, InfoBarPosition, LineEdit, MessageBoxBase,
                            PrimaryPushButton, ProgressBar, PushButton,
                            StrongBodyLabel, SubtitleLabel, TitleLabel)

import torrent_engine as te
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
            folders = {}
            for file in self._files:
                parts = file.path.replace("\\", "/").split("/")
                parent = self.tree.invisibleRootItem()
                for depth in range(len(parts) - 1):
                    key = tuple(parts[:depth + 1])
                    node = folders.get(key)
                    if node is None:
                        node = QTreeWidgetItem(parent, [parts[depth], ""])
                        node.setFlags(node.flags() | Qt.ItemIsUserCheckable)
                        node.setCheckState(0, Qt.Unchecked)
                        folders[key] = node
                    parent = node
                leaf = QTreeWidgetItem(parent, [parts[-1], fmt_size(file.size)])
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
        self.update_state(item)

    @staticmethod
    def _title(item):
        return item.name or item.id[:12]

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
        if item.state == self._last_state:
            return
        self._last_state = item.state
        self._clear_actions()
        tid = item.id
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

    def __init__(self, engine, bridge, settings, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.settings = settings
        self._cards = {}

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

    def _notify(self, kind, text):
        bar = InfoBar.warning if kind == "warning" else InfoBar.success
        bar(
            title="Торренты", content=text, orient=Qt.Horizontal,
            isClosable=True, position=InfoBarPosition.TOP,
            duration=-1 if kind == "warning" else 4000,
            parent=self.window(),
        )

    # ----------------------------------------------------------- список

    def save_path(self):
        return (self.settings.get("torrent_folder")
                or self.settings["default_folder"])

    def refresh(self):
        items = {item.id: item for item in self.engine.items()}
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
