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
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config

config.save = lambda settings: None

import libtorrent as lt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QPushButton

import gui
import gui_torrent
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
# Второй видеофайл — ради диалога выбора 2.2: он показывается только
# когда смотреть есть из чего (как у сериала на несколько серий)
VIDEO2 = os.path.join(CONTENT, "котики серия 2.mkv")


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


def make_video():
    if not os.path.isfile(FFMPEG):
        return "ffmpeg не найден — запустите build.bat или build_ffmpeg.ps1"
    import shutil
    shutil.copy2(FFMPEG, FF)
    return make_one(VIDEO, 120, "6M") or make_one(VIDEO2, 30, "4M")


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


def buttons_of(card):
    layout = card.actions_widget.layout()
    return [layout.itemAt(i).widget().text() for i in range(layout.count())
            if layout.itemAt(i).widget() is not None]


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

# ---- 2.2: диалог выбора файла на настоящем окне ----
# Диалог модальный: exec() остановил бы проверку, поэтому показываем его
# сам, снимаем и отвечаем за пользователя. Настоящий здесь — виджет и
# его отрисовка: именно её offscreen-тест не видит (находка 32).
TARGETS = ts.watchable_files(item.files)
check("к просмотру предложены оба видеофайла",
      len(TARGETS) == 2,
      str([os.path.basename(f.path) for f in TARGETS]))
SECOND = min(TARGETS, key=lambda f: f.size)
dialog_shot = {}


def live_ask(item_arg, targets):
    dialog = gui_torrent.WatchFileDialog(
        item_arg, targets, page.engine.file_progress(item_arg.id),
        window)
    dialog.show()
    pump(0.6)
    dialog_shot["path"] = shot("02-диалог-что-смотреть")
    dialog_shot["preselected"] = dialog._current_file()
    # Берём НЕ предвыбранный файл: так видно, что ответ диалога и правда
    # доходит до просмотра, а не совпал с прежним «сам выберу»
    dialog.select(SECOND.index)
    chosen = dialog._current_file()
    dialog.close()
    dialog.deleteLater()
    pump(0.3)
    return chosen


page.ask_watch_file = live_ask

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

# 2.2: диалог сработал и его ответ дошёл до просмотра
pre = dialog_shot.get("preselected")
check("в диалоге предвыбран самый большой файл",
      pre is not None and pre.index == target.index,
      getattr(pre, "path", None))
check("смотрится тот файл, который выбрали в диалоге",
      page._stream.active == (IH, SECOND.index),
      f"{page._stream.active}, ждали индекс {SECOND.index}")
check("ссылка плеера ведёт на выбранный файл",
      os.path.basename(SECOND.path)
      in urllib.parse.unquote(launched.get("url", "")),
      launched.get("url", ""))

# 2.2: индикатор подготовки. До первого обращения плеера карточка
# показывает подготовку, а не молчит (находка 11: до ~20 с)
card = page._cards.get(IH)
# Снимку нужен цикл событий: без него grabWindow берёт ПРЕЖНИЙ
# отрисованный кадр — на первом прогоне 2.2 снимок показал карточку до
# нажатия «Смотреть», хотя проверка уже видела новое состояние
pump(0.4)
check("пока плеер не отозвался — карточка показывает подготовку",
      (page.watch_status(IH) == "запускаем плеер…"
       or page.watch_status(IH).startswith("готовим плеер…"))
      and page.watch_status(IH) in card.meta_label.text()
      and buttons_of(card)[0] == "Остановить просмотр",
      f"{page.watch_status(IH)} | {buttons_of(card)} | "
      f"{card.meta_label.text()}")
shot("03-готовим-плеер")

served = window.torrent_stream.server
ok = wait_until(lambda: served.requests > 0, 60)
first_request = time.monotonic() - t0
check("плеер обратился к нашему серверу", ok, f"через {first_request:.1f} с")
ready = wait_until(lambda: page.watch_status(IH) == "идёт просмотр", 10)
check("индикатор сам сменился на «идёт просмотр»", ready,
      f"{page.watch_status(IH)} | подготовка длилась {first_request:.1f} с")

# Плеер читает поток: ждём, пока отдадим заметный объём
ok = wait_until(lambda: served.bytes > 8 * MB, 90)
playing = time.monotonic() - t0
check("плеер вычитал первые мегабайты потока", ok,
      f"{served.bytes / MB:.1f} МБ за {playing:.1f} с, "
      f"запросов {served.requests}")
pump(0.4)
check("карточка во время просмотра: «идёт просмотр» и кнопка остановки",
      "идёт просмотр" in card.meta_label.text()
      and buttons_of(card)[0] == "Остановить просмотр",
      f"{buttons_of(card)} | {card.meta_label.text()}")
shot("04-идёт-просмотр")
# Кнопки карточки не должны накапливаться (регресс на находку 32)
stale = [w for w in card.actions_widget.findChildren(QPushButton)
         if w.parent() is card.actions_widget]
in_layout = card.actions_widget.layout().count()
check("старые кнопки карточки не остаются поверх новых",
      len(stale) == in_layout, f"детей {len(stale)}, в раскладке {in_layout}")

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
