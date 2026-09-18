"""Управление УСТАНОВЛЕННЫМ exe снаружи процесса (сессия 2.4).

Живые проверки 2.1-2.3 строили MainWindow в своём процессе и читали
виджеты напрямую. Установленную копию так не проверить — она frozen и
живёт отдельным процессом. Поэтому здесь:
- кнопки нажимаются через UI Automation (uia.ps1): Qt отдаёт наружу имена
  кнопок и полей, это тот же канал, что у экранного диктора;
- состояние читается по побочным следам: подписи карточек (тоже через
  UIA), torrent.log, слушающие порты процесса, командные строки плееров;
- снимок окна — PIL.ImageGrab по прямоугольнику окна.
"""
import base64
import ctypes
import json
import os
import subprocess
import tempfile
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
UIA_PS1 = os.path.join(HERE, "uia.ps1")
LOCAL = os.environ["LOCALAPPDATA"]
APP_DIR = os.path.join(LOCAL, "Programs", "VideoDownloader")
EXE = os.path.join(APP_DIR, "VideoDownloader.exe")
DATA_DIR = os.path.join(LOCAL, "VideoDownloader")
SETTINGS = os.path.join(DATA_DIR, "settings.json")
TORRENT_LOG = os.path.join(DATA_DIR, "torrent.log")
TORRENT_DATA = os.path.join(DATA_DIR, "torrents")
PLAYERS = os.path.join(tempfile.gettempdir(), "vd-torrent-proto", "players")
VLC = os.path.join(PLAYERS, "vlc-3.0.23", "vlc.exe")
MPV = os.path.join(PLAYERS, "mpv", "mpv.exe")

user32 = ctypes.WinDLL("user32", use_last_error=True)
WM_CLOSE = 0x0010


# ------------------------------------------------------------ настройки

def read_settings():
    with open(SETTINGS, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def patch_settings(**changes):
    """Правка настроек установленной копии. Пишем БЕЗ BOM (AGENTS.md)."""
    data = read_settings()
    data.update(changes)
    tmp = SETTINGS + ".tmp-live"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS)
    return data


# ------------------------------------------------------------ процесс

def launch(*args, temp_dir=None):
    """Запустить установленную копию.

    temp_dir — подменить процессу TEMP/TMP. Нужно проверке «мало места»:
    временные раздачи живут под %TEMP%, и свободное место движок
    спрашивает у того тома, куда TEMP показывает.
    """
    env = None
    if temp_dir:
        env = dict(os.environ)
        env["TEMP"] = env["TMP"] = temp_dir
    return subprocess.Popen([EXE, *args], cwd=APP_DIR, env=env)


def windows_of(pid):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return found


def main_window(pid, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for hwnd, title in windows_of(pid):
            if "Video Downloader" in title:
                return hwnd
        time.sleep(0.2)
    return None


def rect_of(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def close_window(hwnd):
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send(vk=0, scan=0, flags=0):
    inp = _INPUT(type=1)
    inp.ki = _KEYBDINPUT(vk, scan, flags, 0, 0)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def focus(hwnd):
    """Вывести окно вперёд обходом anti-focus-stealing — тем же приёмом,
    что у второго экземпляра приложения (single_instance.force_foreground)."""
    import sys
    sys.path.insert(0, os.path.dirname(HERE))
    import single_instance
    single_instance.force_foreground(hwnd)
    time.sleep(0.5)
    return user32.GetForegroundWindow() == hwnd


def _require_foreground(hwnd):
    # Без этой проверки ввод уходит в чужое окно: 17.09.2026 путь и Enter
    # ушли в висящий запрос брандмауэра, который держал передний план
    if user32.GetForegroundWindow() != hwnd:
        raise RuntimeError("целевое окно не на переднем плане — ввод отменён")


def type_text(hwnd, text):
    """Ввод текста посимвольно как Unicode (кириллица в пути)."""
    _require_foreground(hwnd)
    for ch in text:
        _send(scan=ord(ch), flags=0x0004)            # KEYEVENTF_UNICODE
        _send(scan=ord(ch), flags=0x0004 | 0x0002)
        time.sleep(0.005)


def press(hwnd, vk):
    _require_foreground(hwnd)
    _send(vk=vk)
    _send(vk=vk, flags=0x0002)


def click(pid, x, y, double=False):
    """Настоящий клик мышью (SetCursorPos + mouse_event, как в живых
    проверках 1.1). Жмём только если под курсором окно НАШЕГО процесса —
    иначе клик ушёл бы в перекрывающее окно (запрос брандмауэра)."""
    point = wintypes.POINT(x, y)
    under = user32.WindowFromPoint(point)
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(under, ctypes.byref(owner))
    if owner.value != pid:
        raise RuntimeError(f"под точкой {x},{y} чужое окно (pid {owner.value})")
    user32.SetCursorPos(x, y)
    for _ in range(2 if double else 1):
        user32.mouse_event(0x0002, 0, 0, 0, 0)      # LEFTDOWN
        user32.mouse_event(0x0004, 0, 0, 0, 0)      # LEFTUP
        time.sleep(0.08)


def scroll(pid, x, y, clicks):
    """Колесо мыши над точкой (x, y): clicks < 0 — вниз, > 0 — вверх.

    Нужна страницам с прокруткой: в UI Automation элементы ниже видимой
    области имеют настоящие экранные координаты, которые лежат ЗА окном
    (страница «Настройки»: группа Torrent на y=1038-1326 при нижней
    границе окна 925), и клик по ним уходил мимо.
    """
    point = wintypes.POINT(x, y)
    under = user32.WindowFromPoint(point)
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(under, ctypes.byref(owner))
    if owner.value != pid:
        raise RuntimeError(f"под точкой {x},{y} чужое окно (pid {owner.value})")
    user32.SetCursorPos(x, y)
    for _ in range(abs(clicks)):
        user32.mouse_event(0x0800, 0, 0,                  # MOUSEEVENTF_WHEEL
                           120 if clicks > 0 else -120, 0)
        time.sleep(0.05)


def shot(hwnd, path):
    from PIL import ImageGrab
    try:
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.6)
    ImageGrab.grab(bbox=rect_of(hwnd), all_screens=True).save(path)
    return path


# ------------------------------------------------------------ UIA

def _b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def uia(pid, op, name="", value="", ctl_type="", nth=0):
    out = os.path.join(tempfile.gettempdir(), f"vd-uia-{os.getpid()}.json")
    if os.path.exists(out):
        os.remove(out)
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", UIA_PS1, "-ProcessId", str(pid), "-Op", op,
           "-Name", _b64(name), "-Value", _b64(value), "-Type", ctl_type,
           "-Nth", str(nth), "-Out", out]
    subprocess.run(cmd, capture_output=True, timeout=90)
    try:
        with open(out, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"no result: {exc}"}


def elements(pid):
    res = uia(pid, "dump")
    return res.get("items", []) if res.get("ok") else []


def names(pid):
    return [e["name"] for e in elements(pid) if e.get("name")]


def wait_element(pid, pred, timeout, interval=1.0):
    """Ждать элемент, для которого pred(element) истинно; вернуть его."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for e in elements(pid):
            if pred(e):
                return e
        time.sleep(interval)
    return None


def invoke(pid, name, nth=0, ctl_type=""):
    return uia(pid, "invoke", name=name, nth=nth, ctl_type=ctl_type)


def select(pid, name, nth=0, ctl_type="ControlType.TreeItem"):
    """Выбрать строку дерева: Invoke у Qt выбор не меняет."""
    return uia(pid, "select", name=name, nth=nth, ctl_type=ctl_type)


def invoke_async(pid, name, nth=0, ctl_type=""):
    """Нажать кнопку, которая открывает МОДАЛЬНЫЙ диалог: Qt выполняет
    нажатие внутри exec() диалога, и синхронный Invoke ждал бы, пока диалог
    не закроют."""
    out = os.path.join(tempfile.gettempdir(),
                       f"vd-uia-async-{os.getpid()}-{time.monotonic_ns()}.json")
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", UIA_PS1, "-ProcessId", str(pid), "-Op", "invoke",
           "-Name", _b64(name), "-Type", ctl_type, "-Nth", str(nth),
           "-Out", out]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def set_value(pid, value, name="", ctl_type="ControlType.Edit", nth=0):
    return uia(pid, "setvalue", name=name, value=value, ctl_type=ctl_type,
               nth=nth)


# ------------------------------------------------------------ следы

def listening_ports(pid):
    """(протокол, адрес) слушающих сокетов процесса — по netstat."""
    out = subprocess.run(["netstat", "-ano"], capture_output=True,
                         text=True, errors="replace").stdout
    ports = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or not parts[-1].isdigit() \
                or int(parts[-1]) != pid:
            continue
        proto = parts[0].upper()
        if proto == "TCP" and len(parts) >= 5 and "LISTEN" in parts[3]:
            ports.add(("TCP", parts[1]))
        elif proto == "UDP":
            ports.add(("UDP", parts[1]))
    return sorted(ports)


def processes(image):
    """[(pid, parent, cmdline)] по имени exe — через CIM, как в 1.0.5."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='%s'\" | "
          "ForEach-Object { '{0}|{1}|{2}' -f $_.ProcessId, "
          "$_.ParentProcessId, $_.CommandLine }" % image)
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True)
    text = out.stdout.decode("cp866", "replace")
    try:
        text = out.stdout.decode("utf-8")
    except UnicodeDecodeError:
        pass
    rows = []
    for line in text.splitlines():
        if line.count("|") >= 2:
            p, parent, cmd = line.split("|", 2)
            rows.append((int(p), int(parent), cmd.strip()))
    return rows


def kill(pid):
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                   capture_output=True)


def log_size():
    try:
        return os.path.getsize(TORRENT_LOG)
    except OSError:
        return 0


def log_since(offset):
    try:
        with open(TORRENT_LOG, "rb") as f:
            f.seek(offset)
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def last_boot_events(count=3):
    """Последние события загрузки системы (Kernel-Boot 27).

    Тем же журналом пользуется движок (torrent_engine.last_boot_time):
    BootType 0 — холодный старт, 1 — быстрый запуск («выключил и
    включил»), 2 — выход из гибернации (перезагрузкой не считается).
    Отдаём [{"time": unix, "boot_type": n}], новые первыми.
    """
    ps = ("Get-WinEvent -FilterHashtable @{LogName='System';"
          "ProviderName='Microsoft-Windows-Kernel-Boot';Id=27} "
          f"-MaxEvents {int(count)} | ForEach-Object "
          # ВНИМАНИЕ: -UFormat %s в Windows PowerShell 5.1 считает от
          # ЛОКАЛЬНОГО времени и завышает результат на смещение зоны
          # (здесь на 4 часа) — берём честный ToUnixTimeSeconds
          "{ '{0}|{1}' -f ([datetimeoffset]$_.TimeCreated)"
          ".ToUnixTimeSeconds(), $_.Properties[0].Value }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True, errors="replace")
    events = []
    for line in out.stdout.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 2 and parts[0].lstrip("-").isdigit():
            events.append({"time": int(parts[0]),
                           "boot_type": int(parts[1])
                           if parts[1].isdigit() else -1})
    return events


def firewall_rules():
    ps = ("Get-NetFirewallApplicationFilter | Where-Object { $_.Program "
          "-like '*VideoDownloader*' } | ForEach-Object { $r = $_ | "
          "Get-NetFirewallRule; '{0}|{1}|{2}|{3}' -f $r.Direction, "
          "$r.Action, $r.Profile, $_.Program }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True, errors="replace")
    return [line for line in out.stdout.splitlines() if line.strip()]
