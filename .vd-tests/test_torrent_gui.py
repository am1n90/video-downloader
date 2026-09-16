# -*- coding: utf-8 -*-
"""Офлайн-тест GUI режима Torrent поверх движка (сессия 1.3).

Сеть — только 127.0.0.1: свою маленькую раздачу (3 файла, кириллица и
пробелы в путях) раздаёт второй экземпляр libtorrent в этом же процессе,
как в test_torrent_engine.py.

Ожидание — processEvents + sleep, а НЕ QTest.qWait: колбэки движка идут из
фонового потока и доходят до страницы очередью сигналов Qt.

Сценарии: ленивый запуск движка при показе страницы; добавление magnet;
карточка и её подпись; кнопки по состояниям (пауза/продолжение/ошибка);
дерево файлов с галочками; применение выбора файлов; удаление с файлами;
группа «Torrent» в Настройках и её связь с живым движком; closeEvent.
"""
import dataclasses
import hashlib
import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config

SAVES = []
config.save = lambda settings: SAVES.append(dict(settings))  # не трогаем settings.json

import libtorrent as lt
from PySide6.QtWidgets import QApplication

import gui
import gui_torrent
import torrent_engine as te

PASS, FAIL = [], []


def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details, flush=True)


MB = 1024 * 1024
BASE = tempfile.mkdtemp(prefix="vd-gui-torrent-")
SRC_ROOT = os.path.join(BASE, "источник")
CONTENT = os.path.join(SRC_ROOT, "Тестовая раздача")
os.makedirs(CONTENT)
SIZES = {"видео 1.bin": 2 * MB + 12345, "видео 2.bin": 1 * MB + 54321,
         "описание.txt": 20000}
for file_name, size in SIZES.items():
    with open(os.path.join(CONTENT, file_name), "wb") as f:
        f.write(os.urandom(size))

TORRENT = os.path.join(BASE, "test.torrent")
fs = lt.file_storage()
lt.add_files(fs, CONTENT)
ct = lt.create_torrent(fs, 256 * 1024, lt.create_torrent.v1_only)
lt.set_piece_hashes(ct, SRC_ROOT)
with open(TORRENT, "wb") as f:
    f.write(lt.bencode(ct.generate()))
TI = lt.torrent_info(TORRENT)
IH = te._hex(TI.info_hashes().v1)
FILE_ORDER = [TI.files().file_path(i) for i in range(TI.num_files())]

LOCAL = {"enable_dht": False, "enable_lsd": False, "enable_upnp": False,
         "enable_natpmp": False}
ENGINE_SETTINGS = dict(LOCAL, min_reconnect_time=1)

app = QApplication(sys.argv)
app.setApplicationName("VD-torrent-gui-test")


class Seed:
    def __init__(self):
        self.ses = lt.session(dict(LOCAL, listen_interfaces="127.0.0.1:0"))
        atp = lt.add_torrent_params()
        atp.ti = lt.torrent_info(TORRENT)
        atp.save_path = SRC_ROOT
        atp.flags = atp.flags | lt.torrent_flags.seed_mode
        self.h = self.ses.add_torrent(atp)
        end = time.monotonic() + 15
        while str(self.h.status().state) != "seeding" and time.monotonic() < end:
            time.sleep(0.1)
        self.port = self.ses.listen_port()


seed = Seed()
check("подготовка: сид раздаёт", str(seed.h.status().state) == "seeding",
      f"port {seed.port}")


def pump(seconds=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_until(pred, timeout=40):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(0.02)
    try:
        return bool(pred())
    except Exception:
        return False


def magnet():
    return f"magnet:?xt=urn:btih:{IH}&dn=test&x.pe=127.0.0.1:{seed.port}"


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def same_as_source(save_path, rel):
    got = os.path.join(save_path, rel)
    return os.path.isfile(got) and sha(got) == sha(os.path.join(SRC_ROOT, rel))


PAGES = []


def make_page(name):
    """Страница с собственным движком и Bridge — как их вяжет MainWindow."""
    save_dir = os.path.join(BASE, "Загрузки с пробелом", name)
    bridge = gui.Bridge()
    engine = te.TorrentEngine(
        data_dir=os.path.join(BASE, "данные", name),
        on_change=lambda item: bridge.itemChanged.emit(item),
        on_list_change=lambda: bridge.queueChanged.emit(),
        listen_interfaces="127.0.0.1:0", extra_settings=ENGINE_SETTINGS,
    )
    settings = {"torrent_folder": save_dir, "default_folder": save_dir}
    page = gui_torrent.TorrentPage(engine, bridge, settings)
    PAGES.append((page, engine))
    return page, engine, save_dir


def buttons(card):
    layout = card.actions_widget.layout()
    out = []
    for i in range(layout.count()):
        widget = layout.itemAt(i).widget()
        if widget is not None:
            out.append(widget.text())
    return out


def state_of(page, tid):
    item = page.engine.get(tid)
    return None if item is None else item.state


# ---- 1: ленивый запуск движка + добавление magnet ----
page1, eng1, save1 = make_page("s1")
check("1 движок не поднят, пока страницу не показали", eng1._ses is None)
page1.show()
pump(0.3)
check("1 показ страницы поднял движок", eng1._ses is not None)
check("1 пустой список: подсказка видна, контейнер скрыт",
      page1.empty_label.isVisible() and not page1.rows_container.isVisible())

page1.magnet_edit.setText(magnet())
added = page1.add_magnet()
pump(0.3)
check("1 magnet добавлен, поле очищено",
      added and page1.magnet_edit.text() == "", page1.magnet_edit.text())
check("1 карточка появилась", len(page1._cards) == 1, str(list(page1._cards)))
card1 = page1._cards.get(IH)
check("1 подсказка скрыта, список показан",
      card1 is not None and not page1.empty_label.isVisible()
      and page1.rows_container.isVisible())
check("1 пустая magnet-ссылка не добавляется", page1.add_magnet("") is False)

ok = wait_until(lambda: state_of(page1, IH) == te.STATE_SEEDING, 40)
pump(0.4)
check("1 раздача докачана и раздаётся", ok, state_of(page1, IH))
check("1 файлы совпадают с источником",
      wait_until(lambda: all(same_as_source(save1, rel) for rel in FILE_ORDER),
                 15))

# ---- 2: подпись карточки и кнопки по состояниям ----
item1 = page1.engine.get(IH)
meta = card1.meta_label.text()
check("2 подпись: состояние «Раздаётся» и 100%",
      "Раздаётся" in meta and "100%" in meta, meta)
check("2 подпись: объём считается по выбранным файлам",
      gui.fmt_mb(item1.selected_size) in meta, meta)
check("2 прогресс-бар заполнен", card1.bar.value() == 100, str(card1.bar.value()))
check("2 у готовой раздачи есть «Открыть папку», «Файлы», «Удалить»",
      buttons(card1) == ["Открыть папку", "Файлы", "Удалить"], str(buttons(card1)))

# Пауза у ДОКАЧАННОЙ раздачи — это «Готово» (скачано, раздача выключена),
# состояние paused бывает только у незавершённой (см. _snapshot движка)
page1.pause(IH)
ok = wait_until(lambda: state_of(page1, IH) == te.STATE_FINISHED, 10)
pump(0.3)
check("2 пауза докачанной раздачи -> «Готово»", ok, state_of(page1, IH))
page1.resume(IH)
ok = wait_until(lambda: state_of(page1, IH) == te.STATE_SEEDING, 15)
pump(0.3)
check("2 продолжение вернуло раздачу в раздачу", ok, state_of(page1, IH))

# Состояние error в карточке: движок в это время трогать не нужно —
# проверяем, что карточка предлагает «Повторить» (иначе libtorrent
# промолчит 10 минут, находка 2)
broken = dataclasses.replace(page1.engine.get(IH), state=te.STATE_ERROR,
                             error="file_open (файл занят)",
                             error_file=os.path.join(save1, FILE_ORDER[0]))
card1.update_state(broken)
check("2 ошибка: «Повторить» первой кнопкой",
      buttons(card1)[0] == "Повторить", str(buttons(card1)))
check("2 ошибка: в подписи текст и имя файла",
      "file_open" in card1.meta_label.text()
      and os.path.basename(FILE_ORDER[0]) in card1.meta_label.text(),
      card1.meta_label.text())
card1.update_state(page1.engine.get(IH))      # вернуть настоящее состояние

# Пауза посреди скачивания — состояние paused и кнопка «Продолжить».
# Сид придерживаем, иначе раздача успевает докачаться до паузы.
seed.h.set_upload_limit(64 * 1024)
page2, eng2, save2 = make_page("s2")
page2.show()
pump(0.3)
page2.magnet_edit.setText(magnet())
page2.add_magnet()
started = wait_until(lambda: (page2.engine.get(IH) is not None)
                     and 0 < page2.engine.get(IH).progress < 1.0, 30)
page2.pause(IH)
ok = wait_until(lambda: state_of(page2, IH) == te.STATE_PAUSED, 10)
pump(0.3)
card2 = page2._cards.get(IH)
check("2 пауза посреди скачивания: состояние и кнопка «Продолжить»",
      started and ok and buttons(card2)[0] == "Продолжить",
      f"{state_of(page2, IH)} {buttons(card2)}")
seed.h.set_upload_limit(0)
page2.resume(IH)
ok = wait_until(lambda: state_of(page2, IH) in (te.STATE_DOWNLOADING,
                                               te.STATE_SEEDING), 20)
pump(0.3)
check("2 продолжение возобновляет скачивание", ok, state_of(page2, IH))

# ---- 3: дерево файлов с галочками ----
item1 = page1.engine.get(IH)
dialog = gui_torrent.TorrentFilesDialog(item1, page1)
root = dialog.tree.invisibleRootItem()
check("3 дерево: одна папка верхнего уровня", root.childCount() == 1,
      str(root.childCount()))
folder = root.child(0)
check("3 дерево: папка раздачи с тремя файлами",
      folder.text(0) == "Тестовая раздача" and folder.childCount() == 3,
      f"{folder.text(0)} / {folder.childCount()}")
check("3 дерево: все файлы отмечены и папка целиком отмечена",
      dialog.priorities() == [4, 4, 4]
      and folder.checkState(0) == gui_torrent.Qt.Checked,
      str(dialog.priorities()))

folder.child(1).setCheckState(0, gui_torrent.Qt.Unchecked)
pump(0.1)
check("3 снятая галочка файла -> приоритет 0",
      dialog.priorities() == [4, 0, 4], str(dialog.priorities()))
check("3 папка стала частично отмеченной",
      folder.checkState(0) == gui_torrent.Qt.PartiallyChecked,
      str(folder.checkState(0)))
check("3 «Выбрано» считает сумму выбранных файлов",
      dialog.selected_size() == item1.files[0].size + item1.files[2].size
      and gui.fmt_mb(dialog.selected_size()) in dialog.size_label.text(),
      dialog.size_label.text())

folder.setCheckState(0, gui_torrent.Qt.Unchecked)
pump(0.1)
check("3 снятая папка снимает все файлы", dialog.priorities() == [0, 0, 0],
      str(dialog.priorities()))
check("3 без единого файла «Применить» недоступна",
      not dialog.yesButton.isEnabled())
folder.setCheckState(0, gui_torrent.Qt.Checked)
pump(0.1)
check("3 отмеченная папка возвращает все файлы и «Применить»",
      dialog.priorities() == [4, 4, 4] and dialog.yesButton.isEnabled(),
      str(dialog.priorities()))
dialog.close()
dialog.deleteLater()

# ---- 4: применение выбора файлов ----
page4, eng4, save4 = make_page("s4")
page4.show()
pump(0.3)
# Сид придерживаем: 3 МБ на loopback скачиваются быстрее, чем успевает
# примениться выбор файлов, и «скачан только выбранный» стало бы гонкой
seed.h.set_upload_limit(64 * 1024)
page4.magnet_edit.setText(magnet())
page4.add_magnet()
check("4 метаданные получены",
      wait_until(lambda: (page4.engine.get(IH) or None)
                 and page4.engine.get(IH).has_metadata, 25))
page4.ask_files = lambda item: [4, 0, 0]       # вместо диалога
check("4 выбор файлов применён", page4.choose_files(IH))
# prioritize_files асинхронный (находка 21) — ждём, а не проверяем сразу
ok = wait_until(
    lambda: [f.priority for f in page4.engine.get(IH).files] == [4, 0, 0], 15)
check("4 приоритеты дошли до движка", ok,
      str([f.priority for f in page4.engine.get(IH).files]))
# Кэш файлов не должен «залипнуть» на старых приоритетах: иначе выбор
# применяется, но в снимке и в дереве с галочками числятся все файлы
item4 = page4.engine.get(IH)
check("4 selected_size считает только выбранное",
      item4.selected_size == item4.files[0].size,
      f"{item4.selected_size} vs {item4.files[0].size}")
reopened = gui_torrent.TorrentFilesDialog(item4, page4)
check("4 повторно открытый диалог показывает прежний выбор",
      reopened.priorities() == [4, 0, 0], str(reopened.priorities()))
reopened.close()
reopened.deleteLater()
seed.h.set_upload_limit(0)
ok = wait_until(lambda: state_of(page4, IH) == te.STATE_SEEDING, 40)
got_first = wait_until(lambda: same_as_source(save4, FILE_ORDER[0]), 15)
got_second = same_as_source(save4, FILE_ORDER[1])
check("4 выбранный файл скачан", ok and got_first,
      f"{state_of(page4, IH)}, совпал: {got_first}")
check("4 невыбранный файл не скачан", not got_second, f"совпал: {got_second}")

# ---- 5: удаление раздачи вместе с файлами ----
removed_names = []
page4.confirm_remove = lambda name: (removed_names.append(name), (True, True))[1]
check("5 удаление подтверждено и выполнено", page4.remove(IH))
check("5 в диалог ушло имя раздачи",
      removed_names and removed_names[0] == "Тестовая раздача",
      str(removed_names))
pump(0.3)
check("5 карточка убрана, подсказка вернулась",
      not page4._cards and page4.empty_label.isVisible(),
      str(list(page4._cards)))
check("5 файлы удалены с диска",
      wait_until(lambda: not os.path.exists(
          os.path.join(save4, FILE_ORDER[0])), 10))

page5, eng5, save5 = make_page("s5")
page5.show()
pump(0.3)
page5.magnet_edit.setText(magnet())
page5.add_magnet()
wait_until(lambda: page5.engine.get(IH) is not None, 10)
page5.confirm_remove = lambda name: (False, False)
check("5 отказ в диалоге не убирает раздачу",
      page5.remove(IH) is False and page5.engine.get(IH) is not None)

# ---- 6: MainWindow — настройки и закрытие ----
settings = dict(config.load())
settings["history"] = []
settings["check_updates"] = False          # иначе QThread переживёт окно
settings["app_mode"] = "torrent"
settings["torrent_folder"] = os.path.join(BASE, "Загрузки с пробелом", "win")
window = gui.MainWindow(settings)
window.show()
pump(0.4)
check("6 движок создан один на окно",
      isinstance(window.torrent_engine, te.TorrentEngine))
check("6 страница режима получила тот же движок",
      window.torrent_page.engine is window.torrent_engine)
check("6 режим Torrent при старте поднял движок",
      window.torrent_engine._ses is not None)
check("6 папка раздач берётся из настроек",
      window.torrent_page.save_path() == settings["torrent_folder"],
      window.torrent_page.save_path())

sp = window.settings_page
check("6 в «Настройках» три карточки группы Torrent",
      hasattr(sp, "torrent_folder_edit") and hasattr(sp, "seed_check")
      and hasattr(sp, "torrent_port_spin"))
check("6 раздача после скачивания включена по умолчанию",
      sp.seed_check.isChecked() and window.torrent_engine.seed_after_download)
sp.seed_check.setChecked(False)
pump(0.2)
check("6 выключение раздачи дошло до живого движка",
      window.torrent_engine.seed_after_download is False
      and settings["torrent_seed_after_download"] is False)
sp.seed_check.setChecked(True)
pump(0.2)
check("6 включение раздачи дошло до живого движка",
      window.torrent_engine.seed_after_download is True)

sp.torrent_port_spin.setValue(6881)
pump(0.1)
check("6 порт сохраняется в настройки", settings["torrent_port"] == 6881,
      str(settings.get("torrent_port")))

window.close()
pump(0.4)
check("6 closeEvent остановил движок", window.torrent_engine._ses is None)

# ---- завершение: не оставить работающих потоков ----
for page, engine in PAGES:
    page.close()
    engine.shutdown(timeout=2.0)
pump(0.3)
alive = [t.name for t in __import__("threading").enumerate()
         if t.name == "torrent-engine" and t.is_alive()]
check("к выходу потоков движка не осталось", not alive, str(alive))

print()
print(f"ИТОГ: {len(PASS)} PASS, {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
