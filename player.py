"""Внешний плеер: поиск и запуск (торрент-стриминг, Этап 2, сессия 2.1).

Плеер НЕ встраивается и НЕ поставляется с программой — это решение №6
(mpv и VLC под GPL, запускаем отдельным процессом). Поэтому его нужно
найти на машине пользователя; путь можно задать вручную в Настройках.

Порядок поиска: путь из настроек -> PATH -> «App Paths» в реестре ->
обычные места установки. mpv раньше VLC: в замерах Этапа 0.2 у него
первый кадр 3.2-3.4 с против 3.9-6.7 с у VLC.

Только stdlib (правило проекта): shutil, subprocess, winreg.
"""

import os
import shutil
import subprocess
import sys

try:
    import winreg
except ImportError:                     # не Windows — поиска по реестру нет
    winreg = None

# Порядок важен: первым найденным и запустим
KNOWN_PLAYERS = (
    ("mpv", "mpv.exe"),
    ("VLC", "vlc.exe"),
)

_APP_PATHS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
_CREATE_NO_WINDOW = 0x08000000


class PlayerNotFound(Exception):
    """Плеер не найден и не задан в настройках."""


def _program_dirs():
    names = ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")
    dirs = [os.environ[name] for name in names if os.environ.get(name)]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(os.path.join(local, "Programs"))
    home = os.path.expanduser("~")
    dirs.append(os.path.join(home, "scoop", "apps"))
    return dirs


def _candidates(exe):
    """Обычные места установки — по одному шаблону на плеер."""
    stem = os.path.splitext(exe)[0]
    out = []
    for base in _program_dirs():
        out.append(os.path.join(base, stem, exe))
        out.append(os.path.join(base, stem, "current", exe))     # scoop
        if exe == "vlc.exe":
            out.append(os.path.join(base, "VideoLAN", "VLC", exe))
    return out


def _from_registry(exe):
    """«App Paths» — туда пишутся и VLC, и многие сборки mpv."""
    if winreg is None:
        return ""
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (0, getattr(winreg, "KEY_WOW64_32KEY", 0)):
            try:
                with winreg.OpenKey(root, _APP_PATHS + "\\" + exe, 0,
                                    winreg.KEY_READ | view) as key:
                    path = winreg.QueryValueEx(key, "")[0]
            except OSError:
                continue
            path = os.path.expandvars(str(path).strip('"'))
            if path and os.path.isfile(path):
                return path
    return ""


def find_player(exe):
    """Путь к конкретному плееру (mpv.exe / vlc.exe) или ""."""
    found = shutil.which(exe)
    if found:
        return found
    found = _from_registry(exe)
    if found:
        return found
    for path in _candidates(exe):
        if os.path.isfile(path):
            return path
    return ""


def available():
    """[(название, путь)] — все найденные плееры, в порядке предпочтения."""
    out = []
    for label, exe in KNOWN_PLAYERS:
        path = find_player(exe)
        if path:
            out.append((label, path))
    return out


def resolve(configured=""):
    """Чем открывать: путь из настроек, если он есть, иначе найденный.

    Путь из настроек не молчим-игнорируем, если он пропал: пользователь
    должен увидеть, что указанного плеера нет, а не гадать, почему
    открылось не то (кидаем PlayerNotFound с этим путём в тексте).
    """
    configured = (configured or "").strip().strip('"')
    if configured:
        if os.path.isfile(configured):
            return configured
        raise PlayerNotFound(f"указанный плеер не найден: {configured}")
    found = available()
    if not found:
        raise PlayerNotFound("на компьютере не найдены mpv или VLC")
    return found[0][1]


def label_for(path):
    name = os.path.splitext(os.path.basename(path or ""))[0].lower()
    for label, exe in KNOWN_PLAYERS:
        if name == os.path.splitext(exe)[0]:
            return label
    return os.path.basename(path or "")


def launch(target, exe):
    """Открыть URL или файл в найденном плеере (см. resolve).

    Процесс не ждём и не убиваем: плеер живёт своей жизнью, при закрытии
    окна программы он остаётся открытым (решение №6 — отдельный
    процесс), просто оборвётся соединение с нашим сервером.
    """
    flags = _CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen([exe, target], close_fds=True,
                            creationflags=flags,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL)
