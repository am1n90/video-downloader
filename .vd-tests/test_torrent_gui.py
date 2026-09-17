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
Сессия 2.2 добавила сценарий 9: окно выбора для раздачи с несколькими
видеофайлами и индикатор подготовки плеера. Сессия 2.6 — сценарии 12
(новый поток добавления: ожидание списка файлов, «Отмена» / «Скачать» /
«Посмотреть») и 13 (само окно: галочки и выделение — разные ответы).
Долг 1.4 — сценарий 14: Библиотека торрентов (запись после «Скачать» /
«Посмотреть», удаление с галочкой и без, «Очистить», раздача после
focus_file).
Перед релизом 1.1.0 — сценарий 15: повторное добавление раздачи, которая
уже в списке, не открывает окно выбора (его «Отмена» удаляла файлы).

Окно выбора модальное, поэтому в тесте подменяется ЕДИНСТВЕННАЯ точка
его показа — TorrentPage.ask_choice (см. ANSWER ниже).
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
BRIDGES = {}


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
    BRIDGES[page] = bridge
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


def wait_ready(page, timeout=30):
    """Дождаться, пока окно выбора ОТВЕТИЛО и раздача пошла качаться.

    С 2.6 одних метаданных мало: пока ответа нет, раздача ждёт (2.6) и
    «Смотреть» открыл бы окно выбора вместо плеера. Ловилось как флейк
    сценария 7б раз в несколько прогонов.
    """
    return wait_until(lambda: (page.engine.get(IH) is not None)
                      and page.engine.get(IH).has_metadata
                      and not page.is_pending(IH), timeout)


# С 2.6 раздача после «Добавить» ничего не качает, пока пользователь не
# ответит в окне выбора, — а окно это модальное и в offscreen-тесте
# висело бы вечно. Подменяем единственную точку показа (ask_choice) на
# ответы по контексту: при добавлении — «Скачать» со всеми галочками
# (прежнее поведение, на нём стоят сценарии 1-11), у кнопки «Файлы» —
# «ничего не делать». Сценарии меняют ANSWER под себя.
ASKED = []


def download_all(item):
    return gui_torrent.FilesChoice(
        gui_torrent.ACTION_DOWNLOAD,
        tuple(gui_torrent.PRIORITY_ON for _ in item.files),
        None, item.save_path)


def answer_nothing(item):
    return gui_torrent.FilesChoice(gui_torrent.ACTION_CLOSE)


def watch_file(index):
    """Ответ «Посмотреть» на файле с этим индексом."""
    def answer(item):
        video = next(f for f in ts.watchable_files(item.files)
                     if f.index == index)
        return gui_torrent.FilesChoice(
            gui_torrent.ACTION_WATCH,
            tuple(f.priority for f in item.files), video, item.save_path)
    return answer


ANSWER = {gui_torrent.MODE_ADD: download_all,
          gui_torrent.MODE_MANAGE: answer_nothing}


def fake_choice(self, item, mode):
    ASKED.append((mode, [f.index for f in ts.watchable_files(item.files)]))
    return ANSWER[mode](item)


gui_torrent.TorrentPage.ask_choice = fake_choice


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
dialog = gui_torrent.TorrentFilesDialog(item1, gui_torrent.MODE_MANAGE, [],
                                        page1)
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
check("4 метаданные получены и окно выбора ответило", wait_ready(page4))
# Вместо окна — ответ «Скачать» с одной снятой галочкой
ANSWER[gui_torrent.MODE_MANAGE] = lambda item: gui_torrent.FilesChoice(
    gui_torrent.ACTION_DOWNLOAD, (4, 0, 0), None, item.save_path)
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
reopened = gui_torrent.TorrentFilesDialog(item4, gui_torrent.MODE_MANAGE, [],
                                          page4)
check("4 повторно открытый диалог показывает прежний выбор",
      reopened.priorities() == [4, 0, 0], str(reopened.priorities()))
reopened.close()
reopened.deleteLater()
ANSWER[gui_torrent.MODE_MANAGE] = answer_nothing
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

# В раздаче два видеофайла, и «Смотреть» при нескольких видео открывает
# окно выбора. Здесь отвечаем за пользователя тем же файлом, что
# выбирался сам в 2.1 (самый большой), — само окно и развилка
# «спрашивать или нет» проверяются сценарием 9.
def pick_biggest(item):
    video = max(ts.watchable_files(item.files), key=lambda f: f.size)
    return gui_torrent.FilesChoice(
        gui_torrent.ACTION_WATCH,
        tuple(f.priority for f in item.files), video, item.save_path)


ANSWER[gui_torrent.MODE_MANAGE] = pick_biggest

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
started = wait_ready(page7) and page7.engine.get(IH).progress < 1.0
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
started = wait_ready(page8)
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
started9 = wait_ready(page9) and page9.engine.get(IH).progress < 1.0
item9 = page9.engine.get(IH)
check("9 раздача добавлена и ещё качается", started9,
      "" if item9 is None else str(item9.state))

# 9а. Сам диалог: дерево то же, выбор одиночный
targets9 = ts.watchable_files(item9.files)
check("9 к просмотру предложены оба видеофайла, txt — нет",
      [os.path.basename(f.path) for f in targets9]
      == ["видео 1.mkv", "видео 2.mp4"],
      str([f.path for f in targets9]))
dlg9 = gui_torrent.TorrentFilesDialog(item9, gui_torrent.MODE_MANAGE,
                                      page9.engine.file_progress(IH), page9)
root9 = dlg9.tree.invisibleRootItem()
folder9 = root9.child(0)
check("9 окно показывает всю раздачу, включая невидеофайлы",
      root9.childCount() == 1 and folder9.childCount() == 3,
      f"{root9.childCount()} / {folder9.childCount()}")
check("9 при нескольких видео «Посмотреть» ждёт выделения строки",
      dlg9.watch_target() is None and not dlg9.watch_btn.isEnabled())
check("9 выделение видео включает «Посмотреть»",
      dlg9.select(VIDEO_INDEX) and dlg9.watch_btn.isEnabled()
      and dlg9.watch_target().index == VIDEO_INDEX)
other9 = next(f for f in targets9 if f.index != VIDEO_INDEX)
check("9 выделение другого файла меняет цель просмотра",
      dlg9.select(other9.index)
      and dlg9.watch_target().index == other9.index)
txt9 = next(f for f in item9.files if f.path.endswith(".txt"))
check("9 на невидеофайле «Посмотреть» недоступна",
      dlg9.select(txt9.index) and dlg9.watch_target() is None
      and not dlg9.watch_btn.isEnabled())
check("9 галочка невидеофайла при этом работает",
      dlg9.set_checked(txt9.index, False)
      and dlg9.priorities()[txt9.index] == 0, str(dlg9.priorities()))
dlg9.set_checked(txt9.index, True)
# У каждого видео видно, сколько уже скачано, — по этому выбирают, что
# пойдёт быстрее
shares9 = [folder9.child(i).text(2) for i in range(folder9.childCount())]
check("9 у видеофайлов показан процент скачанного",
      all(s.endswith("%") or s == "скачан" for s in shares9[:2]),
      str(shares9))
check("9 без нажатия кнопок окно ничего не решает",
      dlg9.choice().action == gui_torrent.ACTION_CLOSE)
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
check("9 при двух видеофайлах окно показывается",
      page9.watch(IH)
      and ASKED == [(gui_torrent.MODE_MANAGE,
                     [f.index for f in targets9])], str(ASKED))
check("9 смотрится тот файл, который выбрали в окне",
      page9._stream.active == (IH, VIDEO_INDEX), str(page9._stream.active))
page9.stop_watch()

ASKED.clear()
LAUNCHED.clear()
ANSWER[gui_torrent.MODE_MANAGE] = answer_nothing
check("9 закрытое окно не запускает ни поток, ни плеер",
      page9.watch(IH) is False and not LAUNCHED
      and not page9.is_watching(IH))
ANSWER[gui_torrent.MODE_MANAGE] = pick_biggest

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

# ---- 10. Сериал: вложенные папки в окне выбора (2.3) ----
# В 2.2 окно проверялось на плоской раздаче из двух файлов. Сериал с
# сезонами по папкам строит дерево другой глубины — важно, что выделить
# можно ЛЮБУЮ серию, включая лежащую глубоко.
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
item10 = _types.SimpleNamespace(id="ee" * 20, name="Сериал",
                                save_path=BASE, files=tuple(_files10))
targets10 = gui_torrent.ts.watchable_files(item10.files)
# Родитель обязателен: MessageBoxBase qfluentwidgets берёт у него
# размеры прямо в конструкторе
dlg10 = gui_torrent.TorrentFilesDialog(item10, gui_torrent.MODE_MANAGE, [],
                                       page9)

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
check("10 у сериала ничего не предвыбрано — серию выделяет пользователь",
      dlg10.watch_target() is None and not dlg10.watch_btn.isEnabled())
_first = next(f for f in targets10
              if os.path.basename(f.path) == "S01E01.mkv")
check("10 выделяется серия из вложенной папки",
      dlg10.select(_first.index)
      and dlg10.watch_target().index == _first.index,
      str(dlg10.watch_target().path))
_deep = next(f for f in targets10
             if os.path.basename(f.path) == "S02E05.mkv")
check("10 выделяется и серия из второго сезона",
      dlg10.select(_deep.index)
      and dlg10.watch_target().index == _deep.index,
      str(dlg10.watch_target().path))
_sub = next(f for f in item10.files if f.path.endswith("S01E01.srt"))
check("10 на субтитрах «Посмотреть» гаснет, а галочка работает",
      dlg10.select(_sub.index) and dlg10.watch_target() is None
      and dlg10.set_checked(_sub.index, False)
      and dlg10.priorities()[_sub.index] == 0)
dlg10.deleteLater()

# ---- 11. «Смотреть» переключает закачку на выбранную серию (2.5) ----
# До 2.5 у сериала качались ВСЕ серии сразу: выбор в диалоге поднимал
# только куски окна просмотра. Теперь выбор — это ещё и «качай её».
seed.h.set_upload_limit(96 * 1024)
# Раздачи предыдущих страниц всё ещё качаются и делят с нами эти 96 КБ/с
# втроём: на последнем сценарии метаданных иногда не было и за 30 с.
# Проверки тех страниц уже сделаны — ставим их на паузу.
for _page, _eng in list(PAGES):
    try:
        _eng.pause(IH)
    except Exception:
        pass
page11, eng11, save11 = make_page("s11")
page11.show()
pump(0.3)
page11.magnet_edit.setText(magnet())
page11.add_magnet()
started11 = wait_ready(page11) and page11.engine.get(IH).progress < 1.0
check("11 раздача добавлена и ещё качается", started11)


def prios11():
    return [f.priority for f in page11.engine.get(IH).files]


check("11 до «Смотреть» качаются все файлы раздачи",
      prios11() == [4] * len(FILE_ORDER), str(prios11()))

# Выбираем НЕ предвыбранный (не самый большой) файл — как серию 3 у сериала
OTHER_INDEX = next(i for i, p in enumerate(FILE_ORDER)
                   if os.path.basename(p) == "видео 2.mp4")
ANSWER[gui_torrent.MODE_MANAGE] = watch_file(OTHER_INDEX)
LAUNCHED.clear()
check("11 «Смотреть» на второй серии запустил просмотр именно её",
      page11.watch(IH) and page11._stream.active == (IH, OTHER_INDEX),
      str(page11._stream.active))
applied11 = wait_until(lambda: prios11()[VIDEO_INDEX] == 0, 10)
check("11 остальные файлы раздачи сняты с закачки",
      applied11 and prios11()[OTHER_INDEX] == te.STREAM_PRIORITY
      and all(p == 0 for i, p in enumerate(prios11()) if i != OTHER_INDEX),
      str(prios11()))

# Главное последствие: в диалоге должны остаться ВСЕ серии, иначе
# следующую уже не выбрать — «Смотреть» молча открывал бы ту же самую
targets11 = ts.watchable_files(page11.engine.get(IH).files)
check("11 в окне выбора по-прежнему все серии",
      [f.index for f in targets11] == [VIDEO_INDEX, OTHER_INDEX],
      str([os.path.basename(f.path) for f in targets11]))

# Досмотрели одну, включаем другую — закачка переезжает на неё
ANSWER[gui_torrent.MODE_MANAGE] = watch_file(VIDEO_INDEX)
page11.stop_watch()
LAUNCHED.clear()
check("11 «Смотреть» на первой серии переключил просмотр",
      page11.watch(IH) and page11._stream.active == (IH, VIDEO_INDEX),
      str(page11._stream.active))
applied11b = wait_until(lambda: prios11()[OTHER_INDEX] == 0, 10)
check("11 теперь качается она, а прежняя снята",
      applied11b and prios11()[VIDEO_INDEX] == te.STREAM_PRIORITY,
      str(prios11()))
page11.stop_watch()

# Вернуть всё сразу можно окном «Файлы» — там галочки и стоят
ANSWER[gui_torrent.MODE_MANAGE] = download_all
page11.choose_files(IH)
check("11 окно «Файлы» возвращает закачку всей раздачи",
      wait_until(lambda: prios11() == [4] * len(FILE_ORDER), 10), str(prios11()))

# ---- 12. Новый поток добавления: выбор ДО закачки (2.6) ----
# «Добавить» больше не начинает качать: раздача ждёт, окно выбора
# открывается само, как только пришёл список файлов, и только ответ в
# нём («Отмена» / «Скачать» / «Посмотреть») что-то запускает.
ANSWER[gui_torrent.MODE_MANAGE] = answer_nothing
seed.h.set_upload_limit(96 * 1024)
for _page, _eng in list(PAGES):
    try:
        _eng.pause(IH)                  # их проверки уже сделаны
    except Exception:
        pass


def cancel_answer(item):
    return gui_torrent.FilesChoice(gui_torrent.ACTION_CANCEL)


# 12а. Ожидание списка файлов и «Отмена»
ANSWER[gui_torrent.MODE_ADD] = answer_nothing    # пока окно «закрываем»
ASKED.clear()
page12, eng12, save12 = make_page("s12")
page12.show()
pump(0.3)
page12.magnet_edit.setText(magnet())
page12.add_magnet()
pump(0.2)
card12 = page12._cards.get(IH)
check("12 «Добавить» ничего не качает — раздача ждёт ответа",
      card12 is not None and page12.is_pending(IH)
      and int(eng12._handles[IH].status().total_done) == 0,
      str(state_of(page12, IH)))
# Как выглядит карточка, пока список файлов ещё не пришёл. Отдельным
# снимком, а не по ходу: метаданные с локального сида приходят за доли
# секунды, и поймать это состояние в потоке — гонка
card12.update_state(dataclasses.replace(
    page12.engine.get(IH), state=te.STATE_METADATA, has_metadata=False,
    files=()))
check("12 пока список файлов не пришёл — «Получаем список файлов…»",
      card12.meta_label.text() == "Получаем список файлов…"
      and buttons(card12) == ["Удалить"],
      f"{card12.meta_label.text()} | {buttons(card12)}")
asked12 = wait_until(lambda: bool(ASKED), 30)
pump(0.5)
check("12 окно выбора открылось само, как только пришёл список",
      asked12 and ASKED[0][0] == gui_torrent.MODE_ADD, str(ASKED))
check("12 окно показано один раз, а не на каждый тик движка",
      len(ASKED) == 1, str(ASKED))
page12.refresh()
pump(0.2)
check("12 закрытое окно ничего не запускает — раздача ждёт",
      state_of(page12, IH) == te.STATE_PENDING
      and page12.is_pending(IH), str(state_of(page12, IH)))
card12 = page12._cards.get(IH)
check("12 на карточке ждущей раздачи только «Выбрать файлы» и «Удалить»",
      buttons(card12) == ["Выбрать файлы", "Удалить"], str(buttons(card12)))
check("12 в подписи — «Ожидает выбора файлов», без процентов",
      card12.meta_label.text() == "Ожидает выбора файлов",
      card12.meta_label.text())
held = int(eng12._handles[IH].status().total_done)
time.sleep(2)
pump(0.3)
check("12 пока ждём ответа, ничего не качается",
      int(eng12._handles[IH].status().total_done) == held,
      f"{held} байт успело прийти в момент метаданных")

ASKED.clear()
ANSWER[gui_torrent.MODE_ADD] = cancel_answer
page12.choose_pending(IH)               # кнопка «Выбрать файлы»
pump(0.3)
gone12 = wait_until(lambda: page12.engine.get(IH) is None, 10)
check("12 «Выбрать файлы» открывает то же окно, «Отмена» убирает раздачу",
      gone12 and not page12._cards
      and ASKED and ASKED[0][0] == gui_torrent.MODE_ADD, str(ASKED))
check("12 «Отмена» убирает и то, что успело скачаться",
      wait_until(lambda: not os.path.isdir(
          os.path.join(save12, "Тестовая раздача")), 10),
      str(os.listdir(save12) if os.path.isdir(save12) else []))

# 12б. «Скачать»: качается всё отмеченное, папку можно сменить
other_dir = os.path.join(BASE, "Другая папка")
WANTED13 = [gui_torrent.PRIORITY_ON] * len(FILE_ORDER)
WANTED13[OTHER_INDEX] = 0               # одна галочка снята
ASKED.clear()
ANSWER[gui_torrent.MODE_ADD] = lambda item: gui_torrent.FilesChoice(
    gui_torrent.ACTION_DOWNLOAD, tuple(WANTED13), None, other_dir)
page13, eng13, save13 = make_page("s13")
page13.show()
pump(0.3)
page13.magnet_edit.setText(magnet())
page13.add_magnet()
started13 = wait_until(lambda: state_of(page13, IH) == te.STATE_DOWNLOADING, 30)
check("12 «Скачать» запускает закачку сразу после окна", started13,
      str(state_of(page13, IH)))
check("12 «Скачать» качает всё отмеченное галочками",
      wait_until(lambda: [f.priority for f in page13.engine.get(IH).files]
                 == WANTED13, 10),
      str([f.priority for f in page13.engine.get(IH).files]))
check("12 папка из окна применилась к раздаче",
      wait_until(lambda: page13.engine.get(IH).save_path == other_dir, 10),
      page13.engine.get(IH).save_path)
check("12 раздача больше не ждёт выбора", not page13.is_pending(IH))
card13 = page13._cards.get(IH)
check("12 на карточке снова обычные кнопки",
      "Пауза" in buttons(card13) and "Файлы" in buttons(card13),
      str(buttons(card13)))

# 12в. «Посмотреть»: качается строго выделенная серия
try:
    eng13.pause(IH)                     # её проверки сделаны, канал общий
except Exception:
    pass
seed.h.set_upload_limit(256 * 1024)
ASKED.clear()
LAUNCHED.clear()
ANSWER[gui_torrent.MODE_ADD] = watch_file(OTHER_INDEX)
page14, eng14, save14 = make_page("s14")
page14.show()
pump(0.3)
page14.magnet_edit.setText(magnet())
page14.add_magnet()
watching14 = wait_until(lambda: page14.is_watching(IH), 30)
pump(0.3)
check("12 «Посмотреть» из окна запускает просмотр выбранной серии",
      watching14 and page14._stream.active == (IH, OTHER_INDEX)
      and len(LAUNCHED) == 1, str(LAUNCHED))


def prios14():
    return [f.priority for f in page14.engine.get(IH).files]


check("12 качается ТОЛЬКО выбранная серия, галочки не в счёт",
      wait_until(lambda: prios14()[VIDEO_INDEX] == 0, 10)
      and prios14()[OTHER_INDEX] == te.STREAM_PRIORITY
      and all(p == 0 for i, p in enumerate(prios14()) if i != OTHER_INDEX),
      str(prios14()))
check("12 явный заказ файлов при просмотре не записан",
      IH not in page14.engine._chosen
      and not os.path.exists(page14.engine._chosen_path(IH)))
before14 = page14.engine.file_progress(IH)
wait_until(lambda: page14.engine.file_progress(IH)[OTHER_INDEX]
           > before14[OTHER_INDEX], 60)
after14 = page14.engine.file_progress(IH)
# У соседа прибавка возможна ровно одна — его хвост, лежащий в КУСКЕ,
# который делится с началом выбранной серии (размеры файлов не кратны
# куску). Тот же эффект границы разобран по байтам в сессии 2.5
tail14 = SIZES["видео 1.mkv"] % (256 * 1024)
grew14 = after14[VIDEO_INDEX] - before14[VIDEO_INDEX]
check("12 растёт только выбранная серия (у соседа — общий кусок)",
      after14[OTHER_INDEX] > before14[OTHER_INDEX] and grew14 in (0, tail14),
      f"{before14} -> {after14}, общий хвост {tail14} Б")
page14.stop_watch()
ANSWER[gui_torrent.MODE_ADD] = download_all

# ---- 13. Само окно выбора: галочки и выделение — разные ответы (2.6) ----
# Галочки отвечают «что скачать», выделение строки — «что смотреть».
# Клик по квадратику галочки НЕ должен выделять строку: иначе простая
# расстановка галочек оживляла бы «Посмотреть» на случайной серии.
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

item13 = page13.engine.get(IH)
dlg13 = gui_torrent.TorrentFilesDialog(item13, gui_torrent.MODE_ADD, [],
                                       page13)
check("13 при добавлении отмечены все файлы",
      dlg13.priorities() == [gui_torrent.PRIORITY_ON] * len(FILE_ORDER),
      str(dlg13.priorities()))
check("13 три кнопки: «Отмена», «Скачать», «Посмотреть»",
      (dlg13.cancelButton.text(), dlg13.yesButton.text(),
       dlg13.watch_btn.text()) == ("Отмена", "Скачать", "Посмотреть"))
check("13 папка сохранения показана и её можно сменить",
      item13.save_path in dlg13.folder_label.text()
      and dlg13.folder_btn.isEnabled(), dlg13.folder_label.text())
check("13 при нескольких видео «Посмотреть» ждёт выделения",
      not dlg13.watch_btn.isEnabled())

dlg13.show()
pump(0.2)
leaf13 = dlg13._leaves[VIDEO_INDEX]
rect13 = dlg13.tree.visualItemRect(leaf13)
style13 = dlg13.tree.style()
box_x = rect13.left() + style13.pixelMetric(
    gui_torrent.QStyle.PM_IndicatorWidth) // 2
QTest.mouseClick(dlg13.tree.viewport(), Qt.LeftButton, Qt.NoModifier,
                 QPoint(box_x, rect13.center().y()))
pump(0.2)
check("13 клик по галочке снимает её и НЕ выделяет строку",
      dlg13.priorities()[VIDEO_INDEX] == 0
      and not leaf13.isSelected() and not dlg13.watch_btn.isEnabled(),
      f"{dlg13.priorities()} | выделено: {leaf13.isSelected()}")

text_x = rect13.left() + rect13.width() - 10
QTest.mouseClick(dlg13.tree.viewport(), Qt.LeftButton, Qt.NoModifier,
                 QPoint(text_x, rect13.center().y()))
pump(0.2)
check("13 клик по названию выделяет строку и не трогает галочку",
      leaf13.isSelected() and dlg13.priorities()[VIDEO_INDEX] == 0
      and dlg13.watch_btn.isEnabled()
      and dlg13.watch_target().index == VIDEO_INDEX,
      f"{dlg13.priorities()} | выделено: {leaf13.isSelected()}")
check("13 «Посмотреть» работает и на файле без галочки",
      dlg13.watch_target().index == VIDEO_INDEX)
dlg13.close()
dlg13.deleteLater()
pump(0.1)

# Фильм: единственное видео выделять не нужно
one_video13 = dataclasses.replace(
    item13, files=tuple(f for f in item13.files
                        if not f.path.endswith(".mp4")))
dlg13b = gui_torrent.TorrentFilesDialog(one_video13, gui_torrent.MODE_ADD, [],
                                        page13)
check("13 у фильма «Посмотреть» активна сразу, без выделения",
      dlg13b.watch_btn.isEnabled()
      and dlg13b.watch_target().index == VIDEO_INDEX)
dlg13b.deleteLater()

# ---- 14. Библиотека торрентов (долг 1.4) ----
# Своя история в settings (torrent_history), живое состояние — из движка.
# Запись появляется только после «Скачать»/«Посмотреть»; удаление записи
# снимает раздачу и с «Торрентов» (галочка — только судьба файлов);
# «Очистить» раздачи и файлы не трогает; раздача после focus_file не
# выглядит «готовой».
import json

seed.h.set_upload_limit(0)
for _page, _eng in PAGES:
    try:
        _eng.pause(IH)                  # чужие проверки сделаны, канал общий
    except Exception:
        pass
LAUNCHED.clear()


def library_for(page):
    lib = gui_torrent.TorrentLibraryPage(page, BRIDGES[page],
                                         page.settings)
    lib._notify = lambda kind, text: NOTES.append((kind, text))
    return lib


NOTES = []
SIDE_FILES = (te.RESUME_EXT, te.CHOSEN_EXT, te.PENDING_EXT)


def data_files(engine):
    return sorted(name for name in os.listdir(engine.resume_dir)
                  if name.startswith(IH))


def record15(page):
    return config.find_torrent_record(page.settings, IH)


# 14а. «Отмена» — записи нет
ANSWER[gui_torrent.MODE_ADD] = cancel_answer
ASKED.clear()
page15, eng15, save15 = make_page("s15")
page15.show()
pump(0.3)
page15.magnet_edit.setText(magnet())
page15.add_magnet()
wait_until(lambda: eng15.get(IH) is None and ASKED, 30)
pump(0.3)
check("14 после «Отмена» раздача в Библиотеку не попала",
      eng15.get(IH) is None and record15(page15) is None,
      str(page15.settings.get("torrent_history")))

# 14б. «Скачать» — запись с файлами, датой и тем, что скачано
ANSWER[gui_torrent.MODE_ADD] = download_all
saves_before = len(SAVES)
page15.magnet_edit.setText(magnet())
page15.add_magnet()
check("14 «Скачать»: окно ответило", wait_ready(page15))
rec = record15(page15)
check("14 «Скачать» создал запись: id, название, папка, файлы, дата",
      rec is not None and rec["save_path"] == save15
      and [f[0] for f in rec["files"]] == FILE_ORDER
      and [f[1] for f in rec["files"]] == [TI.files().file_size(i)
                                           for i in range(TI.num_files())]
      and abs(rec["added"] - time.time()) < 120,
      str(rec))
check("14 запись сохранена в settings.json (config.save)",
      len(SAVES) > saves_before
      and any(e.get("id") == IH for e in SAVES[-1].get("torrent_history", [])))
ok = wait_until(lambda: state_of(page15, IH) == te.STATE_SEEDING, 60)
check("14 докачано: в записи все файлы помечены скачанными",
      ok and wait_until(lambda: record15(page15)["done"]
                        == list(range(len(FILE_ORDER))), 10),
      str(record15(page15)))

lib15 = library_for(page15)
lib15.show()
pump(0.3)
row15 = lib15.rows().get(IH)
check("14 Библиотека показала раздачу одной строкой",
      row15 is not None and len(lib15.rows()) == 1
      and not lib15.empty_label.isVisible())
view15 = lib15.view(record15(page15), eng15.get(IH))
check("14 сериал: «Скачано 2 из 2 видеофайлов», «+ 1 файл», «Раздаётся»",
      "Скачано 2 из 2 видеофайлов" in view15.details
      and "+ 1 файл" in view15.details
      and view15.state_text == "Раздаётся"
      and view15.category == gui_torrent.LIB_DONE, view15.details)
check("14 свёрнутая строка: «Открыть папку», «Показать файлы»",
      row15.buttons() == ["Открыть папку", "Показать файлы"],
      str(row15.buttons()))
lib15.toggle_expanded(IH)
pump(0.2)
check("14 развёрнуто: у каждой скачанной серии «Открыть»",
      row15.buttons().count("Открыть") == 2
      and "Скрыть файлы" in row15.buttons(), str(row15.buttons()))
check("14 путь скачанного файла и папка раздачи",
      lib15.file_path(IH, VIDEO_INDEX) == os.path.join(save15, VIDEO_REL)
      and lib15.folder_of(IH) == os.path.join(save15, "Тестовая раздача"),
      f"{lib15.file_path(IH, VIDEO_INDEX)} | {lib15.folder_of(IH)}")
for _ in range(5):
    lib15.refresh()
pump(0.2)
check("14 повторный refresh без дублей и крашей",
      len(lib15.rows()) == 1 and len(lib15._row_widgets()) == 1)
lib15.search.setText("нет такой раздачи")
pump(0.1)
hidden = len(lib15.rows()) == 0
lib15.search.setText("")
pump(0.1)
check("14 поиск по названию", hidden and len(lib15.rows()) == 1)

# 14в. Удаление записи без галочки: раздача снята, файлы на месте, сирот нет
lib15._confirm_delete = lambda count: (True, False)
lib15.rows()[IH].select_check.setChecked(True)
check("14 «Удалить» активна при выбранной записи",
      lib15.delete_btn.isEnabled())
lib15._delete_selected()
pump(0.5)
check("14 без галочки: раздача снята с «Торрентов»",
      eng15.get(IH) is None and IH not in page15._cards)
check("14 без галочки: нет .fastresume/.chosen/.pending",
      wait_until(lambda: not data_files(eng15), 5), str(data_files(eng15)))
check("14 без галочки: файлы на диске целы",
      all(same_as_source(save15, rel) for rel in FILE_ORDER))
check("14 без галочки: запись убрана, InfoBar «Удалено: 1 запись»",
      record15(page15) is None and not lib15.rows()
      and NOTES[-1] == ("success", "Удалено: 1 запись"), str(NOTES[-1:]))

# 14г. focus_file: смотрят одну серию — раздача не выглядит «готовой»
ANSWER[gui_torrent.MODE_ADD] = watch_file(VIDEO_INDEX)
page16, eng16, save16 = make_page("s16")
page16.show()
pump(0.3)
page16.magnet_edit.setText(magnet())
page16.add_magnet()
check("14 «Посмотреть» запустил просмотр",
      wait_until(lambda: page16.is_watching(IH), 30))
check("14 «Посмотреть» тоже создал запись", record15(page16) is not None)
check("14 выбранная серия скачалась",
      wait_until(lambda: eng16.file_progress(IH)[VIDEO_INDEX]
                 >= TI.files().file_size(VIDEO_INDEX), 60))
page16.stop_watch()
check("14 раздача перешла в «Раздаётся»",
      wait_until(lambda: state_of(page16, IH) == te.STATE_SEEDING, 30),
      state_of(page16, IH))
check("14 в записи скачанной помечена только эта серия",
      wait_until(lambda: VIDEO_INDEX in record15(page16)["done"]
                 and OTHER_INDEX not in record15(page16)["done"], 10),
      str(record15(page16)))
lib16 = library_for(page16)
lib16.show()
pump(0.3)
item16 = eng16.get(IH)
view16 = lib16.view(record15(page16), item16)
check("14 частичный приоритет: «Скачано 1 из 2», категория «Не докачано»",
      "Скачано 1 из 2 видеофайлов" in view16.details
      and view16.category == gui_torrent.LIB_PARTIAL
      and " из " in view16.details.split("  •  ")[2], view16.details)
units16 = {u.index: u for u in view16.units}
check("14 частичный приоритет: серия «Скачано», соседняя «Не скачано»",
      gui_torrent.file_status(units16[VIDEO_INDEX], True) == "Скачано"
      and gui_torrent.file_status(units16[OTHER_INDEX], True)
      .startswith("Не скачано"),
      f"{gui_torrent.file_status(units16[VIDEO_INDEX], True)} | "
      f"{gui_torrent.file_status(units16[OTHER_INDEX], True)}")
check("14 частичный приоритет: соседнюю серию можно смотреть",
      units16[VIDEO_INDEX].action == gui_torrent.FILE_OPEN
      and units16[OTHER_INDEX].action == gui_torrent.FILE_WATCH,
      f"{units16[VIDEO_INDEX].action} {units16[OTHER_INDEX].action}")
finished16 = lib16.view(record15(page16),
                        dataclasses.replace(item16, state=te.STATE_FINISHED))
check("14 раздача без раздачи: «Выбранное скачано», а не «Готово»",
      finished16.state_text == "Выбранное скачано", finished16.state_text)
lib16.filter_combo.setCurrentText(gui_torrent.LIB_DONE)
pump(0.1)
in_done = IH in lib16.rows()
lib16.filter_combo.setCurrentText(gui_torrent.LIB_PARTIAL)
pump(0.1)
check("14 фильтр: частичная раздача в «Не докачано», не в «Скачано»",
      not in_done and IH in lib16.rows())
lib16.filter_combo.setCurrentIndex(0)
pump(0.1)

# 14д. Снять раздачу с карточки без файлов — запись остаётся
page16.confirm_remove = lambda name: (True, False)
page16.remove(IH)
pump(0.5)
rec16 = record15(page16)
check("14 карточка без файлов: запись осталась, скачанное помнит",
      eng16.get(IH) is None and rec16 is not None
      and VIDEO_INDEX in rec16["done"]
      and OTHER_INDEX not in rec16["done"], str(rec16))
view16b = lib16.view(rec16, None)
check("14 снятая раздача: «Убрана из Торрентов», серия открывается",
      view16b.state_text == "Убрана из Торрентов"
      and {u.index: u.action for u in view16b.units}
      == {VIDEO_INDEX: gui_torrent.FILE_OPEN, OTHER_INDEX: ""},
      view16b.details)
lib16.filter_combo.setCurrentText(gui_torrent.LIB_REMOVED)
pump(0.1)
check("14 фильтр «Убраны из Торрентов»", IH in lib16.rows())
lib16.filter_combo.setCurrentIndex(0)
pump(0.1)

# 14е. Запись переживает сохранение: JSON туда-обратно + дедуп при load
restored = json.loads(json.dumps(page16.settings, ensure_ascii=False))
restored["torrent_history"] = config._dedup_history(
    restored["torrent_history"] + restored["torrent_history"],
    config.TORRENT_FIELD)
check("14 после перезапуска запись та же (дубли схлопнуты)",
      [e for e in restored["torrent_history"] if e["id"] == IH] == [rec16],
      str(restored["torrent_history"]))

# 14ж. Удаление с галочкой записи, которой нет в движке: занятый файл
locked = open(os.path.join(save16, VIDEO_REL), "rb")
lib16._confirm_delete = lambda count: (True, True)
lib16.rows()[IH].select_check.setChecked(True)
lib16._delete_selected()
pump(0.2)
check("14 занятый файл: запись осталась, имя в InfoBar",
      record15(page16) is not None
      and NOTES[-1] == ("warning", "Не удалены: видео 1.mkv"),
      str(NOTES[-1:]))
locked.close()
lib16.rows()[IH].select_check.setChecked(True)
lib16._delete_selected()
pump(0.2)
root16 = os.path.join(save16, "Тестовая раздача")
check("14 с галочкой без движка: файлы и папка раздачи удалены",
      not os.path.exists(root16) and os.path.isdir(save16),
      str(os.listdir(save16)))
check("14 с галочкой без движка: запись убрана",
      record15(page16) is None and not lib16.rows())

# 14з. Удаление с галочкой раздачи в движке
ANSWER[gui_torrent.MODE_ADD] = download_all
page15.magnet_edit.setText(magnet())
page15.add_magnet()
check("14 снова «Скачать»", wait_ready(page15)
      and wait_until(lambda: state_of(page15, IH) == te.STATE_SEEDING, 60))
lib15.refresh()
lib15._confirm_delete = lambda count: (True, True)
lib15.rows()[IH].select_check.setChecked(True)
lib15._delete_selected()
check("14 с галочкой: раздача снята, файлы удалены движком",
      eng15.get(IH) is None
      and wait_until(lambda: not os.path.exists(
          os.path.join(save15, VIDEO_REL)), 10),
      str(os.listdir(save15)))
check("14 с галочкой: сирот в папке данных нет, записи нет",
      wait_until(lambda: not data_files(eng15), 5)
      and record15(page15) is None, str(data_files(eng15)))

# 14и. Карточка с файлами — запись уходит вместе с ними
page15.magnet_edit.setText(magnet())
page15.add_magnet()
wait_ready(page15)
check("14 запись есть перед удалением с карточки",
      record15(page15) is not None)
page15.confirm_remove = lambda name: (True, True)
page15.remove(IH)
pump(0.3)
check("14 карточка с файлами: запись убрана", record15(page15) is None)

# 14к. «Очистить данные библиотеки»: раздачи и файлы не трогаются
page15.magnet_edit.setText(magnet())
page15.add_magnet()
check("14 перед очисткой: раздача качается и есть в Библиотеке",
      wait_ready(page15)
      and wait_until(lambda: state_of(page15, IH) == te.STATE_SEEDING, 60)
      and record15(page15) is not None)
lib15.refresh()
check("14 «Очистить» активна при непустой истории",
      lib15.clear_btn.isEnabled())
lib15._confirm_clear = lambda: True
lib15._clear_library_data()
pump(1.5)                             # снимки движка идут дальше
check("14 очистка: список пуст, записи не вернулись со снимками движка",
      not lib15.rows() and record15(page15) is None
      and page15.settings.get("torrent_history") == [])
check("14 очистка: раздача в движке и файлы на месте",
      eng15.get(IH) is not None and IH in page15._cards
      and all(same_as_source(save15, rel) for rel in FILE_ORDER))
check("14 очистка: InfoBar, кнопка неактивна",
      NOTES[-1] == ("success", "Данные библиотеки очищены")
      and not lib15.clear_btn.isEnabled())

# 14л. Файлы записи вне папки сохранения не удаляются
outside = os.path.join(BASE, "чужой.txt")
with open(outside, "wb") as f:
    f.write(b"x")
bad_record = {"id": "0" * 40, "save_path": save16,
              "files": [["../чужой.txt", 1], [None, 1], "мусор"]}
check("14 путь за пределами папки сохранения не удаляется",
      gui_torrent.remove_record_files(bad_record) == []
      and os.path.isfile(outside))
check("14 ru_plural: 1 файл, 2 файла, 5 файлов, 11 файлов, 21 файл",
      [gui_torrent.ru_plural(n, "файл", "файла", "файлов")
       for n in (1, 2, 5, 11, 21)]
      == ["файл", "файла", "файлов", "файлов", "файл"])

# 14м. Лимит файлов записи: раздача с сотнями файлов не раздувает
# settings.json — хранятся пути только первых LIBRARY_FILES_MAX, дальше
# только счётчик и сумма размера (done тоже обрезан)
FAKE_TOTAL = gui_torrent.LIBRARY_FILES_MAX + 7
fake_files = tuple(
    te.TorrentFile(i, f"файл{i:03d}.bin", 1000 + i, 4)
    for i in range(FAKE_TOTAL))
fake_item = te.TorrentItem(
    id="f" * 40, name="Раздача с кучей файлов", state=te.STATE_SEEDING,
    progress=1.0, download_rate=0, upload_rate=0, num_peers=0, num_seeds=1,
    wanted_size=0, wanted_done=0, total_size=sum(f.size for f in fake_files),
    save_path=save15, has_metadata=True, files=fake_files,
    selected_size=sum(f.size for f in fake_files),
    added_time=int(time.time()))
fake_record = gui_torrent.library_record(fake_item, set(range(FAKE_TOTAL)))
check("14 лимит файлов: хранится не больше LIBRARY_FILES_MAX",
      len(fake_record["files"]) == gui_torrent.LIBRARY_FILES_MAX,
      str(len(fake_record["files"])))
extra_count = FAKE_TOTAL - gui_torrent.LIBRARY_FILES_MAX
extra_size = sum(f.size for f in fake_files[gui_torrent.LIBRARY_FILES_MAX:])
check("14 лимит файлов: files_more и files_more_size верны",
      fake_record["files_more"] == extra_count
      and fake_record["files_more_size"] == extra_size,
      str((fake_record.get("files_more"), fake_record.get("files_more_size"))))
check("14 лимит файлов: done обрезан до сохранённых файлов",
      fake_record["done"] == list(range(gui_torrent.LIBRARY_FILES_MAX)),
      f"хвост: {fake_record['done'][-3:]}")

fake_view = lib15.view(fake_record, None)
# view.details — «сырые» части без NBSP; неразрывные пробелы подставляет
# только строка (TorrentLibraryRow.update_view) для отображения
tail_marker = f"+ {extra_count} файлов"
shown_total = gui_torrent.fmt_size(
    fake_record["files_more_size"] + sum(e[1] for e in fake_record["files"]))
check("14 лимит файлов: «+N файлов» и общий размер учитывают скрытый хвост",
      tail_marker in fake_view.details and shown_total in fake_view.details,
      fake_view.details)

for lib in (lib15, lib16):
    lib.close()
ANSWER[gui_torrent.MODE_ADD] = download_all

# ---- 15. Повторное добавление раздачи, которая уже в списке ----
# Найдено на установленной копии перед релизом 1.1.0: движок для уже
# добавленной раздачи просто возвращает её id, а страница открывала окно
# «Что скачать» — и его «Отмена» (remove с удалением файлов) стирала уже
# скачанное. Теперь окно не открывается, пользователю — сообщение.
NOTES15 = []
page15._notify = lambda kind, text: NOTES15.append((kind, text))
ANSWER[gui_torrent.MODE_ADD] = cancel_answer     # если окно откроется — беда
ASKED.clear()
check("15 перед проверкой раздача в списке и раздаётся",
      state_of(page15, IH) == te.STATE_SEEDING, state_of(page15, IH))
page15.magnet_edit.setText(magnet())
readded = page15.add_magnet()
pump(1.5)
check("15 повторный magnet не открывает окно выбора", not ASKED, str(ASKED))
check("15 пользователю сказано, что раздача уже в списке",
      readded and ("info", "Эта раздача уже в списке") in NOTES15,
      str(NOTES15))
check("15 раздача осталась в движке и на карточке",
      page15.engine.get(IH) is not None and IH in page15._cards)
check("15 скачанные файлы на месте",
      all(same_as_source(save15, rel) for rel in FILE_ORDER))
check("15 поле magnet очищено", page15.magnet_edit.text() == "")
page15.add_torrent_file(TORRENT)
pump(1.5)
check("15 тот же .torrent тоже не открывает окно и ничего не удаляет",
      not ASKED and page15.engine.get(IH) is not None
      and all(same_as_source(save15, rel) for rel in FILE_ORDER), str(ASKED))
ANSWER[gui_torrent.MODE_ADD] = download_all

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
