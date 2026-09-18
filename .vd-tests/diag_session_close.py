"""Разрушение сессии libtorrent: от чего зависит его время.

Замеры 18.09 (diag_close_time.py + run_app_traced.py) свели всё время
закрытия окна к одному шагу — `del ses` в torrent_engine.shutdown():
0.02 с, если к этому моменту пиров нет, и 0.71 с (иногда 1.2-1.7 с),
если хотя бы один пир ещё подключён. Наблюдение снаружи: деструктор
держит именно TCP-соединения с пиром, а слушающие сокеты, DHT, LSD и
UPnP закрывает сразу.

Здесь тот же шаг без GUI и без движка — на голой сессии, чтобы гонять
варианты десятками, а не по одному прогону в две минуты.

Варианты (аргумент):
    pause     — как в приложении: ses.pause(), потом del (по умолчанию);
    plain     — del без паузы;
    remove    — убрать раздачи из сессии (remove_torrent), потом del;
    waitpeers — пауза и ждать, пока пиров не станет 0 (не дольше 3 с);
    disconnect— обнулить лимит соединений и дать 50 мс, потом del;
    nodht     — как pause, но сессия без DHT/LSD/UPnP (контроль: они ли).

Запуск (из корня проекта):
    build-venv\\Scripts\\python.exe .vd-tests\\diag_session_close.py [вариант] [прогонов] [МБ]
"""
import ctypes
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import libtorrent as lt                                      # noqa: E402
import config                                                # noqa: E402
import win_conns                                             # noqa: E402

SEEDER = os.path.join(HERE, "seed_local_tracker.py")
SEED = os.path.join(tempfile.gettempdir(), "vd-110", "seed")
BASE = os.path.join(tempfile.gettempdir(), "vd-sesclose")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HOLD = 0.0                       # сколько секунд держать сессию перед закрытием

SETTINGS = {
    "alert_mask": int(lt.alert_category.error) | int(lt.alert_category.status)
    | int(lt.alert_category.storage) | int(lt.alert_category.piece_progress),
    "stop_tracker_timeout": 1,
    "user_agent": f"VideoDownloader/{config.APP_VERSION} "
                  f"libtorrent/{lt.__version__}",
}


def on_disk(path):
    high = ctypes.c_ulong(0)
    low = kernel32.GetCompressedFileSizeW(ctypes.c_wchar_p(path),
                                          ctypes.byref(high))
    if low == 0xFFFFFFFF and ctypes.get_last_error():
        return 0
    return (high.value << 32) + low


def tree_size(root):
    total = 0
    for base, _dirs, files in os.walk(root):
        for name in files:
            try:
                total += on_disk(os.path.join(base, name))
            except OSError:
                pass
    return total


def seed_start(limit_kb=1500):
    os.makedirs(SEED, exist_ok=True)
    stop = os.path.join(SEED, "stop")
    if os.path.exists(stop):
        os.remove(stop)
    shutil.copy2(os.path.join(ROOT, "dist", "VideoDownloader", "ffmpeg.exe"),
                 os.path.join(SEED, "ffmpeg.exe"))
    proc = subprocess.Popen([sys.executable, SEEDER, SEED, str(limit_kb)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    info = os.path.join(SEED, "seed.json")
    end = time.monotonic() + 300
    while time.monotonic() < end:
        if os.path.isfile(info):
            time.sleep(0.5)
            with open(info, "r", encoding="utf-8") as f:
                return proc, json.load(f)
        if proc.poll() is not None:
            raise RuntimeError("сид не поднялся:\n" +
                               proc.stdout.read().decode("utf-8", "replace"))
        time.sleep(1.0)
    raise RuntimeError("сид не написал seed.json")


def seed_stop(proc):
    if proc is None:
        return
    open(os.path.join(SEED, "stop"), "w").close()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


def peers_now(ses):
    """Пиров в сессии — по её же счётчикам (как в state-снимке)."""
    ses.post_session_stats()
    end = time.monotonic() + 1.0
    while time.monotonic() < end:
        ses.wait_for_alert(50)
        for alert in ses.pop_alerts():
            if type(alert).__name__ == "session_stats_alert":
                return alert.values.get("peer.num_peers_connected", -1)
    return -1


def run_once(magnet, want_mb, variant, index):
    data = os.path.join(BASE, f"run{index}")
    shutil.rmtree(data, ignore_errors=True)
    os.makedirs(data, exist_ok=True)
    settings = dict(SETTINGS)
    if variant == "nodht":
        settings.update({"enable_dht": False, "enable_lsd": False,
                         "enable_upnp": False, "enable_natpmp": False})
    params = lt.session_params()
    params.settings = settings
    ses = lt.session(params)
    atp = lt.parse_magnet_uri(magnet)
    atp.save_path = data
    handle = ses.add_torrent(atp)
    end = time.monotonic() + 120
    while time.monotonic() < end and not handle.status().has_metadata:
        ses.wait_for_alert(200)
        ses.pop_alerts()
    while time.monotonic() < end:
        ses.wait_for_alert(200)
        ses.pop_alerts()
        if tree_size(data) >= want_mb * 1048576:
            break
    got = tree_size(data)
    # Сессия приложения к моменту закрытия живёт минуты, а стенд —
    # десятки секунд; держим её столько же, разбирая алерты, как движок
    if HOLD:
        stop = time.monotonic() + HOLD
        while time.monotonic() < stop:
            ses.wait_for_alert(200)
            ses.pop_alerts()
            ses.post_torrent_updates()

    # Пиров и сокеты смотрим ДО действия варианта: сам опрос стоит
    # ~0.1 с, и за это время пауза успевает разогнать пиров — из-за
    # этого первый прогон стенда не воспроизводил медленный случай
    before = peers_now(ses)
    socks = win_conns.snapshot(os.getpid())
    if variant in ("pause", "waitpeers", "nodht"):
        ses.pause()
    elif variant == "remove":
        for h in ses.get_torrents():
            ses.remove_torrent(h)
    elif variant == "disconnect":
        ses.apply_settings({"connections_limit": 0})
        time.sleep(0.05)
    if variant == "waitpeers":
        stop = time.monotonic() + 3.0
        while time.monotonic() < stop and peers_now(ses) > 0:
            time.sleep(0.05)
    del handle
    t = time.monotonic()
    del ses
    seconds = time.monotonic() - t
    peer_socks = [s for s in socks if "127.0.0.1" in s]
    print(f"  {index}: скачано {got / 1048576:5.1f} МБ, пиров {before}, "
          f"сокетов к пирам {len(peer_socks)} -> del ses {seconds:.2f} с")
    return seconds, before


def main():
    global HOLD
    variant = sys.argv[1] if len(sys.argv) > 1 else "pause"
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    want_mb = float(sys.argv[3]) if len(sys.argv) > 3 else 6
    HOLD = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
    print(f"\n=== del ses: вариант {variant}, {runs} прогонов, "
          f"{want_mb} МБ ===")
    seed_proc = None
    times = []
    try:
        seed_proc, info = seed_start()
        for i in range(runs):
            seconds, _peers = run_once(info["magnet"], want_mb, variant, i + 1)
            times.append(seconds)
    finally:
        seed_stop(seed_proc)
        shutil.rmtree(BASE, ignore_errors=True)
    slow = [t for t in times if t > 0.3]
    print(f"\n  медиана {statistics.median(times):.2f} с, максимум "
          f"{max(times):.2f} с, медленных (> 0.3 с) {len(slow)} из "
          f"{len(times)}: {', '.join(f'{t:.2f}' for t in times)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
