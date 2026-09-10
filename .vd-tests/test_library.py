# -*- coding: utf-8 -*-
"""Тест BUG-1 (краш Библиотеки): многократные refresh + поиск + фильтр —
без RuntimeError и без дублей строк."""
import os, sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config

config.save = lambda settings: None
config.get_history = lambda settings: []   # историю владельца не трогаем

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEvent, QTimer
import gui

app = QApplication(sys.argv)

settings = dict(config.DEFAULTS)
tmpd = os.path.join(os.environ.get("TEMP", "/tmp"), "vd-lib-test")
os.makedirs(tmpd, exist_ok=True)

names = ["a.mp4", "b.mp3", "c.mkv"]
for n in names:
    open(os.path.join(tmpd, n), "wb").write(b"x" * 100)
settings["default_folder"] = tmpd

# Библиотека показывает названия без расширений ("title": splitext(name)[0])
titles_expected = ["a", "b", "c"]

window = gui.MainWindow(settings)
window.show()
lib = window.library_page

QTimer.singleShot(300, app.quit)
app.exec()

def settle():
    app.processEvents()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()

def row_titles():
    settle()
    return sorted(r.entry.get("title") for r in lib.findChildren(gui.LibraryRow))

errors = []

# --- 7 refresh подряд (раньше 3-й падал с RuntimeError) ---
try:
    for i in range(7):
        lib.refresh()
        titles = row_titles()
        if titles != titles_expected:
            errors.append(f"refresh #{i+1}: дубликаты/потеря: {titles}")
    print("7x refresh: OK, строки:", row_titles())
except RuntimeError as e:
    errors.append(f"краш при refresh: {e}")

# --- ввод текста в поиск (каждый символ = refresh) ---
try:
    for ch in "az":
        lib.search.setText(lib.search.text() + ch)
        settle()
    print("поиск 'az': строк:", len(row_titles()), "(ожидается 0)")
    lib.search.clear()
    settle()
    print("поиск сброшен: строк:", len(row_titles()))
except RuntimeError as e:
    errors.append(f"краш при поиске: {e}")

# --- фильтр по источнику ---
try:
    idx = lib.source_filter.findText("Папка загрузок")
    lib.source_filter.setCurrentIndex(idx if idx >= 0 else 0)
    settle()
    print("фильтр: строк:", len(row_titles()))
    lib.source_filter.setCurrentIndex(0)
    settle()
except RuntimeError as e:
    errors.append(f"краш при фильтре: {e}")

if errors:
    print("БИБЛИОТЕКА: ОШИБКИ:")
    for e in errors:
        print("  -", e)
    sys.exit(1)
print("БИБЛИОТЕКА: OK — ни крашей, ни дублей")
sys.exit(0)

