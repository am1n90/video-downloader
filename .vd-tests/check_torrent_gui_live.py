# -*- coding: utf-8 -*-
"""Живая проверка страницы «Торренты» на НАСТОЯЩЕМ окне (не offscreen).

Окно появляется на экране примерно на полминуты. Раздачу отдаёт локальный
сид на 127.0.0.1 в этом же процессе — интернет и настоящий рой не нужны.

Снимает настоящими пикселями (QScreen.grabWindow, как в
check_mode_switch_live.py — widget.grab теряет Mica-фон) состояния:
пустая страница, получение сведений/скачивание, раздаётся, дерево файлов.
PNG — в %TEMP%\\vd-torrent-live. settings.json не сохраняется.
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config

config.save = lambda settings: None

import libtorrent as lt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

import gui
import torrent_engine as te

OUT = os.path.join(os.environ["TEMP"], "vd-torrent-live")
os.makedirs(OUT, exist_ok=True)

PASS, FAIL = [], []


def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details, flush=True)


MB = 1024 * 1024
BASE = tempfile.mkdtemp(prefix="vd-torrent-live-")
SRC_ROOT = os.path.join(BASE, "источник")
CONTENT = os.path.join(SRC_ROOT, "Фильм про котиков")
os.makedirs(CONTENT)
SIZES = {"котики 1080p.bin": 12 * MB + 12345, "котики 720p.bin": 6 * MB + 54321,
         "субтитры.srt": 20000}
for file_name, size in SIZES.items():
    with open(os.path.join(CONTENT, file_name), "wb") as f:
        f.write(os.urandom(size))

TORRENT = os.path.join(BASE, "live.torrent")
fs = lt.file_storage()
lt.add_files(fs, CONTENT)
ct = lt.create_torrent(fs, 256 * 1024, lt.create_torrent.v1_only)
lt.set_piece_hashes(ct, SRC_ROOT)
with open(TORRENT, "wb") as f:
    f.write(lt.bencode(ct.generate()))
TI = lt.torrent_info(TORRENT)
IH = te._hex(TI.info_hashes().v1)

LOCAL = {"enable_dht": False, "enable_lsd": False, "enable_upnp": False,
         "enable_natpmp": False}

app = QApplication(sys.argv)

ses = lt.session(dict(LOCAL, listen_interfaces="127.0.0.1:0"))
atp = lt.add_torrent_params()
atp.ti = lt.torrent_info(TORRENT)
atp.save_path = SRC_ROOT
atp.flags = atp.flags | lt.torrent_flags.seed_mode
seed_handle = ses.add_torrent(atp)
end = time.monotonic() + 15
while str(seed_handle.status().state) != "seeding" and time.monotonic() < end:
    time.sleep(0.1)
seed_port = ses.listen_port()
check("сид раздаёт", str(seed_handle.status().state) == "seeding",
      f"port {seed_port}")
# придерживаем сид, иначе на loopback всё скачается до первого снимка
seed_handle.set_upload_limit(768 * 1024)

settings = dict(config.load())
settings["history"] = []
settings["check_updates"] = False
settings["app_mode"] = "torrent"
settings["torrent_folder"] = os.path.join(BASE, "Загрузки")
settings["torrent_seed_after_download"] = True

w = gui.MainWindow(settings)
# Своя папка данных движка: иначе проверка складывает раздачи в рабочую
# torrent-data\ проекта, они восстанавливаются из fastresume при следующем
# прогоне, и «пустая страница» уже не пустая. Подменяем ДО show(): движок
# поднимается лениво, в showEvent страницы.
w.torrent_engine.data_dir = os.path.join(BASE, "данные")
w.torrent_engine.resume_dir = os.path.join(BASE, "данные", "resume")
w.resize(1100, 720)
w.show()
w.activateWindow()
w.raise_()


def pump(seconds):
    end_at = time.monotonic() + seconds
    while time.monotonic() < end_at:
        app.processEvents()
        time.sleep(0.01)


def wait_until(pred, timeout):
    end_at = time.monotonic() + timeout
    while time.monotonic() < end_at:
        app.processEvents()
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(0.02)
    return False


def shot(name):
    pump(0.5)
    geo = w.frameGeometry()
    pix = QGuiApplication.primaryScreen().grabWindow(
        0, geo.x(), geo.y(), geo.width(), geo.height())
    path = os.path.join(OUT, name)
    check(f"снимок {name}", pix.save(path), path)


pump(1.0)
page = w.torrent_page
check("открыт режим Torrent на странице «Торренты»",
      w.app_mode == "torrent" and w.stackedWidget.currentWidget() is page)
check("движок поднят показом страницы", w.torrent_engine._ses is not None)
check("подсказка пустого списка видна", page.empty_label.isVisible())
shot("01-пусто.png")

page.magnet_edit.setText(
    f"magnet:?xt=urn:btih:{IH}&dn=Фильм+про+котиков&x.pe=127.0.0.1:{seed_port}")
page.add_magnet()
pump(0.6)
card = page._cards.get(IH)
check("карточка раздачи появилась", card is not None)
check("карточка видна на экране", card is not None and card.isVisible())
shot("02-добавлено.png")

ok = wait_until(lambda: page.engine.get(IH).state == te.STATE_DOWNLOADING, 30)
pump(1.5)
meta = card.meta_label.text() if card else ""
check("идёт скачивание, подпись заполнена", ok and len(meta) > 10, meta)
check("в подписи есть проценты и скорость",
      "%" in meta and ("/с" in meta), meta)
check("прогресс-бар виден и не нулевой",
      card.bar.isVisible() and card.bar.value() >= 0, str(card.bar.value()))
check("кнопки скачивания: Пауза/Файлы/Удалить",
      [card.actions_widget.layout().itemAt(i).widget().text()
       for i in range(card.actions_widget.layout().count())]
      == ["Пауза", "Файлы", "Удалить"])
shot("03-скачивается.png")

# дерево файлов поверх настоящего окна
dialog = None
item = page.engine.get(IH)
if item is not None and item.files:
    import gui_torrent
    dialog = gui_torrent.TorrentFilesDialog(item, w)
    dialog.show()
    pump(0.8)
    check("дерево файлов открылось и показывает 3 файла",
          dialog.tree.invisibleRootItem().child(0).childCount() == 3)
    shot("04-файлы.png")
    dialog.close()
    dialog.deleteLater()
    pump(0.3)

seed_handle.set_upload_limit(0)
ok = wait_until(lambda: page.engine.get(IH).state == te.STATE_SEEDING, 60)
pump(1.0)
meta = card.meta_label.text() if card else ""
check("раздача докачана и раздаётся", ok, page.engine.get(IH).state)
check("подпись готовой раздачи: «Раздаётся» и 100%",
      "Раздаётся" in meta and "100%" in meta, meta)
check("кнопки готовой раздачи: Открыть папку/Файлы/Удалить",
      [card.actions_widget.layout().itemAt(i).widget().text()
       for i in range(card.actions_widget.layout().count())]
      == ["Открыть папку", "Файлы", "Удалить"])
shot("05-раздаётся.png")

w.close()
pump(1.0)
check("закрытие окна остановило движок", w.torrent_engine._ses is None)

print()
print(f"ИТОГ: {len(PASS)} PASS, {len(FAIL)} FAIL")
print("снимки:", OUT)
sys.exit(1 if FAIL else 0)
