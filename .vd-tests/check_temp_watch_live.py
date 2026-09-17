# -*- coding: utf-8 -*-
"""Живая проверка «Посмотреть во временную папку» с НАСТОЯЩИМИ плеерами.

Настоящее окно программы, настоящий локальный сид на 127.0.0.1 и
настоящие mpv и VLC (портативные копии прототипа Этапа 0.2). Проверяется
то, что офлайн-тесты подменяют: плеер действительно играет наш поток,
наблюдатель видит его позицию (mpv — по JSON IPC, VLC — по status.json),
закрытие плеера ставит временную раздачу на паузу, повторное открытие
уходит на прошлую позицию, а доигранный до конца файл удаляется.

Плееры запускаются через ОБЁРТКУ (.cmd): она добавляет ключи «без окна»
и ускорение показа и записывает полученные ключи в лог — по нему видно,
что программа передала --start / --start-time. Имя обёртки mpv.cmd /
vlc.cmd не случайно: player.py узнаёт плеер по имени файла.

  build-venv\\Scripts\\python.exe .vd-tests\\check_temp_watch_live.py

Настоящий рой не нужен. settings.json не трогается.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config

config.save = lambda settings: None

import libtorrent as lt
from PySide6.QtWidgets import QApplication

import gui
import gui_torrent
import player
import torrent_stream as ts

PASS, FAIL = [], []


def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details, flush=True)


MB = 1024 * 1024
BASE = tempfile.mkdtemp(prefix="vd-temp-watch-")
SRC_ROOT = os.path.join(BASE, "источник")
CONTENT = os.path.join(SRC_ROOT, "Сериал про котиков")
os.makedirs(CONTENT)
LONG_NAME = "котики s01e01.mkv"
SHORT_NAME = "котики s01e02.mkv"
LONG = os.path.join(CONTENT, LONG_NAME)
SHORT = os.path.join(CONTENT, SHORT_NAME)

FFMPEG = os.path.join(ROOT, "build-ffmpeg-cache", "extracted",
                      "ffmpeg-n9.0-latest-win64-gpl-9.0", "bin", "ffmpeg.exe")
FF = os.path.join(BASE, "ffmpeg.exe")

PLAYERS = os.path.join(os.environ["TEMP"], "vd-torrent-proto", "players")
MPV = os.path.join(PLAYERS, "mpv", "mpv.exe")
VLC = os.path.join(PLAYERS, "vlc-3.0.23", "vlc.exe")
ARGS_LOG = os.path.join(BASE, "args.log")


def make_one(path, seconds, bitrate):
    cmd = [FF, "-v", "error", "-y",
           "-f", "lavfi",
           "-i", f"testsrc2=size=1280x720:rate=25:duration={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
           "-c:v", "libx264", "-preset", "veryfast", "-b:v", bitrate,
           "-c:a", "aac", "-shortest", path]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode:
        return result.stderr.decode("utf-8", "replace")[:300]
    return ""


def prepare_media():
    if not os.path.isfile(FFMPEG):
        return "ffmpeg не найден — запустите build.bat или build_ffmpeg.ps1"
    for path in (MPV, VLC):
        if not os.path.isfile(path):
            return f"плеер не найден: {path}"
    shutil.copy2(FFMPEG, FF)
    # Первая серия длинная: её смотрят и закрывают на середине. Вторая
    # короткая: её доигрывают до конца, чтобы проверить удаление файла
    return make_one(LONG, 90, "5M") or make_one(SHORT, 20, "2M")


def wrapper(name, exe, extra):
    """Обёртка-плеер: без окна, с ускорением и с логом ключей (ASCII)."""
    path = os.path.join(BASE, name)
    with open(path, "w", encoding="ascii") as f:
        f.write("@echo off\r\n")
        f.write(f'echo {name} %* >> "{ARGS_LOG}"\r\n')
        f.write(f'"{exe}" {extra} %*\r\n')
    return path


error = prepare_media()
check("подготовка: видео и плееры на месте", not error, error)
if error:
    sys.exit(1)
# Плеер без окна, но в ОБЫЧНОМ темпе: «досмотрел» определяется по
# последней известной позиции, а опрос идёт раз в секунду — при
# ускоренном показе короткий файл кончается между опросами, и конец
# просмотра виден не был бы (проверено: 8 с на скорости 4x не ловились)
MPV_WRAP = wrapper("mpv.cmd", MPV,
                   "--no-config --vo=null --ao=null --no-terminal")
VLC_WRAP = wrapper("vlc.cmd", VLC,
                   "--intf=dummy --vout=dummy --aout=dummy --no-video-title-show")
print(f"первая серия {os.path.getsize(LONG) / MB:.0f} МБ, "
      f"вторая {os.path.getsize(SHORT) / MB:.0f} МБ", flush=True)

TORRENT = os.path.join(BASE, "live.torrent")
fs = lt.file_storage()
lt.add_files(fs, CONTENT)
ct = lt.create_torrent(fs, 512 * 1024, lt.create_torrent.v1_only)
lt.set_piece_hashes(ct, SRC_ROOT)
with open(TORRENT, "wb") as f:
    f.write(lt.bencode(ct.generate()))
TI = lt.torrent_info(TORRENT)
IH = TI.info_hashes().v1.to_bytes().hex()
ORDER = [TI.files().file_path(i) for i in range(TI.num_files())]
I_LONG = ORDER.index(os.path.join("Сериал про котиков", LONG_NAME))
I_SHORT = ORDER.index(os.path.join("Сериал про котиков", SHORT_NAME))

LOCAL = {"enable_dht": False, "enable_lsd": False, "enable_upnp": False,
         "enable_natpmp": False}
seed_ses = lt.session(dict(LOCAL, listen_interfaces="127.0.0.1:0"))
atp = lt.add_torrent_params()
atp.ti = lt.torrent_info(TORRENT)
atp.save_path = SRC_ROOT
atp.flags = atp.flags | lt.torrent_flags.seed_mode
sh = seed_ses.add_torrent(atp)
end = time.monotonic() + 20
while str(sh.status().state) != "seeding" and time.monotonic() < end:
    time.sleep(0.1)
sh.set_upload_limit(3 * MB)
check("подготовка: сид раздаёт", str(sh.status().state) == "seeding",
      f"порт {seed_ses.listen_port()}")

app = QApplication(sys.argv)
app.setApplicationName("VD-temp-watch-live")
settings = dict(config.load())
settings["history"] = []
settings["torrent_history"] = []
settings["check_updates"] = False
settings["app_mode"] = "torrent"
settings["torrent_folder"] = os.path.join(BASE, "Загрузки")
settings["torrent_player"] = MPV_WRAP
window = gui.MainWindow(settings)
# Свои папки: иначе проверка складывает раздачи в рабочую torrent-data\
# проекта, а временные — в общий корень установленной копии
window.torrent_engine.data_dir = os.path.join(BASE, "данные")
window.torrent_engine.resume_dir = os.path.join(BASE, "данные", "resume")
window.torrent_engine.watch_root = os.path.join(BASE, "временные")
window.torrent_engine._boot_time = lambda: 1.0
# Единственный пир стенда после паузы возвращается только через
# min_reconnect_time (60 с по умолчанию, находка 18) — в рое таких пауз
# нет, здесь это просто мешает. Сессия ещё не создана: движок
# поднимается лениво, в showEvent страницы
window.torrent_engine._settings["min_reconnect_time"] = 1
page = window.torrent_page
engine = window.torrent_engine
window.resize(1100, 720)
window.show()

NOTES = []
page._notify = lambda kind, text: NOTES.append((kind, text))


def pump(seconds):
    end_at = time.monotonic() + seconds
    while time.monotonic() < end_at:
        app.processEvents()
        time.sleep(0.02)


def wait_until(pred, timeout):
    end_at = time.monotonic() + timeout
    while time.monotonic() < end_at:
        app.processEvents()
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return bool(pred())


def watch_answer(index):
    def answer(item, mode):
        video = next(f for f in ts.watchable_files(item.files)
                     if f.index == index)
        return gui_torrent.FilesChoice(
            gui_torrent.ACTION_WATCH,
            tuple(f.priority for f in item.files), video, item.save_path)
    return answer


def kill_player():
    """«Пользователь закрыл плеер»: обёртка и сам плеер — одним деревом."""
    proc = page._player_proc
    if proc is None:
        return False
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                   capture_output=True)
    return True


def args_for(name):
    if not os.path.isfile(ARGS_LOG):
        return []
    with open(ARGS_LOG, encoding="ascii", errors="replace") as f:
        return [line.strip() for line in f if line.startswith(name)]


def temp_file(rel):
    return os.path.join(engine.watch_dir(IH), "Сериал про котиков", rel)


magnet = (f"magnet:?xt=urn:btih:{IH}&dn=live"
          f"&x.pe=127.0.0.1:{seed_ses.listen_port()}")

# ---- 1. mpv: просмотр во временную папку ----
page.ask_choice = watch_answer(I_LONG)
page.magnet_edit.setText(magnet)
page.add_magnet()
started = wait_until(lambda: page.is_watching(IH), 60)
check("1 «Посмотреть» начал просмотр в настоящем mpv",
      started and engine.is_temp(IH) and page._watcher is not None,
      str(page.watch_status(IH)))
check("1 качается во временную папку, в загрузках ничего нет",
      wait_until(lambda: os.path.isfile(temp_file(LONG_NAME)), 30)
      and not os.path.isdir(os.path.join(settings["torrent_folder"],
                                         "Сериал про котиков")),
      engine.get(IH).save_path)
playing = wait_until(lambda: (page._watcher.position or 0) >= 14, 90)
check("1 mpv реально играет наш поток: позиция дошла до 14 с",
      playing, f"позиция {page._watcher.position}, "
               f"длительность {page._watcher.duration}")
check("1 наблюдатель получил ответ плеера (IPC работает)",
      page._watcher.reached and page._watcher.duration
      and abs(page._watcher.duration - 90) < 2,
      str(page._watcher.duration))
card = page._cards.get(IH)
check("1 на карточке видно, что папка временная, и нет «Пауза»",
      gui_torrent.TEMP_TEXT in card.meta_label.text()
      and "Пауза" not in [card.actions_widget.layout().itemAt(i).widget().text()
                          for i in range(card.actions_widget.layout().count())],
      card.meta_label.text())

# ---- 2. Закрыли плеер на середине ----
last_pos = page._watcher.position
done_before = engine.file_progress(IH)[I_LONG]
kill_player()
paused = wait_until(lambda: gui_torrent.te.STATE_PAUSED
                    == engine.get(IH).state, 30)
check("2 закрыли mpv — закачка встала на паузу",
      paused and not page.is_watching(IH), engine.get(IH).state)
saved = engine.watch_position(IH, I_LONG)
check("2 позиция просмотра сохранена",
      saved is not None and abs(saved - last_pos) < 5,
      f"сохранено {saved}, последняя позиция плеера {last_pos}")
check("2 скачанное во временной папке осталось",
      engine.file_progress(IH)[I_LONG] >= done_before,
      f"{done_before} -> {engine.file_progress(IH)[I_LONG]}")
check("2 пользователю сказано про паузу",
      any(k == "info" and "паузе" in t for k, t in NOTES), str(NOTES[-1:]))

# ---- 3. Снова «Посмотреть»: продолжаем с прошлой позиции ----
page.watch(IH, index=I_LONG)
resumed = wait_until(lambda: any("--start=" in line
                                 for line in args_for("mpv.cmd")), 30)
start_arg = [line for line in args_for("mpv.cmd") if "--start=" in line]
check("3 mpv открыт с прошлой позиции (--start)",
      resumed and f"--start={int(saved - 5)}" in " ".join(start_arg),
      str(start_arg[-1:])[:200])
check("3 докачка продолжилась, а не началась с нуля",
      wait_until(lambda: engine.file_progress(IH)[I_LONG] > done_before, 60),
      f"{done_before} -> {engine.file_progress(IH)[I_LONG]}")
kill_player()
wait_until(lambda: not page.is_watching(IH), 30)

# ---- 4. Доиграли до конца: файл удаляется, раздача остаётся ----
NOTES.clear()
page.watch(IH, index=I_SHORT)
pump(0.3)


def diag4(label):
    item = engine.get(IH)
    watcher = page._watcher
    print(f"    [{label}] {item.state} пиров {item.num_peers}, "
          f"серия 2 {engine.file_progress(IH)[I_SHORT]} из "
          f"{TI.files().file_size(I_SHORT)}, позиция "
          f"{None if watcher is None else watcher.position}, "
          f"плеер жив {page._player_proc is not None and page._player_proc.poll() is None}",
          flush=True)
check("4 вторая серия открылась в mpv",
      wait_until(lambda: page.is_watching(IH), 60), str(page.watch_status(IH)))
diag4("начало")
ended = wait_until(lambda: page._watcher is None, 180)
diag4("конец ожидания")
check("4 mpv доиграл файл до конца и закрылся сам", ended,
      str(page.watch_status(IH)))
check("4 досмотренный файл удалён из временной папки",
      wait_until(lambda: not os.path.isfile(temp_file(SHORT_NAME)), 30),
      str(os.listdir(os.path.dirname(temp_file(SHORT_NAME)))))
check("4 первая серия и сама раздача на месте",
      engine.get(IH) is not None and os.path.isfile(temp_file(LONG_NAME)))
check("4 после перепроверки раздача на паузе, не в ошибке",
      wait_until(lambda: engine._handles[IH].status().paused
                 and IH not in engine._rechecking
                 and engine.get(IH).state != gui_torrent.te.STATE_ERROR, 60),
      f"{engine.get(IH).state}, перепроверка "
      f"{IH in engine._rechecking}")
check("4 пользователю сказано про удаление файла",
      any(k == "success" and "удал" in t for k, t in NOTES), str(NOTES[-1:]))

# ---- 5. VLC: позиция по status.json и --start-time ----
settings["torrent_player"] = VLC_WRAP
engine.set_watch_position(IH, I_LONG, None)
page.watch(IH, index=I_LONG)
check("5 VLC открыл наш поток",
      wait_until(lambda: page.is_watching(IH), 60), str(page.watch_status(IH)))
vlc_pos = wait_until(lambda: (page._watcher.position or 0) >= 12, 120)
check("5 VLC отдаёт позицию по своему http (status.json)",
      vlc_pos and page._watcher.reached,
      f"позиция {page._watcher.position}, "
      f"длительность {page._watcher.duration}")
vlc_last = page._watcher.position
kill_player()
check("5 закрыли VLC — временная раздача на паузе",
      wait_until(lambda: engine._handles[IH].status().paused, 30)
      and not page.is_watching(IH), engine.get(IH).state)
vlc_saved = engine.watch_position(IH, I_LONG)
check("5 позиция VLC сохранена",
      vlc_saved is not None and abs(vlc_saved - vlc_last) < 5,
      f"сохранено {vlc_saved}, последняя позиция {vlc_last}")
page.watch(IH, index=I_LONG)
resumed5 = wait_until(lambda: any("--start-time=" in line
                                  for line in args_for("vlc.cmd")), 30)
vlc_args = [line for line in args_for("vlc.cmd") if "--start-time=" in line]
check("5 VLC открыт с прошлой позиции (--start-time, не --start)",
      resumed5 and f"--start-time={int(vlc_saved - 5)}"
      in " ".join(vlc_args)
      and "--no-one-instance" in " ".join(vlc_args),
      str(vlc_args[-1:])[:220])
kill_player()
wait_until(lambda: not page.is_watching(IH), 30)

# ---- 6. Перезагрузка компьютера убирает временную раздачу ----
temp_dir = engine.watch_dir(IH)
page.stop_watchers()
window.close()
pump(0.5)
engine.shutdown(timeout=3.0)
import torrent_engine as te

eng2 = te.TorrentEngine(data_dir=engine.data_dir,
                        listen_interfaces="127.0.0.1:0",
                        extra_settings=dict(LOCAL),
                        watch_root=engine.watch_root,
                        boot_time=lambda: time.time())
eng2.start()
check("6 после перезагрузки временная раздача и её папка удалены",
      eng2.get(IH) is None
      and wait_until(lambda: not os.path.exists(temp_dir), 20),
      str(os.listdir(engine.watch_root)
          if os.path.isdir(engine.watch_root) else []))
eng2.shutdown(timeout=2.0)

del sh, seed_ses
pump(0.3)
shutil.rmtree(BASE, ignore_errors=True)
print()
print(f"ИТОГ: {len(PASS)} PASS, {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
