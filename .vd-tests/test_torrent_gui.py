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
Сессия 2.2 добавила сценарий 9: диалог «что смотреть» для раздачи с
несколькими видеофайлами и индикатор подготовки плеера.
"""
import collections
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
import torrent_stream as ts

PASS, FAIL = [], []


def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details, flush=True)


MB = 1024 * 1024
BASE = tempfile.mkdtemp(prefix="vd-gui-torrent-")
SRC_ROOT = os.path.join(BASE, "источник")
CONTENT = os.path.join(SRC_ROOT, "Тестовая раздача")
os.makedirs(CONTENT)
# Расширения настоящие: с 2.1 страница сама выбирает, что смотреть, —
# видеофайл определяется по расширению (.bin им не является)
SIZES = {"видео 1.mkv": 2 * MB + 12345, "видео 2.mp4": 1 * MB + 54321,
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
check("2 у готовой раздачи: «Смотреть», «Открыть папку», «Файлы», «Удалить»",
      buttons(card1) == ["Смотреть", "Открыть папку", "Файлы", "Удалить"],
      str(buttons(card1)))

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
      started and ok and buttons(card2)[:2] == ["Смотреть", "Продолжить"],
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
# Своя папка данных: иначе раздачи теста уезжают в рабочую torrent-data# проекта и всплывают при следующем запуске программы в dev-режиме
window.torrent_engine.data_dir = os.path.join(BASE, "данные", "win")
window.torrent_engine.resume_dir = os.path.join(BASE, "данные", "win",
                                                "resume")
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

# ---- 7: «Смотреть» — просмотр во время закачки (2.1) ----
# Настоящий плеер не запускаем: подменяем поиск и запуск, запоминая, с
# чем их позвали. Всё остальное — настоящее: сервис, HTTP-сервер, движок.
LAUNCHED = []
FAKE_PLAYER = r"C:\Плееры\mpv.exe"


def fake_resolve(configured=""):
    if configured == "нет плеера":
        raise gui_torrent.player.PlayerNotFound("на компьютере не найдены "
                                                "mpv или VLC")
    return configured or FAKE_PLAYER


gui_torrent.player.resolve = fake_resolve
gui_torrent.player.launch = (
    lambda target, exe, subtitles=():
    LAUNCHED.append((target, exe, tuple(subtitles))))

# В раздаче два видеофайла, и с 2.2 «Смотреть» спрашивает, какой из них
# открыть. Здесь отвечаем за пользователя тем же файлом, что выбирался
# сам в 2.1 (самый большой), — сам диалог и развилка «спрашивать или
# нет» проверяются сценарием 9.
ASKED = []


def pick_biggest(self, item, targets):
    ASKED.append([f.index for f in targets])
    return max(targets, key=lambda f: f.size)


gui_torrent.TorrentPage.ask_watch_file = pick_biggest

VIDEO_INDEX = next(i for i, p in enumerate(FILE_ORDER)
                   if os.path.basename(p) == "видео 1.mkv")
VIDEO_REL = FILE_ORDER[VIDEO_INDEX]

# 7а. Скачанный файл открывается напрямую — сервер не нужен
item1 = page1.engine.get(IH)
check("7 can_watch у докачанной раздачи с видео", page1.can_watch(item1))
check("7 «Смотреть» скачанного файла запускает плеер",
      page1.watch(IH) and len(LAUNCHED) == 1, str(LAUNCHED))
target, exe, _subs = LAUNCHED[-1]
check("7 у скачанного файла открывается САМ ФАЙЛ, а не http",
      target == os.path.join(save1, VIDEO_REL) and exe == FAKE_PLAYER, target)
check("7 сервер для скачанного файла не поднимался",
      page1._stream is None or not page1._stream.server.running)
check("7 просмотр по файлу не считается активным", not page1.is_watching(IH))

# 7б. Недокачанная раздача — поток через HTTP
seed.h.set_upload_limit(96 * 1024)
page7, eng7, save7 = make_page("s7")
page7.show()
pump(0.3)
page7.magnet_edit.setText(magnet())
page7.add_magnet()
started = wait_until(lambda: (page7.engine.get(IH) is not None)
                     and page7.engine.get(IH).has_metadata
                     and 0 <= page7.engine.get(IH).progress < 1.0, 30)
LAUNCHED.clear()
check("7 «Смотреть» недокачанной раздачи открывает http-ссылку",
      started and page7.watch(IH) and len(LAUNCHED) == 1
      and LAUNCHED[-1][0].startswith("http://127.0.0.1:"),
      str(LAUNCHED))
watch_url = LAUNCHED[-1][0]
check("7 просмотр активен и виден в карточке",
      page7.is_watching(IH)
      and page7._stream.active == (IH, VIDEO_INDEX), str(page7._stream.active))
card7 = page7._cards.get(IH)
# Плеер поддельный, к серверу он не обратится, поэтому подпись здесь —
# «запускаем плеер…», а не «идёт просмотр»: с 2.2 карточка показывает
# подготовку, пока плеер не дал о себе знать (сценарий 9)
check("7 в карточке «Остановить просмотр» и пометка в подписи",
      buttons(card7)[0] == "Остановить просмотр"
      and page7.watch_status(IH) == "запускаем плеер…"
      and "запускаем плеер…" in card7.meta_label.text(),
      f"{buttons(card7)} | {card7.meta_label.text()}")

# 7в. Ссылка действительно отдаёт байты файла — весь путь целиком
import urllib.request

with open(os.path.join(SRC_ROOT, VIDEO_REL), "rb") as f:
    SRC_HEAD = f.read(65536)
got = {}


def fetch_head():
    request = urllib.request.Request(watch_url,
                                     headers={"Range": "bytes=0-65535"})
    with urllib.request.urlopen(request, timeout=60) as resp:
        got["status"] = resp.status
        got["body"] = resp.read()


fetcher = __import__("threading").Thread(target=fetch_head, daemon=True)
fetcher.start()
ok = wait_until(lambda: "body" in got, 60)       # ждём, пока куски придут
check("7 по ссылке приходят те самые байты файла",
      ok and got.get("status") == 206 and got.get("body") == SRC_HEAD,
      f"{got.get('status')}, {len(got.get('body') or b'')} байт")

# 7г. Остановка просмотра
# Кнопок ровно столько, сколько в раскладке: прежние не должны
# оставаться детьми виджета и рисоваться поверх новых (снимок живой
# проверки 2.1 показал «Смотреть» поверх «Остановить просмотр»)
from PySide6.QtWidgets import QPushButton as _QPushButton

stale = [w for w in card7.actions_widget.findChildren(_QPushButton)
         if w.parent() is card7.actions_widget]
check("7 старые кнопки карточки не остаются поверх новых",
      len(stale) == len(buttons(card7)),
      f"детей {len(stale)}, в раскладке {len(buttons(card7))}")

check("7 «Остановить просмотр» снимает поток",
      page7.stop_watch() and not page7.is_watching(IH)
      and page7._stream.active is None)
pump(0.2)
check("7 кнопка вернулась в «Смотреть»", "Смотреть" in buttons(card7),
      str(buttons(card7)))

# 7д. Плеера нет: поток не рвём, ссылку кладём в буфер обмена
page7.settings["torrent_player"] = "нет плеера"
LAUNCHED.clear()
check("7 без плеера «Смотреть» не падает и плеер не запускается",
      page7.watch(IH) is False and not LAUNCHED)
check("7 без плеера просмотр продолжается, ссылка в буфере обмена",
      page7.is_watching(IH)
      and gui_torrent.QApplication.clipboard().text().startswith(
          "http://127.0.0.1:"),
      gui_torrent.QApplication.clipboard().text()[:40])
page7.settings["torrent_player"] = ""
page7.stop_watch()

# 7е. Смотреть нечего: в раздаче нет видеофайлов
no_video = dataclasses.replace(
    page7.engine.get(IH),
    files=tuple(f for f in page7.engine.get(IH).files
                if f.path.endswith(".txt")))
check("7 без видеофайлов кнопки «Смотреть» нет",
      not page7.can_watch(no_video))
metadata_only = dataclasses.replace(page7.engine.get(IH),
                                    state=te.STATE_METADATA,
                                    has_metadata=False, files=())
check("7 до метаданных кнопки «Смотреть» нет",
      not page7.can_watch(metadata_only))

# 7ж. Удаление раздачи во время просмотра снимает просмотр
page7.watch(IH)
check("7 просмотр перед удалением активен", page7.is_watching(IH))
page7.confirm_remove = lambda name: (True, True)
page7.remove(IH)
pump(0.3)
check("7 удаление раздачи остановило просмотр", not page7.is_watching(IH))
seed.h.set_upload_limit(0)

# 7з. MainWindow: закрытие окна во время просмотра
settings8 = dict(config.load())
settings8["history"] = []
settings8["check_updates"] = False
settings8["app_mode"] = "torrent"
settings8["torrent_folder"] = os.path.join(BASE, "Загрузки с пробелом", "win2")
settings8["torrent_player"] = ""
window8 = gui.MainWindow(settings8)
window8.torrent_engine.data_dir = os.path.join(BASE, "данные", "win2")
window8.torrent_engine.resume_dir = os.path.join(BASE, "данные", "win2",
                                                 "resume")
window8.show()
pump(0.4)
check("7 в «Настройках» появилась карточка плеера",
      hasattr(window8.settings_page, "torrent_player_edit"))
window8.settings_page.torrent_player_edit.setText(FAKE_PLAYER)
pump(0.1)
check("7 путь к плееру сохраняется в настройки",
      settings8["torrent_player"] == FAKE_PLAYER,
      settings8.get("torrent_player"))

seed.h.set_upload_limit(96 * 1024)
page8 = window8.torrent_page
page8.magnet_edit.setText(magnet())
page8.add_magnet()
started = wait_until(lambda: (page8.engine.get(IH) is not None)
                     and page8.engine.get(IH).has_metadata, 30)
LAUNCHED.clear()
check("7 просмотр в настоящем окне запущен",
      started and page8.watch(IH) and page8.is_watching(IH), str(LAUNCHED))
# Запрос, который ждёт недостающие куски: именно он мог бы задержать выход
holder8 = {}


def long_read():
    try:
        request = urllib.request.Request(
            LAUNCHED[-1][0], headers={"Range": "bytes=0-2097151"})
        with urllib.request.urlopen(request, timeout=60) as resp:
            holder8["body"] = resp.read()
    except Exception as exc:
        holder8["error"] = type(exc).__name__


reader8 = __import__("threading").Thread(target=long_read, daemon=True)
reader8.start()
pump(1.0)
t_close = time.monotonic()
window8.close()
close_s = time.monotonic() - t_close
pump(0.3)
check("7 закрытие окна во время просмотра укладывается в бюджет",
      close_s < 4.5, f"{close_s:.2f} с")
check("7 closeEvent закрыл и сервер просмотра, и движок",
      not window8.torrent_stream.server.running
      and window8.torrent_engine._ses is None,
      f"сервер={window8.torrent_stream.server.running}")
reader8.join(3)
check("7 висевший запрос завершился, поток не остался",
      not reader8.is_alive(), str(holder8.keys()))
seed.h.set_upload_limit(0)

# ---- 9: диалог «что смотреть» и индикатор подготовки плеера (2.2) ----
page9, eng9, save9 = make_page("s9")
page9.show()
pump(0.3)
seed.h.set_upload_limit(96 * 1024)
page9.magnet_edit.setText(magnet())
page9.add_magnet()
started9 = wait_until(lambda: (page9.engine.get(IH) is not None)
                      and page9.engine.get(IH).has_metadata
                      and 0 <= page9.engine.get(IH).progress < 1.0, 30)
item9 = page9.engine.get(IH)
check("9 раздача добавлена и ещё качается", started9,
      "" if item9 is None else str(item9.state))

# 9а. Сам диалог: дерево то же, выбор одиночный
targets9 = ts.watchable_files(item9.files)
check("9 к просмотру предложены оба видеофайла, txt — нет",
      [os.path.basename(f.path) for f in targets9]
      == ["видео 1.mkv", "видео 2.mp4"],
      str([f.path for f in targets9]))
dlg9 = gui_torrent.WatchFileDialog(item9, targets9,
                                   page9.engine.file_progress(IH), page9)
root9 = dlg9.tree.invisibleRootItem()
folder9 = root9.child(0)
check("9 диалог показывает всю раздачу, включая невидеофайлы",
      root9.childCount() == 1 and folder9.childCount() == 3,
      f"{root9.childCount()} / {folder9.childCount()}")
disabled9 = [folder9.child(i).text(0) for i in range(folder9.childCount())
             if folder9.child(i).isDisabled()]
check("9 невидеофайл выбрать нельзя", disabled9 == ["описание.txt"],
      str(disabled9))
chosen9 = dlg9._current_file()
check("9 предвыбран самый большой видеофайл",
      chosen9 is not None and chosen9.index == VIDEO_INDEX,
      "" if chosen9 is None else chosen9.path)
check("9 «Смотреть» доступна при выбранном видео", dlg9.yesButton.isEnabled())
other9 = next(f for f in targets9 if f.index != VIDEO_INDEX)
check("9 выбор другого файла меняет ответ диалога",
      dlg9.select(other9.index)
      and dlg9._current_file().index == other9.index)
# У каждого видео видно, сколько уже скачано, — по этому выбирают, что
# пойдёт быстрее
shares9 = [folder9.child(i).text(2) for i in range(folder9.childCount())]
check("9 у видеофайлов показан процент скачанного",
      all(s.endswith("%") or s == "скачан" for s in shares9[:2])
      and shares9[2] == "", str(shares9))
check("9 без подтверждения диалог ничего не отдаёт", dlg9.chosen() is None)
dlg9.close()
dlg9.deleteLater()

# 9б. Развилка: одно видео — без вопроса, два — с вопросом
ASKED.clear()
LAUNCHED.clear()
one_video = dataclasses.replace(
    item9, files=tuple(f for f in item9.files
                       if not f.path.endswith(".mp4")))
page9.engine.get = lambda tid: one_video if tid == IH else None
check("9 при одном видеофайле диалог не показывается",
      page9.watch(IH) and not ASKED and len(LAUNCHED) == 1, str(ASKED))
page9.stop_watch()
del page9.engine.get                    # обратно к методу движка

ASKED.clear()
LAUNCHED.clear()
check("9 при двух видеофайлах диалог показывается",
      page9.watch(IH) and ASKED == [[f.index for f in targets9]], str(ASKED))
check("9 смотрится тот файл, который выбрали в диалоге",
      page9._stream.active == (IH, VIDEO_INDEX), str(page9._stream.active))
page9.stop_watch()

ASKED.clear()
LAUNCHED.clear()
gui_torrent.TorrentPage.ask_watch_file = lambda self, item, targets: None
check("9 отмена диалога не запускает ни поток, ни плеер",
      page9.watch(IH) is False and not LAUNCHED
      and not page9.is_watching(IH))
gui_torrent.TorrentPage.ask_watch_file = pick_biggest

# 9в. Индикатор подготовки плеера
LAUNCHED.clear()
check("9 просмотр запущен", page9.watch(IH) and page9.is_watching(IH))
card9 = page9._cards.get(IH)
check("9 сразу после запуска — «запускаем плеер…»",
      page9.watch_status(IH) == "запускаем плеер…"
      and "запускаем плеер…" in card9.meta_label.text(),
      card9.meta_label.text())
# Плеер тут поддельный, к серверу он не обратится: сдвигаем начало отсчёта
# вместо того, чтобы ждать вживую
HINTS9 = []
page9._notify = lambda kind, text: HINTS9.append((kind, text))
page9._watch_started -= gui_torrent.PREPARE_HINT_S
page9._poll_player()
check("9 после PREPARE_HINT_S карточка показывает «готовим плеер…»",
      page9.watch_status(IH).startswith("готовим плеер…")
      and page9.watch_status(IH) in card9.meta_label.text(),
      f"{page9.watch_status(IH)} | {card9.meta_label.text()}")
check("9 причина задержки объяснена отдельным сообщением, один раз",
      len(HINTS9) == 1 and HINTS9[0][0] == "info"
      and "Windows" in HINTS9[0][1], str(HINTS9))
page9._poll_player()
check("9 подсказка не повторяется на каждом тике", len(HINTS9) == 1,
      str(len(HINTS9)))
del page9._notify
check("9 пока плеер молчит, кнопка остаётся «Остановить просмотр»",
      buttons(card9)[0] == "Остановить просмотр", str(buttons(card9)))

# Настоящий запрос к потоку — то самое событие, которого ждёт индикатор
req9 = urllib.request.Request(LAUNCHED[-1][0], headers={"Range": "bytes=0-65535"})
got9 = {}


def read9():
    try:
        with urllib.request.urlopen(req9, timeout=60) as resp:
            got9["body"] = resp.read()
    except Exception as exc:
        got9["error"] = type(exc).__name__


reader9 = __import__("threading").Thread(target=read9, daemon=True)
reader9.start()
ok9 = wait_until(lambda: page9.watch_status(IH) == "идёт просмотр", 60)
check("9 первое обращение плеера переводит в «идёт просмотр»", ok9,
      page9.watch_status(IH))
check("9 таймер подготовки после этого остановлен",
      not page9._prepare_timer.isActive())
reader9.join(5)

# Молчание дольше PREPARE_TIMEOUT_S — говорим честно, поток не рвём
page9.stop_watch()
LAUNCHED.clear()
NOTES9 = []
page9._notify = lambda kind, text: NOTES9.append((kind, text))
page9.watch(IH)
page9._watch_started -= gui_torrent.PREPARE_TIMEOUT_S
page9._poll_player()
check("9 после PREPARE_TIMEOUT_S карточка сообщает, что плеер не отозвался",
      page9.watch_status(IH) == "плеер не отозвался"
      and NOTES9 and NOTES9[-1][0] == "warning", str(NOTES9[-1:]))
check("9 просмотр при этом НЕ снят, ссылка в буфере обмена",
      page9.is_watching(IH)
      and gui_torrent.QApplication.clipboard().text().startswith(
          "http://127.0.0.1:"),
      gui_torrent.QApplication.clipboard().text()[:40])
check("9 остановка просмотра гасит индикатор",
      page9.stop_watch() and page9.watch_status(IH) == ""
      and not page9._prepare_timer.isActive())
seed.h.set_upload_limit(0)

# ---- 10. Сериал: вложенные папки в диалоге «Что смотреть» (2.3) ----
# В 2.2 диалог проверялся на плоской раздаче из двух файлов. Сериал с
# сезонами по папкам строит дерево другой глубины, и предвыбор «самый
# большой файл» там означает произвольную серию — важно, что выбрать
# можно ЛЮБУЮ, включая лежащую глубоко.
import types as _types

_SeriesFile = collections.namedtuple("File", "index path size priority")
_series = []
for _season in (1, 2):
    for _ep in range(1, 6):
        _series.append(os.path.join("Сериал", f"Сезон {_season}",
                                    f"S0{_season}E0{_ep}.mkv"))
        _series.append(os.path.join("Сериал", f"Сезон {_season}",
                                    f"S0{_season}E0{_ep}.srt"))
_series.append(os.path.join("Сериал", "обложка.jpg"))
_files10 = []
for _i, _path in enumerate(_series):
    _video = _path.endswith(".mkv")
    # Самая большая серия лежит В СЕРЕДИНЕ списка нарочно: так проверка
    # предвыбора отличает «взяли самый большой файл» от «взяли последний»
    _size = (300 + _i) * 1024 * 1024 if _video else 40 * 1024
    if _path.endswith("S01E03.mkv"):
        _size = 900 * 1024 * 1024
    _files10.append(_SeriesFile(_i, _path, _size, 4))
item10 = _types.SimpleNamespace(id="ee" * 20, files=tuple(_files10))
targets10 = gui_torrent.ts.watchable_files(item10.files)
# Родитель обязателен: MessageBoxBase qfluentwidgets берёт у него
# размеры прямо в конструкторе
dlg10 = gui_torrent.WatchFileDialog(item10, targets10, [], page9)

root10 = dlg10.tree.invisibleRootItem()
serial = root10.child(0)
seasons = [serial.child(i) for i in range(serial.childCount())]
names10 = [s.text(0) for s in seasons]
check("10 сериал: корневая папка одна, внутри два сезона и обложка",
      root10.childCount() == 1 and names10 == ["Сезон 1", "Сезон 2",
                                               "обложка.jpg"],
      f"{root10.childCount()} / {names10}")
check("10 в сезоне видно и серии, и субтитры",
      seasons[0].childCount() == 10,
      str(seasons[0].childCount()))
check("10 предвыбрана самая большая серия",
      dlg10._current_file() is not None
      and os.path.basename(dlg10._current_file().path) == "S01E03.mkv",
      str(dlg10._current_file() and dlg10._current_file().path))
_first = next(f for f in targets10
              if os.path.basename(f.path) == "S01E01.mkv")
check("10 выбирается серия из вложенной папки, а не только предвыбор",
      dlg10.select(_first.index)
      and dlg10._current_file().index == _first.index,
      str(dlg10._current_file().path))
_sub = next(f for f in item10.files if f.path.endswith("S01E01.srt"))
check("10 субтитры и обложку выбрать нельзя",
      _sub.index not in dlg10._nodes
      and not dlg10.select(_sub.index), "")
dlg10.deleteLater()

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
