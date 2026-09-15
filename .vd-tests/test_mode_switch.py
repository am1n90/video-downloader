# -*- coding: utf-8 -*-
"""Офлайн-тест переключателя режимов «Video Downloader | Torrent»
(торрент-стриминг, сессия 1.1).

Сценарии: режим по умолчанию; переключение кнопкой в заголовке; видимость
пунктов навигации и отсутствие «дыры» от скрытых; последняя страница
режима; «Назад» внутри режима и не в чужой режим; общие «Настройки»;
сворачивание/разворачивание панели; двойной клик в заголовке (маршрутизация
событий — реальный разворот окна проверяется вживую, offscreen его не
делает); загрузки не прерываются; режим после перезапуска; неизвестный
режим; switch_to; группы «Настроек»; предупреждение о настройках в режиме
Torrent.
"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
import gui

SAVES = []
config.save = lambda settings: SAVES.append(dict(settings))  # не трогаем settings.json

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from qfluentwidgets import InfoBar, SettingCardGroup
from qfluentwidgets.common.router import qrouter
from qframelesswindow.titlebar.title_bar_buttons import TitleBarButton

PASS, FAIL = [], []


def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details)


app = QApplication(sys.argv)
app.setApplicationName("VD-mode-switch-test")

BASE = config.load()
VIDEO_KEYS = ["downloadInterface", "libraryInterface"]
TORRENT_KEYS = ["torrentInterface", "torrentLibraryInterface"]


def pump(ms=400):
    QTest.qWait(ms)


def new_window(app_mode=None):
    # qrouter общий на процесс: история окон прошлых сценариев не должна
    # влиять на кнопку «Назад» нового окна (в приложении окно одно)
    qrouter.history = []
    settings = dict(BASE)
    settings["history"] = []
    # Без автопроверки обновлений: MainWindow запускает её QThread через
    # 2.5 с; окно теста живёт дольше, закрывается, поток никто не ждёт ->
    # при выходе процесс падал 0xC0000409 (10 из 10; доказано опытом
    # 16.09.2026: без проверки / с wait() — код 0). Офлайн-тесту сеть не нужна.
    settings["check_updates"] = False
    if app_mode is None:
        settings.pop("app_mode", None)
    else:
        settings["app_mode"] = app_mode
    SAVES.clear()
    w = gui.MainWindow(settings)
    w.show()
    pump()
    return w


def visible(w, key):
    item = w.navigationInterface.widget(key)
    return item is not None and item.isVisibleTo(w)


def current_key(w):
    return w.stackedWidget.currentWidget().objectName()


def back_enabled(w):
    return w.navigationInterface.panel.returnButton.isEnabled()


def own_history(w):
    return [i.routeKey for i in qrouter.history if i.stacked is w.stackedWidget]


def item_y(w, key):
    return w.navigationInterface.widget(key).geometry().y()


check("config: app_mode в DEFAULTS = video",
      config.DEFAULTS.get("app_mode") == "video",
      repr(config.DEFAULTS.get("app_mode")))

# ---- A: режим по умолчанию ----
w = new_window()
check("A режим по умолчанию video", w.app_mode == "video", str(w.app_mode))
check("A переключатель на video", w.mode_switch.currentRouteKey() == "video")
check("A переключатель в заголовке окна", w.mode_switch.parent() is w.titleBar)
check("A надпись заголовка скрыта (не дублирует переключатель)",
      not w.titleBar.titleLabel.isVisibleTo(w))
check("A заголовок окна для панели задач прежний", w.windowTitle() == gui.APP_NAME)
check("A пункты Video видны", all(visible(w, k) for k in VIDEO_KEYS))
check("A пункты Torrent скрыты", not any(visible(w, k) for k in TORRENT_KEYS))
check("A «Настройки» видны", visible(w, "settingsInterface"))
check("A открыта «Загрузка»", current_key(w) == "downloadInterface", current_key(w))
check("A «Назад» недоступна", not back_enabled(w))
check("A при старте ничего не сохранялось", SAVES == [], str(len(SAVES)))
y_first, y_second = item_y(w, "downloadInterface"), item_y(w, "libraryInterface")

# ---- B: переключение кнопкой в заголовке ----
w.mode_switch.setCurrentItem("torrent")   # как клик: currentItemChanged
pump()
check("B режим torrent", w.app_mode == "torrent", str(w.app_mode))
check("B пункты Torrent видны", all(visible(w, k) for k in TORRENT_KEYS))
check("B пункты Video скрыты", not any(visible(w, k) for k in VIDEO_KEYS))
check("B «Настройки» видны", visible(w, "settingsInterface"))
check("B открыта «Торренты»", current_key(w) == "torrentInterface", current_key(w))
check("B settings['app_mode'] = torrent", w.settings.get("app_mode") == "torrent")
check("B режим сохранён (config.save)",
      bool(SAVES) and SAVES[-1].get("app_mode") == "torrent", str(len(SAVES)))
check("B «Назад» недоступна после смены режима",
      not back_enabled(w) and own_history(w) == [], str(own_history(w)))
yt, yl = item_y(w, "torrentInterface"), item_y(w, "torrentLibraryInterface")
check("B нет «дыры»: пункты Torrent на местах пунктов Video",
      (yt, yl) == (y_first, y_second), f"{(y_first, y_second)} -> {(yt, yl)}")

# ---- C: последняя страница режима ----
w.switchTo(w.torrent_library_page); pump()
w.set_mode("video"); pump()
check("C Video: «Загрузка» (последняя страница Video)",
      current_key(w) == "downloadInterface", current_key(w))
w.switchTo(w.library_page); pump()
w.set_mode("torrent"); pump()
check("C Torrent: вернулась «Библиотека» Torrent",
      current_key(w) == "torrentLibraryInterface", current_key(w))
w.set_mode("video"); pump()
check("C Video: вернулась «Библиотека» Video",
      current_key(w) == "libraryInterface", current_key(w))

# ---- D: «Назад» внутри режима ----
w.switchTo(w.download_page); pump()
check("D внутри режима «Назад» доступна", back_enabled(w), str(own_history(w)))
qrouter.pop(); pump()
check("D «Назад» -> «Библиотека» Video, режим тот же",
      current_key(w) == "libraryInterface" and w.app_mode == "video", current_key(w))

# ---- E: «Назад» не уводит в чужой режим ----
w.switchTo(w.download_page); pump()          # история Video: библиотека -> загрузка
w.set_mode("torrent"); pump()
before = current_key(w)
qrouter.pop(); pump()
check("E «Назад» после смены режима не уводит в Video",
      current_key(w) == before and w.app_mode == "torrent"
      and not any(visible(w, k) for k in VIDEO_KEYS),
      f"{before} -> {current_key(w)}")

# ---- F: общие «Настройки» ----
w.switchTo(w.settings_page); pump()
check("F «Настройки» открываются в режиме Torrent",
      current_key(w) == "settingsInterface" and w.app_mode == "torrent")
qrouter.pop(); pump()
check("F «Назад» из «Настроек» -> страница Torrent",
      w._page_mode.get(current_key(w)) == "torrent", current_key(w))
w.switchTo(w.settings_page); pump()
w.set_mode("video"); pump()
check("F смена режима из «Настроек» -> страница Video",
      w._page_mode.get(current_key(w)) == "video", current_key(w))

# ---- G: сворачивание / разворачивание панели ----
panel = w.navigationInterface.panel
panel.collapse(); pump(700)
check("G свёрнуто (Video): Torrent скрыты, Video видны",
      not any(visible(w, k) for k in TORRENT_KEYS)
      and all(visible(w, k) for k in VIDEO_KEYS))
panel.expand(useAni=False); pump()
check("G развёрнуто (Video): Torrent скрыты, Video видны",
      not any(visible(w, k) for k in TORRENT_KEYS)
      and all(visible(w, k) for k in VIDEO_KEYS))
w.set_mode("torrent"); pump()
panel.collapse(); pump(700)
check("G свёрнуто (Torrent): Video скрыты, Torrent видны",
      not any(visible(w, k) for k in VIDEO_KEYS)
      and all(visible(w, k) for k in TORRENT_KEYS))
panel.expand(useAni=False); pump()
check("G развёрнуто (Torrent): Video скрыты, Torrent видны",
      not any(visible(w, k) for k in VIDEO_KEYS)
      and all(visible(w, k) for k in TORRENT_KEYS))
panel.collapse(); pump(700)

# ---- H: заголовок окна — перетаскивание и двойной клик ----
tb = w.titleBar
buttons_w = sum(b.width() for b in tb.findChildren(TitleBarButton) if b.isVisible())
empty = QPoint(tb.width() - buttons_w - 30, 20)
check("H пустое место заголовка без дочерних виджетов", tb.childAt(empty) is None,
      str(tb.childAt(empty)))
check("H пустое место заголовка можно тянуть", tb.canDrag(empty))
seg_right = w.mode_switch.mapTo(tb, QPoint(w.mode_switch.width(), 0)).x()
check("H переключатель не заходит под кнопки окна",
      seg_right < tb.width() - buttons_w, f"{seg_right} < {tb.width() - buttons_w}")
# Фильтр событий, а не подмена tb.mouseDoubleClickEvent атрибутом Qt-объекта.
# Двойной клик по заголовку поглощается: offscreen разворот окна через win32
# PostMessage не работает (реальный разворот — check_mode_switch_live.py).


class DoubleClickSpy(QObject):
    def __init__(self):
        super().__init__()
        self.hits = []

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonDblClick:
            self.hits.append(event.position().toPoint())
            return True
        return False


spy = DoubleClickSpy()
tb.installEventFilter(spy)
QTest.mouseDClick(w.mode_switch.items["video"], Qt.LeftButton); pump()
check("H двойной клик по переключателю не доходит до заголовка", spy.hits == [],
      str(spy.hits))
QTest.mouseDClick(tb, Qt.LeftButton, Qt.NoModifier, empty); pump()
check("H двойной клик по пустому заголовку обрабатывает заголовок",
      len(spy.hits) == 1, str(spy.hits))
tb.removeEventFilter(spy)

# ---- I: загрузки не прерываются сменой режима ----
# Планировщик DownloadManager — безымянный daemon-поток _run_scheduler,
# признак запуска — _scheduler_active
import threading


def scheduler_alive(manager):
    return any(getattr(t, "_target", None) == manager._run_scheduler and t.is_alive()
               for t in threading.enumerate())


mgr = w.manager
before = (mgr._scheduler_active.is_set(), scheduler_alive(mgr))
w.set_mode("torrent"); pump()
w.set_mode("video"); pump()
w.set_mode("torrent"); pump()
after = (mgr._scheduler_active.is_set(), scheduler_alive(mgr))
check("I менеджер загрузок тот же, планировщик активен до и после смен режима",
      w.manager is mgr and before == (True, True) and after == (True, True),
      f"before={before} after={after}")
w.close(); pump(200)

# ---- J: режим после перезапуска ----
w2 = new_window("torrent")
check("J режим после перезапуска: torrent",
      w2.app_mode == "torrent" and w2.mode_switch.currentRouteKey() == "torrent")
check("J открыта «Торренты»", current_key(w2) == "torrentInterface", current_key(w2))
check("J Video скрыты, Torrent видны",
      not any(visible(w2, k) for k in VIDEO_KEYS)
      and all(visible(w2, k) for k in TORRENT_KEYS))
check("J «Назад» недоступна", not back_enabled(w2) and own_history(w2) == [],
      str(own_history(w2)))
check("J при старте ничего не сохранялось", SAVES == [], str(len(SAVES)))

# ---- M: предупреждение о повреждённых настройках в режиме Torrent ----
w2.show_config_warning({"skip_saving": False,
                        "corrupt_path": os.path.join(ROOT, "settings.json.corrupt-test")})
bars = w2.findChildren(InfoBar)
check("M предупреждение о настройках показывается в режиме Torrent", len(bars) == 1,
      str(len(bars)))

# ---- N: switch_to ----
w2.switch_to("library"); pump()
check("N switch_to('library') из Torrent переключает режим",
      w2.app_mode == "video" and current_key(w2) == "libraryInterface", current_key(w2))
w2.switch_to("torrent_library"); pump()
check("N switch_to('torrent_library') из Video переключает режим",
      w2.app_mode == "torrent" and current_key(w2) == "torrentLibraryInterface",
      current_key(w2))
w2.switch_to("settings"); pump()
check("N switch_to('settings') режим не меняет",
      w2.app_mode == "torrent" and current_key(w2) == "settingsInterface")

# ---- O: группы «Настроек» ----
titles = [g.titleLabel.text() for g in w2.settings_page.findChildren(SettingCardGroup)]
check("O группы «Настроек»: Общие / Video Downloader / Torrent",
      titles == ["Общие", "Video Downloader", "Torrent"], str(titles))
w2.close(); pump(200)

# ---- K: неизвестный режим ----
w3 = new_window("xyz")
check("K неизвестный режим -> video",
      w3.app_mode == "video" and current_key(w3) == "downloadInterface"
      and w3.settings.get("app_mode") == "video", str(w3.app_mode))
w3.close(); pump(200)

print()
print(f"ИТОГ: {len(PASS)} PASS, {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
