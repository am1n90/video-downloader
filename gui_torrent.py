"""Режим «Torrent» — страницы (торрент-стриминг, Этап 1).

1.1: только каркас переключателя режимов — страницы-заглушки без логики.
Движок (torrent_engine.py) и настоящие страницы — сессии 1.2-1.4.

Импортируется из gui.MainWindow.__init__, а не с верхнего уровня gui.py:
модуль сам берёт общие виджеты и отступы из gui.
"""

from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, TitleLabel

from gui import SP_BLOCK, SP_WINDOW, TransparentScrollArea


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


class TorrentPage(_PlaceholderPage):
    def __init__(self, parent=None):
        super().__init__(
            "Торренты",
            "Раздел в разработке. Здесь появятся добавление magnet-ссылок "
            "и .torrent-файлов, выбор файлов раздачи и очередь загрузок.",
            parent,
        )


class TorrentLibraryPage(_PlaceholderPage):
    def __init__(self, parent=None):
        super().__init__(
            "Библиотека",
            "Раздел в разработке. Здесь появятся скачанные раздачи.",
            parent,
        )
