"""Защита от второго экземпляра (Windows, только stdlib).

Именованный мьютекс (ctypes/kernel32) определяет, что экземпляр уже
запущен: мьютекс освобождается ОС автоматически при завершении
процесса, включая аварийное — надёжнее lock-файла с PID.

Активация окна первого экземпляра — через именованный канал (named
pipe, тоже ctypes/kernel32): второй процесс шлёт короткую команду
"SHOW" и завершается. Таймаут на подключение защищает от зависшего
первого экземпляра (WaitNamedPipeW не блокирует бесконечно).
"""

import ctypes
from ctypes import wintypes
import threading

kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32

MUTEX_NAME = "Local\\VideoDownloader-SingleInstance-Mutex"
PIPE_NAME = r"\\.\pipe\VideoDownloader-SingleInstance-Pipe"
SHOW_MESSAGE = b"SHOW"

ERROR_ALREADY_EXISTS = 183
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

PIPE_ACCESS_DUPLEX = 0x00000003
PIPE_TYPE_MESSAGE = 0x00000004
PIPE_READMODE_MESSAGE = 0x00000002
PIPE_WAIT = 0x00000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
PIPE_BUFFER_SIZE = 64

kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.GetLastError.restype = wintypes.DWORD

kernel32.CreateNamedPipeW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
]
kernel32.CreateNamedPipeW.restype = wintypes.HANDLE

kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
kernel32.ConnectNamedPipe.restype = wintypes.BOOL

kernel32.ReadFile.argtypes = [
    wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
]
kernel32.ReadFile.restype = wintypes.BOOL

kernel32.WriteFile.argtypes = [
    wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
]
kernel32.WriteFile.restype = wintypes.BOOL

kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
kernel32.CreateFileW.restype = wintypes.HANDLE

kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
kernel32.WaitNamedPipeW.restype = wintypes.BOOL

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

kernel32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
kernel32.DisconnectNamedPipe.restype = wintypes.BOOL

kernel32.GetCurrentThreadId.restype = wintypes.DWORD

user32.GetForegroundWindow.restype = wintypes.HWND

user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, wintypes.LPVOID]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL

user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL

user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL

user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL

SW_RESTORE = 9


def acquire_mutex():
    """Создаёт (или открывает) именованный мьютекс.

    Возвращает (handle, already_running). Хендл нужно держать живым
    (не сборщиком мусора) весь срок жизни процесса — иначе мьютекс
    освободится досрочно.
    """
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    already_running = handle and kernel32.GetLastError() == ERROR_ALREADY_EXISTS
    return handle, bool(already_running)


def start_pipe_server(on_show):
    """Запускает фоновый поток-сервер именованного канала.

    on_show вызывается из фонового потока при получении команды
    SHOW — вызывающий код сам обязан безопасно передать это в
    GUI-поток (например через Qt Signal, испускание которого
    потокобезопасно).
    """

    def _serve():
        while True:
            handle = kernel32.CreateNamedPipeW(
                PIPE_NAME,
                PIPE_ACCESS_DUPLEX,
                PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
                1,
                PIPE_BUFFER_SIZE,
                PIPE_BUFFER_SIZE,
                0,
                None,
            )
            if handle == INVALID_HANDLE_VALUE:
                return

            connected = kernel32.ConnectNamedPipe(handle, None)
            if not connected and kernel32.GetLastError() != 535:  # ERROR_PIPE_CONNECTED
                kernel32.CloseHandle(handle)
                continue

            buf = ctypes.create_string_buffer(PIPE_BUFFER_SIZE)
            bytes_read = wintypes.DWORD(0)
            ok = kernel32.ReadFile(
                handle, buf, PIPE_BUFFER_SIZE, ctypes.byref(bytes_read), None
            )
            if ok and buf.raw[: bytes_read.value] == SHOW_MESSAGE:
                try:
                    on_show()
                except Exception:
                    pass

            kernel32.DisconnectNamedPipe(handle)
            kernel32.CloseHandle(handle)

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    return thread


def send_show_request(timeout_ms=1500):
    """Просит уже запущенный экземпляр показать своё окно.

    Возвращает True, если команда доставлена. False — если первый
    экземпляр не отвечает за timeout_ms (например, завис): в этом
    случае вызывающий должен показать своё собственное окно, а не
    просто завершиться молча.
    """
    try:
        if not kernel32.WaitNamedPipeW(PIPE_NAME, timeout_ms):
            return False

        handle = kernel32.CreateFileW(
            PIPE_NAME, GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None
        )
        if handle == INVALID_HANDLE_VALUE:
            return False

        written = wintypes.DWORD(0)
        kernel32.WriteFile(
            handle, SHOW_MESSAGE, len(SHOW_MESSAGE), ctypes.byref(written), None
        )
        kernel32.CloseHandle(handle)
        return True
    except Exception:
        return False


def force_foreground(hwnd):
    """Выводит окно hwnd поверх остальных, обходя защиту Windows от
    "кражи фокуса" (SetForegroundWindow из фонового потока другого
    процесса обычно молча игнорируется системой).

    Приём — временно присоединить очередь ввода текущего потока к
    потоку окна, которое сейчас в фокусе (AttachThreadInput):
    пока они присоединены, SetForegroundWindow разрешён. Стандартный
    обход, используемый многими приложениями с "поднять окно по
    сигналу от второго экземпляра".
    """
    if not hwnd:
        return

    foreground_hwnd = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground_hwnd, None)
    current_thread = kernel32.GetCurrentThreadId()

    attached = False
    if foreground_thread and foreground_thread != current_thread:
        attached = bool(user32.AttachThreadInput(foreground_thread, current_thread, True))

    try:
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(foreground_thread, current_thread, False)
