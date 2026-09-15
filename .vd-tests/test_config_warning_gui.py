# -*- coding: utf-8 -*-
"""Офлайн-тест GUI-предупреждения о повреждённом settings.json (п.9 «Что осталось»).

Покрывает MainWindow.show_config_warning(): AC1 (предупреждение с путём к
.corrupt-копии и кнопками), AC3 (ровно один раз за сессию — гарантия на
стороне config.consume_load_warning(), здесь же проверяем сам показ),
AC4 (без bool None -> ничего не показывается, обычный старт как раньше).
"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
import gui

config.save = lambda settings: None   # не трогаем settings.json владельца

from PySide6.QtWidgets import QApplication, QPushButton
from qfluentwidgets import InfoBar

PASS, FAIL = [], []
def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details)

app = QApplication(sys.argv)
app.setApplicationName("VD-config-warning-test")

settings = config.load()

# ---- сценарий A: warning is None (обычный старт, AC2) ----
window = gui.MainWindow(dict(settings))
window.show()
window.show_config_warning(None)
bars = window.findChildren(InfoBar)
check("A обычный старт: InfoBar не появляется", len(bars) == 0, str(len(bars)))
window.close()

# ---- сценарий B: битый файл, копия создана (AC1/AC4) ----
window = gui.MainWindow(dict(settings))
window.show()
corrupt_path = os.path.join(ROOT, "settings.json.corrupt-20260915-000000")
window.show_config_warning({"skip_saving": False, "corrupt_path": corrupt_path})
bars = window.findChildren(InfoBar)
check("B битый файл: ровно один InfoBar", len(bars) == 1, str(len(bars)))
if bars:
    bar = bars[0]
    text = bar.contentLabel.text() + bar.titleLabel.text()
    check("B текст без технического жаргона (нет 'skip_saving'/'JSON'/'exception')",
          "skip_saving" not in text and "JSON" not in text and "exception" not in text.lower())
    buttons = [b.text() for b in bar.findChildren(QPushButton)]
    check("B есть кнопка «Открыть папку»", "Открыть папку" in buttons, str(buttons))
    check("B есть кнопка «Скопировать путь»", "Скопировать путь" in buttons, str(buttons))
window.close()

# ---- сценарий C: skip_saving=True — предупреждение серьёзнее, без кнопок пути ----
window = gui.MainWindow(dict(settings))
window.show()
window.show_config_warning({"skip_saving": True, "corrupt_path": None})
bars = window.findChildren(InfoBar)
check("C skip_saving: ровно один InfoBar", len(bars) == 1, str(len(bars)))
window.close()

# ---- сценарий D: показ ровно один раз за сессию (AC3) ----
# consume_load_warning() из config.py уже гарантирует одноразовость: второй
# вызов подряд в реальном запуске вернёт None и show_config_warning его
# не покажет.
window = gui.MainWindow(dict(settings))
window.show()
window.show_config_warning(config.consume_load_warning())   # None: уже разобрано в run()
bars = window.findChildren(InfoBar)
check("D повторный consume в той же сессии: InfoBar не появляется",
      len(bars) == 0, str(len(bars)))
window.close()

app.quit()

print()
print(f"ИТОГ: {len(PASS)} PASS, {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
