"""Где именно уходит время в закрытии: engine.shutdown() по шагам.

Замер diag_close_time.py показал, что почти всё время закрытия окна
приходится на closeEvent (settings.json пишется последней строкой), а не
на выход процесса, и что решает не «собранная копия / исходники», а
АКТИВНАЯ ЗАКАЧКА. Здесь тот же shutdown повторён по шагам, без GUI:

    streams — закрытие потоков просмотра;
    pause   — ses.pause();
    flush   — flush_cache() + ожидание cache_flushed_alert;
    save    — save_resume_data() + ожидание save_resume_data_alert;
    join    — остановка потока алертов (ses.wait_for_alert(250) внутри);
    del ses — разрушение сессии libtorrent.

Шаги повторяют код torrent_engine.shutdown() дословно (контроль: рядом
гоняется НАСТОЯЩИЙ shutdown, и его общее время сверяется с суммой шагов).

Запуск (из корня проекта):
    build-venv\\Scripts\\python.exe .vd-tests\\diag_shutdown_stages.py [МБ] [прогонов] [условие]

Условия:
    base      — как в приложении;
    idle      — раздача добавлена, но данных не качали (контроль);
    real      — вызвать настоящий engine.shutdown(), без разбивки;
    notracker — magnet без трекера, пир подключается напрямую
                (проверка «виноват ли stop_tracker_timeout»);
    tracker5  — stop_tracker_timeout=5 вместо 1 (та же проверка с другой
                стороны).
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

import libtorrent as lt                                      # noqa: E402
import torrent_engine as te                                  # noqa: E402

SEEDER = os.path.join(HERE, "seed_local_tracker.py")
VENV_PY = sys.executable
SEED = os.path.join(tempfile.gettempdir(), "vd-110", "seed")
BASE = os.path.join(tempfile.gettempdir(), "vd-shutdown-diag")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


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


# ------------------------------------------------------------------ сид

def seed_start(limit_kb=1500):
    os.makedirs(SEED, exist_ok=True)
    stop = os.path.join(SEED, "stop")
    if os.path.exists(stop):
        os.remove(stop)
    ff = os.path.join(ROOT, "dist", "VideoDownloader", "ffmpeg.exe")
    shutil.copy2(ff, os.path.join(SEED, "ffmpeg.exe"))
    proc = subprocess.Popen([VENV_PY, SEEDER, SEED, str(limit_kb)],
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


# --------------------------------------------------------------- шаги

def staged_shutdown(engine, timeout=3.0):
    """Шаги torrent_engine.shutdown() с замером каждого."""
    marks = []
    t0 = time.monotonic()
    last = [t0]

    def mark(name):
        now = time.monotonic()
        marks.append((name, now - last[0]))
        last[0] = now

    deadline = t0 + timeout
    engine._close_all_streams()
    mark("streams")
    with engine._lock:
        ses = engine._ses
        ses.pause()
        mark("pause")
        handles = [(tid, h) for tid, h in engine._handles.items()
                   if h.is_valid()]
        for tid, handle in handles:
            if not handle.status().has_metadata:
                continue
            engine._pending_flush.add(tid)
            handle.flush_cache()
        while engine._pending_flush and time.monotonic() < deadline:
            engine._saves_done.wait(deadline - time.monotonic())
        engine._pending_flush.clear()
        mark("flush")
        for tid, handle in handles:
            engine._pending_saves.add(tid)
            handle.save_resume_data(lt.save_resume_flags_t.save_info_dict)
        asked = len(engine._pending_saves)
        while engine._pending_saves and time.monotonic() < deadline:
            engine._saves_done.wait(deadline - time.monotonic())
        unsaved = len(engine._pending_saves)
        engine._pending_saves.clear()
        mark("save")
    engine._stop.set()
    if engine._thread is not None:
        engine._thread.join(2.0)
    mark("join")
    with engine._lock:
        engine.on_change = engine.on_list_change = None
        engine._handles.clear()
        engine._ses = None
    del handles
    mark("clear")
    del ses
    mark("del ses")
    return marks, asked - unsaved


# -------------------------------------------------------------- прогон

def settings_for(tokens):
    """Что выключаем, чтобы найти виновника в разрушении сессии.

    Замер 18.09: шаг `del ses` двумодальный — 0.02 с или 0.71 с. Дольше
    живёт сессия — больше успевают DHT, UPnP/NAT-PMP и трекеры, и всё это
    разбирается в деструкторе.
    """
    extra = {}
    if "tracker5" in tokens:
        extra["stop_tracker_timeout"] = 5
    if "tracker0" in tokens:
        extra["stop_tracker_timeout"] = 0
    if "nodht" in tokens:
        extra["enable_dht"] = False
    if "noupnp" in tokens:
        extra["enable_upnp"] = False
        extra["enable_natpmp"] = False
    if "nolsd" in tokens:
        extra["enable_lsd"] = False
    return extra or None


def run_once(magnet, want_mb, condition, index):
    tokens = condition.split("+")
    data = os.path.join(BASE, f"run{index}")
    dl = os.path.join(data, "downloads")
    shutil.rmtree(data, ignore_errors=True)
    os.makedirs(dl, exist_ok=True)
    extra = settings_for(tokens)
    engine = te.TorrentEngine(data_dir=data, watch_root=os.path.join(data, "w"),
                              extra_settings=extra)
    engine.start()
    if "notracker" in tokens:
        magnet = magnet.split("&tr=")[0]
    tid = engine.add_magnet(magnet, dl, defer=True)
    end = time.monotonic() + 120
    while time.monotonic() < end:
        item = engine.get(tid)
        if item and item.files:
            break
        time.sleep(0.2)
    else:
        raise RuntimeError("метаданные не пришли")
    engine.begin_download(tid)
    if "notracker" in tokens:
        with engine._lock:
            engine._handles[tid].connect_peer(("127.0.0.1", SEED_PORT))
    got = 0
    if "idle" not in tokens:
        end = time.monotonic() + 180
        while time.monotonic() < end:
            got = tree_size(dl)
            if got >= want_mb * 1048576:
                break
            time.sleep(0.2)
    else:
        time.sleep(3.0)
        got = tree_size(dl)
    # «long»: дать сессии пожить, как живёт настоящее окно (DHT, UPnP,
    # анонсы успевают развернуться) — иначе медленный `del ses` ловится
    # раз через раз
    hold = next((int(t[4:]) for t in tokens if t.startswith("long")
                 and t[4:].isdigit()), 60 if "long" in tokens else 0)
    if hold:
        print(f"    держим сессию {hold} с…")
        time.sleep(hold)
    item = engine.get(tid)
    peers = item.num_peers if item else -1
    if "real" in tokens:
        t0 = time.monotonic()
        res = engine.shutdown(timeout=3.0)
        total = time.monotonic() - t0
        print(f"  прогон {index}: скачано {got / 1048576:.1f} МБ, пиров "
              f"{peers} -> НАСТОЯЩИЙ shutdown {total:.2f} с, {res}")
        return total
    marks, saved = staged_shutdown(engine)
    total = sum(v for _n, v in marks)
    line = "  ".join(f"{n} {v:.2f}" for n, v in marks)
    print(f"  прогон {index}: скачано {got / 1048576:.1f} МБ, пиров {peers}, "
          f"сохранено {saved}")
    print(f"    {line}   ИТОГО {total:.2f} с")
    return total, dict(marks)


def main():
    want_mb = float(sys.argv[1]) if len(sys.argv) > 1 else 12
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    condition = sys.argv[3] if len(sys.argv) > 3 else "base"
    print(f"\n=== shutdown по шагам: {want_mb} МБ, {runs} прогонов, "
          f"условие {condition} ===")
    seed_proc = None
    totals = []
    stages = []
    try:
        seed_proc, info = seed_start()
        global SEED_PORT
        SEED_PORT = info["seed_port"]
        print(f"  сид поднят, порт {SEED_PORT}")
        for i in range(runs):
            res = run_once(info["magnet"], want_mb, condition, i + 1)
            if "real" in condition.split("+"):
                totals.append(res)
            else:
                totals.append(res[0])
                stages.append(res[1])
    finally:
        seed_stop(seed_proc)
    print(f"\n  медиана ИТОГО: {statistics.median(totals):.2f} с "
          f"({', '.join(f'{t:.2f}' for t in totals)})")
    if stages:
        print("  медианы по шагам:")
        for name in stages[0]:
            vals = [s[name] for s in stages]
            print(f"    {name:<8} {statistics.median(vals):.2f} с "
                  f"({', '.join(f'{v:.2f}' for v in vals)})")
    return 0


SEED_PORT = 0

if __name__ == "__main__":
    sys.exit(main())
