# -*- coding: utf-8 -*-
"""P1-тесты: применение max_concurrent на лету (BUG-5)."""
import os, sys, time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config

config.save = lambda settings: None
config.get_history = lambda settings: []

import downloader
import gui
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

# Симуляция загрузки (как в test_core)
def fake_run_item(self, item):
    try:
        item.status = downloader.STATUS_DOWNLOADING
        self._notify(item)
        while True:
            if item._cancel.is_set():
                raise downloader.DownloadCancelled()
            time.sleep(0.01)
    except downloader.DownloadCancelled:
        item.status = downloader.STATUS_ERROR
        item.error = "Отменено"
        self._notify(item)
    finally:
        self._wake.set()

downloader.DownloadManager._run_item = fake_run_item

settings = dict(config.DEFAULTS)
settings["check_updates"] = False
window = gui.MainWindow(settings)
window.show()

mgr = window.manager
mgr.set_max_concurrent(1)

items = [mgr.add(f"http://item{i}") for i in range(4)]
time.sleep(1.0)

def count_downloading():
    return sum(1 for i in mgr.items.values()
               if i.status == downloader.STATUS_DOWNLOADING)

d1 = count_downloading()
print("max_concurrent=1: скачивается", d1)
ok1 = d1 == 1

# Меняем на лету через настройки (эмуляция SpinBox -> сигнал)
window.settings_page.maxConcurrentChanged.emit(3)
time.sleep(1.2)
d2 = count_downloading()
print("после emit(3): скачивается", d2)
ok2 = d2 == 3

# Уменьшаем обратно
window.settings_page.maxConcurrentChanged.emit(2)
time.sleep(0.5)
print("после emit(2): активных (не убивает работающие):", count_downloading())

for i in mgr.items.values():
    mgr.cancel(i.id)
time.sleep(0.5)

print("P1 max_concurrent на лету:", "OK" if (ok1 and ok2) else f"FAIL: {ok1} {ok2}")
sys.exit(0 if (ok1 and ok2) else 1)
