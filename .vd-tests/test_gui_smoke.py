# -*- coding: utf-8 -*-
"""Smoke-тест GUI без экрана (offscreen): все страницы создаются, таймер закрывает приложение."""
import os, sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
import gui

# Не трогаем settings.json владельца во время теста
config.save = lambda settings: None

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

app = QApplication(sys.argv)
app.setApplicationName("VD-smoke")

settings = config.load()
window = gui.MainWindow(settings)
window.show()

result = {"closed": False}

def finish():
    window.close()          # здесь сработает closeEvent
    result["closed"] = True
    app.quit()

QTimer.singleShot(1500, finish)   # раньше авто-проверки обновлений (2.5с) — сеть не трогаем
app.exec()

assert result["closed"], "окно не закрылось"
# Проверка страниц
assert window.download_page is not None
assert window.library_page is not None
assert window.settings_page is not None
print("SMOKE_OK: MainWindow + 3 страницы созданы и закрылись без падения")
