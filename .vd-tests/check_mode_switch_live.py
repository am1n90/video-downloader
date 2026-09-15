# -*- coding: utf-8 -*-
"""Живая проверка переключателя режимов на НАСТОЯЩЕМ окне (не offscreen).

Окно появляется на экране на ~15 секунд; курсор мыши кликает ВНУТРИ этого
окна и возвращается на место. Клики — настоящие (win32 SetCursorPos +
mouse_event), не QTest: синтетический двойной клик QTest на настоящем окне
теряет отпускание кнопки (найдено 16.09.2026). Перед каждым кликом скрипт
проверяет, что под точкой именно наше окно; если нет — это FAIL, а не пропуск.

Проверяет:
  - клик по «Torrent» и «Video Downloader» меняет режим;
  - двойной клик по пустому месту заголовка разворачивает окно;
  - снимки экрана настоящими пикселями (QScreen.grabWindow, не
    widget.grab — тот теряет Mica-фон): Video, Torrent, «Настройки»,
    светлая тема — PNG в %TEMP%\\vd-mode-live.
settings.json не сохраняется (config.save подменён).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
import gui
import single_instance

config.save = lambda settings: None

import win32api
import win32con
import win32gui
from PySide6.QtCore import QPoint
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from qframelesswindow.titlebar.title_bar_buttons import TitleBarButton

OUT = os.path.join(os.environ["TEMP"], "vd-mode-live")
os.makedirs(OUT, exist_ok=True)

PASS, FAIL = [], []


def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details, flush=True)


app = QApplication(sys.argv)
settings = config.load()
settings["history"] = []
settings["app_mode"] = "video"
# без сетевой автопроверки обновлений: её поток, не законченный к выходу,
# роняет процесс (0xC0000409) — код выхода проверки зависел бы от GitHub
settings["check_updates"] = False
theme_before = settings["theme"]
w = gui.MainWindow(settings)
w.resize(1000, 680)
w.move(120, 120)
w.show()
QTest.qWait(800)
hwnd = int(w.winId())
# force_foreground — только до разворота: он возвращает окно в обычный размер
single_instance.force_foreground(hwnd)
QTest.qWait(700)


def physical(widget, point):
    g = widget.mapToGlobal(point)
    dpr = widget.window().windowHandle().screen().devicePixelRatio()
    return int(round(g.x() * dpr)), int(round(g.y() * dpr))


def real_click(widget, point, double=False):
    """Настоящий клик; False, если под точкой не наше окно."""
    x, y = physical(widget, point)
    under = win32gui.WindowFromPoint((x, y))
    if not under or win32gui.GetAncestor(under, win32con.GA_ROOT) != hwnd:
        return False
    saved = win32api.GetCursorPos()
    win32api.SetCursorPos((x, y))
    for _ in range(2 if double else 1):
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        QTest.qWait(60)
    win32api.SetCursorPos(saved)
    return True


def click_mode(key):
    item = w.mode_switch.items[key]
    done = real_click(item, item.rect().center())
    QTest.qWait(800)
    return done


def shot(name):
    QTest.qWait(400)
    geo = w.frameGeometry()
    pix = QGuiApplication.primaryScreen().grabWindow(
        0, geo.x(), geo.y(), geo.width(), geo.height())
    path = os.path.join(OUT, name)
    check(f"снимок экрана {name}", pix.save(path), path)


shot("1-video.png")
done = click_mode("torrent")
check("клик по «Torrent» меняет режим", done and w.app_mode == "torrent",
      f"clicked={done} mode={w.app_mode}")
shot("2-torrent.png")
w.switchTo(w.settings_page)
QTest.qWait(700)
shot("3-settings.png")
done = click_mode("video")
check("клик по «Video Downloader» меняет режим", done and w.app_mode == "video",
      f"clicked={done} mode={w.app_mode}")

tb = w.titleBar
buttons_w = sum(b.width() for b in tb.findChildren(TitleBarButton) if b.isVisible())
done = real_click(tb, QPoint(tb.width() - buttons_w - 30, 20), double=True)
QTest.qWait(1200)
check("двойной клик по пустому заголовку разворачивает окно",
      done and w.isMaximized(), f"clicked={done}")
w.showNormal()
QTest.qWait(1000)
check("окно возвращается в обычный размер", not w.isMaximized())

w._apply_theme("light", save=False)
done = click_mode("torrent")
check("клик по «Torrent» в светлой теме", done and w.app_mode == "torrent",
      f"clicked={done} mode={w.app_mode}")
shot("4-torrent-light.png")
w._apply_theme(theme_before, save=False)
QTest.qWait(300)

w.close()
print(f"ИТОГ: {len(PASS)} PASS, {len(FAIL)} FAIL; снимки: {OUT}", flush=True)
sys.exit(1 if FAIL else 0)
