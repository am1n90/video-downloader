"""Общие утилиты прототипа торрент-движка (Этап 0). НЕ часть приложения.

Память процесса (Private Bytes / Working Set), реально занятое место на
диске (sparse-файлы), сессия libtorrent, журнал замеров в results/*.jsonl.
"""
import ctypes
import ctypes.wintypes as wt
import datetime
import json
import os
import threading
import time

import libtorrent as lt

PROTO_TMP = os.path.join(os.environ["TEMP"], "vd-torrent-proto")
HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(HERE, "results")


class _PMC(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class _MEMSTAT(ctypes.Structure):
    _fields_ = [
        ("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD),
        ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GetCurrentProcess.restype = wt.HANDLE
_k32.K32GetProcessMemoryInfo.argtypes = [
    wt.HANDLE, ctypes.POINTER(_PMC), wt.DWORD]
_k32.K32GetProcessMemoryInfo.restype = wt.BOOL
_k32.GetCompressedFileSizeW.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.DWORD)]
_k32.GetCompressedFileSizeW.restype = wt.DWORD
_k32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MEMSTAT)]
_k32.GlobalMemoryStatusEx.restype = wt.BOOL

MB = 2 ** 20


def memory_mb():
    """(private, working_set, peak_working_set) текущего процесса, МБ."""
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(_PMC)
    _k32.K32GetProcessMemoryInfo(_k32.GetCurrentProcess(),
                                 ctypes.byref(pmc), pmc.cb)
    return (pmc.PrivateUsage / MB, pmc.WorkingSetSize / MB,
            pmc.PeakWorkingSetSize / MB)


def sys_memory_mb():
    """(свободно физической памяти МБ, загрузка памяти %)."""
    ms = _MEMSTAT()
    ms.dwLength = ctypes.sizeof(_MEMSTAT)
    _k32.GlobalMemoryStatusEx(ctypes.byref(ms))
    return ms.ullAvailPhys / MB, ms.dwMemoryLoad


def allocated_bytes(path):
    """Реально занятое место на диске (для sparse-файла меньше размера)."""
    high = wt.DWORD(0)
    ctypes.set_last_error(0)
    low = _k32.GetCompressedFileSizeW(path, ctypes.byref(high))
    if low == 0xFFFFFFFF and ctypes.get_last_error() != 0:
        return None
    return (high.value << 32) + low


def files_on_disk(root):
    """Список файлов под root: относительный путь, размер, занято на диске."""
    out = []
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            path = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            alloc = allocated_bytes(path)
            out.append({
                "path": os.path.relpath(path, root),
                "size_mb": round(size / MB, 1),
                "alloc_mb": None if alloc is None else round(alloc / MB, 1),
            })
    return out


def sha1_hex(h):
    to_bytes = getattr(h, "to_bytes", None)
    if to_bytes is not None:
        return to_bytes().hex()
    s = str(h)
    return s if len(s) == 40 else bytes(h).hex()


class Report:
    """Журнал: строки в консоль + события в results/<name>-<время>.jsonl."""

    def __init__(self, name):
        os.makedirs(RESULTS_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = os.path.join(RESULTS_DIR, f"{name}-{stamp}.jsonl")
        self.t0 = time.monotonic()
        self._alert_counts = {}
        self._lock = threading.Lock()   # запись из потоков HTTP-сервера

    def log(self, msg):
        print(f"[{time.monotonic() - self.t0:8.2f}] {msg}", flush=True)

    def record(self, quiet=False, **data):
        data["t"] = round(time.monotonic() - self.t0, 3)
        line = json.dumps(data, ensure_ascii=False) + "\n"
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
        if not quiet:
            self.log("RESULT " + json.dumps(data, ensure_ascii=False))

    def alert(self, name, message):
        """Первые 5 алертов каждого типа — в журнал, дальше только счётчик."""
        n = self._alert_counts.get(name, 0) + 1
        self._alert_counts[name] = n
        if n <= 5:
            self.record(event="alert", type=name, message=message)

    def alert_counts(self):
        return dict(self._alert_counts)


def alert_message(a):
    """a.message() безопасно: на русской Windows libtorrent обрезает текст
    системной ошибки посреди многобайтовой буквы -> UnicodeDecodeError
    (найдено 15.09.2026 на udp/peer-алертах)."""
    try:
        return a.message()
    except UnicodeDecodeError as exc:
        return f"<undecodable message: {exc}>"


def drain_alerts(ses, report, on_alert=None):
    for a in ses.pop_alerts():
        if on_alert is not None:
            on_alert(a)
        name = type(a).__name__
        if any(w in name for w in ("error", "failed", "rejected",
                                   "performance")):
            report.alert(name, alert_message(a))


def alert_mask():
    mask = 0
    for cat in (lt.alert_category.error, lt.alert_category.status,
                lt.alert_category.storage,
                lt.alert_category.performance_warning):
        mask |= int(cat)
    return mask


def make_session(listen, dht=True, local_only=False, dht_state=None,
                 extra=None):
    """Сессия libtorrent. local_only — только 127.0.0.1: без DHT/LSD/UPnP."""
    settings = {
        "listen_interfaces": listen,
        "enable_dht": dht and not local_only,
        "enable_lsd": not local_only,
        "enable_upnp": not local_only,
        "enable_natpmp": not local_only,
        "alert_mask": alert_mask(),
        "user_agent": "vd-proto/0.1 libtorrent/" + lt.__version__,
    }
    if extra:
        settings.update(extra)
    if dht_state:
        params = lt.read_session_params(
            dht_state, lt.save_state_flags_t.save_dht_state)
    else:
        params = lt.session_params()
    params.settings = settings
    return lt.session(params)


def save_dht_state(ses):
    return lt.write_session_params_buf(
        ses.session_state(lt.save_state_flags_t.save_dht_state))
