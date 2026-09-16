# -*- coding: utf-8 -*-
"""Живая проверка просмотра во время закачки (сессия 2.1).

Настоящее окно программы, настоящий локальный сид на 127.0.0.1 и
НАСТОЯЩИЙ плеер (mpv или VLC, тот же, что найдёт player.py). Интернет и
настоящий рой не нужны — раздачу отдаёт второй экземпляр libtorrent в
этом же процессе, скорость отдачи ограничена, чтобы файл не скачался
раньше, чем плеер начнёт играть.

Плеер запускается БЕЗ окна (dummy-вывод у VLC, null у mpv): проверяем не
картинку, а что он реально читает поток и доходит до воспроизведения.
Видео настоящее (ffmpeg lavfi), иначе плееру нечего декодировать.

  build-venv\\Scripts\\python.exe .vd-tests\\check_torrent_stream_live.py

Снимки окна — в %TEMP%\\vd-stream-live. settings.json не трогается.
"""
import os
import subprocess
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
import player
import torrent_stream as ts

OUT = os.path.join(os.environ["TEMP"], "vd-stream-live")
os.makedirs(OUT, exist_ok=True)

PASS, FAIL = [], []


def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details, flush=True)


MB = 1024 * 1024
BASE = tempfile.mkdtemp(prefix="vd-stream-live-")
SRC_ROOT = os.path.join(BASE, "источник")
CONTENT = os.path.join(SRC_ROOT, "Фильм про котиков")
os.makedirs(CONTENT)

FFMPEG = os.path.join(ROOT, "build-ffmpeg-cache", "extracted",
                      "ffmpeg-n9.0-latest-win64-gpl-9.0", "bin", "ffmpeg.exe")
# Копию берём в TEMP: build_ffmpeg.ps1 пересоздаёт extracted и падает на
# занятом exe, если в это время идёт сборка (правило из AGENTS.md)
FF = os.path.join(BASE, "ffmpeg.exe")
VIDEO = os.path.join(CONTENT, "котики 1080p.mkv")


def make_video():
    if not os.path.isfile(FFMPEG):
        return "ffmpeg не найден — запустите build.bat или build_ffmpeg.ps1"
    import shutil
    shutil.copy2(FFMPEG, FF)
    cmd = [FF, "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=120",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=120",
           "-c:v", "libx264", "-preset", "veryfast", "-b:v", "6M",
           "-c:a", "aac", "-shortest", VIDEO]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode:
        return result.stderr.decode("utf-8", "replace")[:300]
    return ""


error = make_video()
check("подготовка: настоящее видео сгенерировано", not error, error)
if error:
    sys.exit(1)
with open(os.path.join(CONTENT, "субтитры.srt"), "wb") as f:
    f.write(b"1\n00:00:01,000 --> 00:00:03,000\n" + "мяу".encode("utf-8"))
size_mb = os.path.getsize(VIDEO) / MB
print(f"видео {size_mb:.0f} МБ", flush=True)

TORRENT = os.path.join(BASE, "live.torrent")
fs = lt.file_storage()
lt.add_files(fs, CONTENT)
ct = lt.create_torrent(fs, 512 * 1024, lt.create_torrent.v1_only)
lt.set_piece_hashes(ct, SRC_ROOT)
with open(TORRENT, "wb") as f:
    f.write(lt.bencode(ct.generate()))
TI = lt.torrent_info(TORRENT)
IH = TI.info_hashes().v1.to_bytes().hex()

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
# 1.5 МБ/с — быстрее битрейта, но заметно медленнее, чем «сразу целиком»
sh.set_upload_limit(1500 * 1024)
check("подготовка: сид раздаёт", str(sh.status().state) == "seeding",
      f"порт {seed_ses.listen_port()}")

app = QApplication(sys.argv)
app.setApplicationName("VD-stream-live")
settings = dict(config.load())
settings["history"] = []
settings["check_updates"] = False
settings["app_mode"] = "torrent"
settings["torrent_folder"] = os.path.join(BASE, "Загрузки")
settings["torrent_player"] = ""
window = gui.MainWindow(settings)
# Своя папка данных движка: иначе проверка складывает раздачи в рабочую
# torrent-data\ проекта, и со следующего прогона они восстанавливаются из
# fastresume (та же ловушка, что в check_torrent_gui_live.py). Подменяем
# ДО show(): движок поднимается лениво, в showEvent страницы.
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
    screen = QGuiApplication.primaryScreen()
    pixmap = screen.grabWindow(window.winId())
    path = os.path.join(OUT, name + ".png")
    pixmap.save(path)
    return path


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


pump(1.0)
try:
    exe = player.resolve("")
    check("плеер найден", True, f"{player.label_for(exe)}: {exe}")
except player.PlayerNotFound as exc:
    check("плеер найден", False, str(exc))
    exe = ""

page = window.torrent_page
magnet = (f"magnet:?xt=urn:btih:{IH}&dn=live"
          f"&x.pe=127.0.0.1:{seed_ses.listen_port()}")
page.magnet_edit.setText(magnet)
page.add_magnet()
ok = wait_until(lambda: page.engine.get(IH) is not None
                and page.engine.get(IH).has_metadata, 30)
pump(0.5)
check("раздача добавлена, метаданные получены", ok)
shot("01-добавлена")

item = page.engine.get(IH)
target = ts.choose_video_file(item.files)
check("выбран самый большой видеофайл",
      target is not None and target.path.endswith("котики 1080p.mkv"),
      getattr(target, "path", None))

# Запускаем НАСТОЯЩИЙ плеер, но без окна: проверяем чтение, не картинку
LOG = os.path.join(OUT, "player.log")
launched = {}
PROCS = []
real_launch = player.launch


def headless_launch(url, exe_path):
    launched["url"] = url
    name = os.path.basename(exe_path).lower()
    if name.startswith("mpv"):
        cmd = [exe_path, "--no-config", "--vo=null", "--ao=null",
               "--idle=no", "--keep-open=no", f"--log-file={LOG}",
               "--msg-level=all=v", url]
    else:
        cmd = [exe_path, "-I", "dummy", "--dummy-quiet", "--vout=dummy",
               "--aout=dummy", "--no-video-title-show", "--file-logging",
               f"--logfile={LOG}", "--verbose=2", url]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL)
    PROCS.append(proc)
    return proc


player.launch = headless_launch
t0 = time.monotonic()
started = page.watch(IH)
check("«Смотреть» запустил плеер", started and "url" in launched,
      launched.get("url", ""))
check("карточка показывает «Остановить просмотр»",
      page.is_watching(IH), str(page._stream.active))

served = window.torrent_stream.server
ok = wait_until(lambda: served.requests > 0, 60)
first_request = time.monotonic() - t0
check("плеер обратился к нашему серверу", ok, f"через {first_request:.1f} с")

# Плеер читает поток: ждём, пока отдадим заметный объём
ok = wait_until(lambda: served.bytes > 8 * MB, 90)
playing = time.monotonic() - t0
check("плеер вычитал первые мегабайты потока", ok,
      f"{served.bytes / MB:.1f} МБ за {playing:.1f} с, "
      f"запросов {served.requests}")
shot("02-идёт-просмотр")

pump(8.0)
after = window.torrent_stream.server.bytes
item = page.engine.get(IH)
check("раздача при этом качается", item.progress > 0,
      f"прогресс {item.progress * 100:.0f}%, отдано {after / MB:.1f} МБ")

# Главное: закрытие окна во время просмотра
t_close = time.monotonic()
window.close()
close_s = time.monotonic() - t_close
check("закрытие окна во время просмотра — в бюджете", close_s < 4.5,
      f"{close_s:.2f} с")
check("сервер просмотра и движок закрыты",
      not window.torrent_stream.server.running
      and window.torrent_engine._ses is None)
pump(0.5)
if os.path.isfile(LOG):
    with open(LOG, "rb") as f:
        tail = f.read()[-4000:].decode("utf-8", "replace")
    # Ищем ОШИБКИ ПОТОКА, а не любое слово error: у VLC на этой машине
    # нормой идут "d3d11va warning: failed to get the MatchingDeviceId"
    # (аппаратный декодер) и "faad warning: decoded zero sample" — к
    # нашему серверу они отношения не имеют (живая проверка 16.09.2026)
    NOISE = ("d3d11va", "dxva", "direct3d", "vout", "faad", "avcodec")
    bad = [line for line in tail.splitlines()
           if " error: " in line.lower()
           and not any(module in line.lower() for module in NOISE)]
    check("в логе плеера нет ошибок чтения потока", not bad,
          " | ".join(bad[:3]) if bad else f"лог {os.path.getsize(LOG)} байт")

player.launch = real_launch
# Плеер запускали мы сами и без окна — за собой убираем (в программе он,
# наоборот, переживает закрытие окна: решение №6)
for proc in PROCS:
    try:
        proc.terminate()
        proc.wait(5)
    except Exception:
        pass
print()
print("снимки:", OUT)
print(f"ИТОГ: {len(PASS)} PASS, {len(FAIL)} FAIL")
del sh, seed_ses
sys.exit(1 if FAIL else 0)
