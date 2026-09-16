# -*- coding: utf-8 -*-
"""Индикатор «готовим плеер» — НАСТОЯЩИМИ часами (сессия 2.3).

В 2.2 пороги 8 и 45 с проверялись сдвигом часов в тесте: на этой машине
плеер прогрет и отзывается за 0.1-1.5 с, а воспроизвести те самые ~20 с
из находки 11 (Защитник проверяет файлы плеера при первом запуске)
случая не было. Здесь задержку создаём сами — плеером назначается
скрипт-обёртка, которая сначала спит, а потом (или не потом, а никогда)
обращается к нашему потоку. Часы настоящие, сервер настоящий, счётчики
потока настоящие, окно настоящее.

Два прогона:
  A. Обёртка молчит 12 с, затем читает поток: подпись должна пройти
     «запускаем плеер…» -> «готовим плеер… N с» (порог 8 с) -> «идёт
     просмотр», и ровно один InfoBar с объяснением задержки.
  B. Обёртка не открывает поток вовсе: на 45-й секунде подпись
     становится «плеер не отозвался», поток при этом НЕ рвётся, а
     ссылка уходит в буфер обмена.

Прогон Б идёт около минуты — это плата за настоящие часы.

  build-venv\\Scripts\\python.exe .vd-tests\\check_prepare_indicator_live.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import libtorrent as lt
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QGuiApplication

import config

config.save = lambda settings: None

import gui
import gui_torrent

MB = 1024 * 1024
BASE = os.path.join(tempfile.gettempdir(), "vd-prepare-live")
OUT = os.path.join(BASE, "снимки")
SRC_ROOT = os.path.join(BASE, "источник")
CONTENT = os.path.join(SRC_ROOT, "Раздача")
FFMPEG = os.path.join(ROOT, "dist", "VideoDownloader", "ffmpeg.exe")
VIDEO = os.path.join(CONTENT, "кино.mkv")

PASS, FAIL = [], []


def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details, flush=True)


def say(text):
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


shutil.rmtree(BASE, ignore_errors=True)
for path in (OUT, CONTENT):
    os.makedirs(path, exist_ok=True)

if not os.path.isfile(FFMPEG):
    print("ffmpeg не найден — запустите build.bat или build_ffmpeg.ps1")
    sys.exit(1)
FF = os.path.join(BASE, "ffmpeg.exe")
shutil.copy2(FFMPEG, FF)
res = subprocess.run(
    [FF, "-v", "error", "-y", "-f", "lavfi",
     "-i", "testsrc2=size=1280x720:rate=25:duration=40",
     "-f", "lavfi", "-i", "sine=frequency=440:duration=40",
     "-c:v", "libx264", "-preset", "veryfast", "-b:v", "6M",
     "-c:a", "aac", "-shortest", VIDEO], capture_output=True)
check("подготовка: видео сгенерировано", res.returncode == 0,
      res.stderr.decode("utf-8", "replace")[:200])
if res.returncode:
    sys.exit(1)

TORRENT = os.path.join(BASE, "live.torrent")
fs = lt.file_storage()
lt.add_files(fs, CONTENT)
ct = lt.create_torrent(fs, 512 * 1024, lt.create_torrent.v1_only)
lt.set_piece_hashes(ct, SRC_ROOT)
with open(TORRENT, "wb") as f:
    f.write(lt.bencode(ct.generate()))
IH = lt.torrent_info(TORRENT).info_hashes().v1.to_bytes().hex()

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
# Медленно нарочно: файл не должен докачаться за время прогона, иначе
# «Смотреть» откроет его напрямую с диска, мимо сервера и индикатора
sh.set_upload_limit(120 * 1024)
check("подготовка: сид раздаёт", str(sh.status().state) == "seeding",
      f"порт {seed_ses.listen_port()}, {os.path.getsize(VIDEO) / MB:.0f} МБ")

# --- подставной «плеер»: обёртка, которая спит, а потом читает поток ---
# Имена служебных файлов — латиницей: .cmd пишется в ASCII
# (правило проекта), а в нём лежит путь к этому скрипту
READER = os.path.join(BASE, "reader.py")
with open(READER, "w", encoding="utf-8") as f:
    f.write("import sys, urllib.request\n"
            "req = urllib.request.Request(sys.argv[1],\n"
            "                             headers={'Range': 'bytes=0-65535'})\n"
            "urllib.request.urlopen(req, timeout=30).read()\n"
            "import time; time.sleep(120)\n")


def make_player(name, sleep_s, then_read):
    """Плеер-обёртка: .cmd, потому что player.launch зовёт его как
    обычный исполняемый файл с URL в первом аргументе."""
    path = os.path.join(BASE, name)
    lines = ["@echo off", f"ping -n {sleep_s + 1} 127.0.0.1 >nul"]
    if then_read:
        lines.append(f'"{sys.executable}" "{READER}" %1')
    else:
        lines.append("ping -n 120 127.0.0.1 >nul")
    with open(path, "w", encoding="ascii") as f:
        f.write("\r\n".join(lines) + "\r\n")
    return path


SLOW_PLAYER = make_player("slow-player.cmd", 12, True)
MUTE_PLAYER = make_player("mute-player.cmd", 1, False)

app = QApplication(sys.argv)
settings = dict(config.load())
settings["history"] = []
settings["check_updates"] = False
settings["app_mode"] = "torrent"
settings["torrent_folder"] = os.path.join(BASE, "Загрузки")
settings["torrent_player"] = SLOW_PLAYER
window = gui.MainWindow(settings)
window.torrent_engine.data_dir = os.path.join(BASE, "данные")
window.torrent_engine.resume_dir = os.path.join(BASE, "данные", "resume")
window.resize(1100, 720)
window.show()


def pump(seconds):
    end_at = time.monotonic() + seconds
    while time.monotonic() < end_at:
        app.processEvents()
        time.sleep(0.01)


def shot(name):
    pixmap = QGuiApplication.primaryScreen().grabWindow(window.winId())
    path = os.path.join(OUT, name + ".png")
    pixmap.save(path)
    return path


def wait_until(pred, timeout):
    end_at = time.monotonic() + timeout
    while time.monotonic() < end_at:
        if pred():
            return True
        pump(0.05)
    return False


page = None
for widget in window.findChildren(gui_torrent.TorrentPage):
    page = widget
    break
pump(1.0)
check("окно режима Torrent открыто", page is not None, str(page))

engine = window.torrent_engine
# Подсказка о пире прямо в ссылке — так же, как в остальных живых
# проверках: трекеров и DHT у локальной раздачи нет
tid = engine.add_magnet(
    f"magnet:?xt=urn:btih:{IH}&x.pe=127.0.0.1:{seed_ses.listen_port()}",
    settings["torrent_folder"])
ok = wait_until(lambda: (engine.get(tid) or None)
                and engine.get(tid).has_metadata, 20)
check("раздача добавлена, метаданные есть", ok, str(engine.get(tid)))
# Немного скачиваем, чтобы просмотр пошёл через сервер, а не с диска
ok = wait_until(lambda: (engine.get(tid).progress or 0) > 0.02, 60)
check("пошла закачка (файл ещё далеко не целиком)", ok,
      f"{engine.get(tid).progress * 100:.1f}%")

infobars = []
page._notify = lambda kind, text: infobars.append((kind, text))

# ---------------------------------------------------------------- A
say("прогон A: плеер отзывается через 12 с")
t0 = time.monotonic()
started = page.watch(tid)
check("A. просмотр начат, плеер запущен", started, "")

pump(3.0)
status_early = page.watch_status(tid)
check("A. до порога 8 с подпись «запускаем плеер…»",
      status_early == "запускаем плеер…",
      f"{status_early} (прошло {time.monotonic() - t0:.1f} с)")

ok = wait_until(lambda: page.watch_status(tid).startswith("готовим плеер"),
                12)
elapsed = time.monotonic() - t0
check("A. после 8 с подпись «готовим плеер… N с»", ok,
      f"{page.watch_status(tid)} на {elapsed:.1f} с")
check("A. порог сработал по настоящим часам (8-10 с)",
      ok and 8.0 <= elapsed <= 10.5, f"{elapsed:.1f} с")
shot("01-готовим-плеер")

hints = [t for kind, t in infobars if "Плеер ещё открывается" in t]
ok = wait_until(lambda: page.watch_status(tid) == "идёт просмотр", 30)
check("A. плеер обратился к потоку — «идёт просмотр»", ok,
      f"{page.watch_status(tid)} на {time.monotonic() - t0:.1f} с")
hints = [t for kind, t in infobars if "Плеер ещё открывается" in t]
check("A. объяснение задержки показано РОВНО один раз", len(hints) == 1,
      f"показов {len(hints)}")
shot("02-идёт-просмотр")
page.stop_watch()
pump(0.5)

# ---------------------------------------------------------------- B
say("прогон B: плеер не открывает поток вовсе — ждём порог 45 с")
infobars.clear()
page.settings["torrent_player"] = MUTE_PLAYER
t0 = time.monotonic()
started = page.watch(tid)
check("B. просмотр начат", started, "")
ok = wait_until(lambda: page.watch_status(tid) == "плеер не отозвался", 70)
elapsed = time.monotonic() - t0
check("B. на 45-й секунде подпись «плеер не отозвался»", ok,
      f"{page.watch_status(tid)} на {elapsed:.1f} с")
check("B. порог 45 с сработал по настоящим часам", ok and 44 <= elapsed <= 50,
      f"{elapsed:.1f} с")
check("B. поток НЕ разорван — просмотр ещё числится активным",
      page.is_watching(tid), str(page.is_watching(tid)))
told = [t for kind, t in infobars if "не обратился к потоку" in t]
check("B. сказано про буфер обмена", len(told) == 1 and "буфер" in told[0],
      "; ".join(told)[:120])
shot("03-плеер-не-отозвался")

page.stop_watch()
pump(0.5)
window.close()
pump(0.5)
engine.shutdown(timeout=5.0)
seed_ses.remove_torrent(sh)
del seed_ses
say(f"снимки: {OUT}")

print(f"\nИТОГ: PASS {len(PASS)}, FAIL {len(FAIL)}")
for name in FAIL:
    print("  FAIL:", name)
sys.exit(1 if FAIL else 0)
