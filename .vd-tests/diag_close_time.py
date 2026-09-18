"""Диагностика находок 45/50: собранная копия закрывается дольше исходников.

Мерит закрытие ПО ЭТАПАМ, а не общим временем:

    t0            — PostMessage(WM_CLOSE) главному окну;
    fastresume    — mtime .fastresume (engine.shutdown сохранил раздачи);
    settings.json — mtime (последняя строка closeEvent, после shutdown);
    выход         — WaitForSingleObject на процессе.

Между t0 и выходом раз в ~15 мс снимаются CPU (user+kernel), рабочее
множество и число дескрипторов, раз в ~50 мс — число потоков. Это и есть
главный разделитель: прирост CPU = процесс СЧИТАЕТ (разбор объектов,
выгрузка), ноль = ЖДЁТ (сеть, join потоков, диск).

Один сценарий гоняется на двух целях:
    exe — УСТАНОВЛЕННАЯ копия (%LOCALAPPDATA%\\Programs\\VideoDownloader);
    src — то же приложение ИЗ ИСХОДНИКОВ (build-venv\\python.exe main.py).
Сборка 18.09 сделана из текущего HEAD, дерево чистое — код совпадает.

Запуск из корня проекта:
    build-venv\\Scripts\\python.exe .vd-tests\\diag_close_time.py exe download 3
    build-venv\\Scripts\\python.exe .vd-tests\\diag_close_time.py src download 3

Сценарии: empty (без раздач), download (активная закачка), seed (раздаёт).
Сид — локальный (seed_local_tracker.py, 127.0.0.1), интернет не нужен.
settings.json обеих целей сохраняется перед первым прогоном и
возвращается в конце.
"""
import ctypes
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import installed_ui as ui                                    # noqa: E402
import win_conns                                             # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VENV_PY = os.path.join(ROOT, "build-venv", "Scripts", "python.exe")
SEEDER = os.path.join(HERE, "seed_local_tracker.py")

BASE = os.path.join(tempfile.gettempdir(), "vd-close-diag")
DL = os.path.join(BASE, "downloads")
SEED = os.path.join(tempfile.gettempdir(), "vd-110", "seed")  # медиа уже созданы стендом 2.8
LOGS = os.path.join(BASE, "logs")

# Цели: где settings.json и где данные движка
EXE_SETTINGS = ui.SETTINGS
EXE_DATA = ui.TORRENT_DATA
SRC_SETTINGS = os.path.join(ROOT, "settings.json")
SRC_DATA = os.path.join(ROOT, "torrent-data")

CHOICE_TITLE = "Что скачать"

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
STILL_ACTIVE = 259
TH32CS_SNAPTHREAD = 0x00000004


class FILETIME(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    @property
    def seconds(self):
        return ((self.high << 32) | self.low) / 1e7


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


class THREADENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", ctypes.c_long), ("tpDeltaPri", ctypes.c_long),
                ("dwFlags", wintypes.DWORD)]


def open_process(pid):
    h = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
    if not h:
        raise OSError(f"OpenProcess({pid}) failed: {ctypes.get_last_error()}")
    return h


def cpu_times(handle):
    """user+kernel в секундах; после выхода процесса — итоговые."""
    c, e, k, u = FILETIME(), FILETIME(), FILETIME(), FILETIME()
    ok = kernel32.GetProcessTimes(handle, ctypes.byref(c), ctypes.byref(e),
                                  ctypes.byref(k), ctypes.byref(u))
    if not ok:
        return None
    return k.seconds, u.seconds


def working_set(handle):
    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters),
                                      counters.cb):
        return 0
    return counters.WorkingSetSize


def handle_count(handle):
    count = wintypes.DWORD(0)
    if not kernel32.GetProcessHandleCount(handle, ctypes.byref(count)):
        return 0
    return count.value


def thread_count(pid):
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap == -1:
        return 0
    entry = THREADENTRY32()
    entry.dwSize = ctypes.sizeof(entry)
    total = 0
    try:
        if not kernel32.Thread32First(snap, ctypes.byref(entry)):
            return 0
        while True:
            if entry.th32OwnerProcessID == pid:
                total += 1
            if not kernel32.Thread32Next(snap, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return total


class Watcher:
    """Снимки процесса, пока он закрывается."""

    def __init__(self, pid, interval=0.015, thread_interval=0.05):
        self.pid = pid
        self.handle = open_process(pid)
        self.interval = interval
        self.thread_interval = thread_interval
        self.samples = []            # (dt, kernel, user, ws, handles)
        self.threads = []            # (dt, count)
        self.conns = {}              # сокет -> (впервые, последний раз)
        self.exit_at = None
        self._t0 = None
        self._stop = threading.Event()

    def start(self, t0):
        self._t0 = t0
        self._fast = threading.Thread(target=self._sample, daemon=True)
        self._slow = threading.Thread(target=self._sample_threads, daemon=True)
        self._conns = threading.Thread(target=self._sample_conns, daemon=True)
        self._wait = threading.Thread(target=self._wait_exit, daemon=True)
        for t in (self._fast, self._slow, self._conns, self._wait):
            t.start()

    def _sample(self):
        while not self._stop.is_set():
            cpu = cpu_times(self.handle)
            if cpu is None:
                break
            self.samples.append((time.monotonic() - self._t0, cpu[0], cpu[1],
                                 working_set(self.handle),
                                 handle_count(self.handle)))
            self._stop.wait(self.interval)

    def _sample_threads(self):
        while not self._stop.is_set():
            self.threads.append((time.monotonic() - self._t0,
                                 thread_count(self.pid)))
            self._stop.wait(self.thread_interval)

    def _sample_conns(self):
        """Сокеты закрывающегося процесса — СНАРУЖИ: во время разрушения
        сессии libtorrent держит GIL, и изнутри приложения ни один поток
        Python не работает (проверено 18.09)."""
        while not self._stop.is_set():
            now = time.monotonic() - self._t0
            try:
                current = win_conns.snapshot(self.pid)
            except Exception:
                return
            for item in current:
                first, _last = self.conns.get(item, (now, now))
                self.conns[item] = (first, now)
            self._stop.wait(0.02)

    def _wait_exit(self):
        kernel32.WaitForSingleObject(self.handle, 60000)
        self.exit_at = time.monotonic() - self._t0
        self._stop.set()

    def finish(self, timeout=60):
        self._wait.join(timeout)
        self._stop.set()
        self._fast.join(2)
        self._slow.join(2)
        self._conns.join(2)
        final = cpu_times(self.handle)
        kernel32.CloseHandle(self.handle)
        return final


# ------------------------------------------------------------ окружение

_backups = {}


def backup_settings(path):
    if path in _backups:
        return
    dst = path + ".bak-closediag"
    if os.path.isfile(path):
        shutil.copy2(path, dst)
        _backups[path] = dst
    else:
        _backups[path] = None


def restore_settings():
    for path, dst in _backups.items():
        if dst and os.path.isfile(dst):
            shutil.copy2(dst, path)
            os.remove(dst)
            print(f"  settings.json возвращён: {path}")


def patch(path, **changes):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data.update(changes)
    tmp = path + ".tmp-diag"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def kill_all():
    for pid, _parent, _cmd in ui.processes("VideoDownloader.exe"):
        ui.kill(pid)
    # Приложение из исходников: python.exe с main.py / run_app_traced.py.
    # Забытый экземпляр держит мьютекс single_instance, и следующий молча
    # передаёт ему «покажись» и выходит — прогон падал бы на «окно не
    # появилось» (поймано 18.09).
    for pid, _parent, cmd in ui.processes("python.exe"):
        low = (cmd or "").lower()
        if "video-downloader" in low and ("main.py" in low
                                          or "run_app_traced.py" in low):
            ui.kill(pid)


def prepare(mode):
    settings = EXE_SETTINGS if mode == "exe" else SRC_SETTINGS
    data = EXE_DATA if mode == "exe" else SRC_DATA
    backup_settings(settings)
    kill_all()
    time.sleep(0.5)
    shutil.rmtree(data, ignore_errors=True)
    shutil.rmtree(DL, ignore_errors=True)
    os.makedirs(DL, exist_ok=True)
    os.makedirs(LOGS, exist_ok=True)
    patch(settings, app_mode="torrent", torrent_folder=DL,
          torrent_history=[], check_updates=False,
          torrent_seed_after_download=True)
    return settings, data


def app_pid(proc, timeout=30):
    """Настоящий pid приложения.

    Ловушка стенда: build-venv\\Scripts\\python.exe — не интерпретатор, а
    СТАБ-перенаправитель (255 КБ): он запускает настоящий
    Python313\\python.exe дочерним процессом, и окно с движком живёт
    ТАМ. Popen отдаёт pid стаба — по нему не найти ни окна, ни UIA, ни
    времени процессора. У exe-цели перенаправления нет.
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        kids = [pid for pid, parent, _cmd in ui.processes("python.exe")
                if parent == proc.pid]
        if kids:
            return kids[0]
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    return proc.pid


def start_app(mode, stamp):
    if mode == "exe":
        proc = ui.launch()
        pid = proc.pid
    else:
        script = os.path.join(ROOT, "main.py") if mode == "src" else \
            os.path.join(HERE, "run_app_traced.py")
        log = open(os.path.join(LOGS, f"{mode}-{stamp}.log"), "w",
                   encoding="utf-8", errors="replace")
        proc = subprocess.Popen([VENV_PY, script],
                                cwd=ROOT, stdout=log, stderr=log)
        pid = app_pid(proc)
        print(f"    стаб pid={proc.pid}, приложение pid={pid}")
        time.sleep(2.0)
        if proc.poll() is not None:
            raise RuntimeError(
                "приложение вышло сразу — мьютекс держит другой экземпляр")
    hwnd = ui.main_window(pid, timeout=90)
    if hwnd is None:
        ui.kill(pid)
        raise RuntimeError(f"{mode}: окно не появилось")
    if ui.wait_element(pid, lambda e: e.get("name") == "Добавить", 60) is None:
        ui.kill(pid)
        raise RuntimeError(f"{mode}: страница «Торренты» не открылась")
    return proc, pid, hwnd


# ------------------------------------------------------------ сид

def seed_start(limit_kb=1500):
    os.makedirs(SEED, exist_ok=True)
    stop = os.path.join(SEED, "stop")
    if os.path.exists(stop):
        os.remove(stop)
    shutil.copy2(os.path.join(ROOT, "dist", "VideoDownloader", "ffmpeg.exe"),
                 os.path.join(SEED, "ffmpeg.exe"))
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
            out = proc.stdout.read().decode("utf-8", "replace")
            raise RuntimeError(f"сид не поднялся:\n{out}")
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


# ------------------------------------------------------------ сценарий

def plain(text):
    return (text or "").replace(" ", " ")


def add_and_download(pid, magnet):
    """Добавить magnet и ответить «Скачать» в окне выбора (поток 2.6)."""
    for attempt in range(5):
        edits = [e for e in ui.elements(pid)
                 if e.get("type") == "ControlType.Edit"]
        nth = next((i for i, e in enumerate(edits)
                    if e.get("w") and e.get("h")), 0)
        res = ui.set_value(pid, magnet, ctl_type="ControlType.Edit", nth=nth)
        if ((res or {}).get("value") or "").strip() != magnet.strip():
            time.sleep(1.0)
            continue
        ui.invoke_async(pid, "Добавить")
        # Ждём ПОСЛЕДСТВИЯ и при молчании жмём снова: асинхронное Invoke
        # изредка не доходит (находка 57 — тот же урок на «Добавить»)
        end = time.monotonic() + 90
        asked = 0.0
        while time.monotonic() < end:
            names = [plain(n) for n in ui.names(pid)]
            if any("Скачивается" in n or "Раздаётся" in n for n in names):
                return True
            if any(CHOICE_TITLE in n for n in names):
                if time.monotonic() - asked > 8:
                    ui.invoke_async(pid, "Скачать")
                    asked = time.monotonic()
            time.sleep(0.5)
        # Карточка есть, но подпись другая (проверка, пауза) — этого
        # достаточно: замеряем закрытие, а не поток добавления
        if any("•" in plain(n) for n in ui.names(pid)):
            print("    карточка есть, подпись не «Скачивается» — идём дальше")
            return True
    raise RuntimeError("magnet не добавился")


def wait_card(pid, text, timeout):
    e = ui.wait_element(pid, lambda x: text in plain(x.get("name")), timeout)
    return plain(e.get("name")) if e else ""


def on_disk(path):
    """Сколько байт РЕАЛЬНО на диске: libtorrent создаёт файлы сразу
    полного размера, поэтому getsize о скачанном не говорит."""
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


def wait_bytes(root, want, timeout):
    """Закрывать при одинаковом объёме скачанного — иначе прогоны
    несравнимы: закрытие «пустой» раздачи и закрытие идущей закачки
    отличаются в разы (первый замер 18.09)."""
    end = time.monotonic() + timeout
    size = 0
    while time.monotonic() < end:
        size = tree_size(root)
        if size >= want:
            return size
        time.sleep(0.5)
    return size


# ------------------------------------------------------------ замер

def newest_mtime(paths):
    best = None
    for path in paths:
        try:
            m = os.stat(path).st_mtime
        except OSError:
            continue
        if best is None or m > best:
            best = m
    return best


def resume_paths(data_dir):
    resume = os.path.join(data_dir, "resume")
    try:
        return [os.path.join(resume, n) for n in os.listdir(resume)]
    except OSError:
        return []


def close_and_measure(pid, hwnd, settings_path, data_dir):
    watcher = Watcher(pid)
    wall0 = time.time()
    t0 = time.monotonic()
    watcher.start(t0)
    ui.close_window(hwnd)
    final = watcher.finish()
    exit_at = watcher.exit_at
    res = {"exit": exit_at, "samples": watcher.samples,
           "threads": watcher.threads, "conns": watcher.conns}
    if watcher.samples:
        first = watcher.samples[0]
        res["cpu_start"] = first[1] + first[2]
        res["ws_start"] = first[3]
        res["handles_start"] = first[4]
    if final:
        res["cpu_total"] = final[0] + final[1]
    fast = newest_mtime([p for p in resume_paths(data_dir)
                         if p.endswith(".fastresume")])
    res["fastresume_at"] = (fast - wall0) if fast else None
    st = newest_mtime([settings_path])
    res["settings_at"] = (st - wall0) if st else None
    res["wall0"] = wall0
    res["pid"] = pid
    return res


def print_trace(res):
    """Трассировка из run_app_traced.py: где именно шло время внутри
    closeEvent (см. режим trace)."""
    path = os.path.join(BASE, f"trace-{res['pid']}.log")
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        print("    трассировки нет")
        return
    print("    трассировка closeEvent (с от WM_CLOSE):")
    for line in lines:
        parts = line.split(" ", 1)
        try:
            when = float(parts[0]) - res["wall0"]
        except ValueError:
            continue
        if when < -1.0:
            continue                       # события до закрытия не нужны
        print(f"      {when:7.3f}  {parts[1]}")


def describe(res):
    exit_at = res["exit"]
    cpu_used = None
    if "cpu_total" in res and "cpu_start" in res:
        cpu_used = res["cpu_total"] - res["cpu_start"]
    print(f"    выход процесса      : {exit_at:.2f} с")
    for key, title in (("fastresume_at", "fastresume записан"),
                       ("settings_at", "settings.json записан")):
        val = res.get(key)
        print(f"    {title:<20}: " +
              (f"{val:.2f} с" if val is not None else "нет"))
    if cpu_used is not None:
        print(f"    CPU за закрытие     : {cpu_used:.2f} с "
              f"({cpu_used / exit_at * 100:.0f}% от времени)")
    # где именно шло время: разложение CPU по четвертям закрытия
    if res["samples"] and exit_at:
        marks = []
        step = exit_at / 4
        base = res["samples"][0][1] + res["samples"][0][2]
        for i in range(1, 5):
            edge = step * i
            prev = base
            for dt, k, u, _ws, _h in res["samples"]:
                if dt <= edge:
                    prev = k + u
            marks.append(prev - base)
        parts = []
        for i, val in enumerate(marks):
            before = marks[i - 1] if i else 0.0
            parts.append(f"{val - before:.2f}")
        print(f"    CPU по четвертям    : {' | '.join(parts)} с")
    if res["threads"]:
        line = " ".join(f"{dt:.2f}:{n}" for dt, n in res["threads"][::2])
        print(f"    потоки              : {line}")
    if res["samples"]:
        ws = [s[3] / 1048576 for s in res["samples"]]
        hd = [s[4] for s in res["samples"]]
        print(f"    рабочее множество   : {ws[0]:.0f} -> {ws[-1]:.0f} МБ, "
              f"дескрипторы {hd[0]} -> {hd[-1]}")
    if res.get("conns"):
        print("    сокеты (жили с…по, с):")
        rows = sorted(res["conns"].items(), key=lambda kv: -kv[1][1])
        for item, (first, last) in rows[:10]:
            print(f"      {first:5.2f}…{last:5.2f}  {item}")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "exe"
    scenario = sys.argv[2] if len(sys.argv) > 2 else "download"
    runs = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    if mode not in ("exe", "src", "trace"):
        raise SystemExit("режим: exe | src | trace")
    print(f"\n=== {mode} / {scenario} / {runs} прогонов ===")
    seed_proc = info = None
    results = []
    try:
        if scenario != "empty":
            seed_proc, info = seed_start()
            print(f"  сид поднят: {info['infohash'][:12]}…")
        for i in range(runs):
            stamp = time.strftime("%H%M%S")
            settings_path, data_dir = prepare(mode)
            proc, pid, hwnd = start_app(mode, f"{i}-{stamp}")
            print(f"  прогон {i + 1}/{runs}: pid={pid}")
            if scenario != "empty":
                add_and_download(pid, info["magnet"])
                want = "Раздаётся" if scenario == "seed" else "Скачивается"
                card = wait_card(pid, want, 120)
                if not card:
                    ui.kill(pid)
                    raise RuntimeError(f"нет карточки «{want}»")
                print(f"    карточка: {card}")
                got = wait_bytes(DL, 12 * 1048576, 90)
                print(f"    скачано к закрытию: {got / 1048576:.1f} МБ")
                time.sleep(2.0)
            else:
                time.sleep(3.0)
            res = close_and_measure(pid, hwnd, settings_path, data_dir)
            describe(res)
            if mode == "trace":
                print_trace(res)
            results.append(res)
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
    finally:
        seed_stop(seed_proc)
        kill_all()
        restore_settings()
    if results:
        times = [r["exit"] for r in results]
        print(f"\n  ИТОГ {mode}/{scenario}: медиана выхода "
              f"{statistics.median(times):.2f} с, прогоны "
              f"{', '.join(f'{t:.2f}' for t in times)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
