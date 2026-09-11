# -*- coding: utf-8 -*-
"""Живая проверка AC11 (offscreen): dev settings.json с тестовыми данными
(make_demo_data.py) -> MainWindow -> Библиотека. Автоматическая часть;
визуальную часть владелец проверяет запуском python main.py.
"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
import gui

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEvent, QTimer

app = QApplication(sys.argv)

settings = config.load()          # реальный dev settings.json
window = gui.MainWindow(settings)
window.show()
lib = window.library_page

QTimer.singleShot(300, app.quit)
app.exec()


def settle():
    app.processEvents()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


lib.refresh()
settle()
titles = [r.entry.get("title") for r in lib.findChildren(gui.LibraryRow)]
sources = [lib.source_filter.itemText(i)
           for i in range(lib.source_filter.count())]

print("строк Библиотеки:", len(titles), titles)
print("фильтр источников:", sources)
print("«Очистить данные библиотеки» активна:", lib.clear_btn.isEnabled())
print("«Удалить» без выбора:", lib.delete_btn.isEnabled())

ok = True
if titles != ["Демо-ролик 1 (новее)", "Демо-ролик 2", "Демо-ролик 3"]:
    print("FAIL: ожидались 3 строки (дубли demo1 схлопнуты, ghost скрыт)")
    ok = False
if "Папка загрузок" in sources or "Все" not in sources:
    print("FAIL: фильтр источников неверен")
    ok = False
if not lib.clear_btn.isEnabled() or lib.delete_btn.isEnabled():
    print("FAIL: неверное начальное состояние кнопок")
    ok = False

window.close()
print("LIVE", "OK" if ok else "FAIL",
      "— дедуп/скрытие ghost/фильтр/кнопки на реальном settings.json")
sys.exit(0 if ok else 1)
