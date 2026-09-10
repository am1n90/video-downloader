# -*- coding: utf-8 -*-
"""Диагностика: что происходит в check_updates установленного приложения
(запуск в offscreen с настройками из %LOCALAPPDATA% — только чтение)."""
import os, sys, threading

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
import updater

url = "http://127.0.0.1:8765/latest.json"
print("1) fetch_manifest напрямую:")
try:
    m = updater.fetch_manifest(url)
    print("   ->", m)
except Exception as e:
    print("   EXCEPTION:", type(e).__name__, e)

print("2) UpdateWorker (как в GUI):")
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
import gui

app = QApplication(sys.argv)

# подменяем путь настроек на установленные (только чтение!)
config.CONFIG_PATH = os.path.join(os.environ["LOCALAPPDATA"],
                                  "VideoDownloader", "settings.json")
settings = config.load()
print("3) settings после load(): update_manifest_url =",
      settings.get("update_manifest_url"))

w = gui.UpdateWorker("check", manifest_url=settings.get("update_manifest_url"))
got = {}
w.manifestReady.connect(lambda m: got.setdefault("ready", m))
w.manifestError.connect(lambda e: got.setdefault("err", e))
w.start()

loop = QEventLoop()
QTimer.singleShot(8000, loop.quit)
loop.exec()

print("4) результат воркера:", got)
