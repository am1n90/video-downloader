"""Внешний плеер: поиск и запуск (торрент-стриминг, Этап 2, сессия 2.1).

Плеер НЕ встраивается и НЕ поставляется с программой — это решение №6
(mpv и VLC под GPL, запускаем отдельным процессом). Поэтому его нужно
найти на машине пользователя; путь можно задать вручную в Настройках.

Порядок поиска: путь из настроек -> PATH -> «App Paths» в реестре ->
обычные места установки. mpv раньше VLC: в замерах Этапа 0.2 у него
первый кадр 3.2-3.4 с против 3.9-6.7 с у VLC.

Только stdlib (правило проекта): shutil, subprocess, winreg.

С «Посмотреть во временную папку» плеер ещё и НАБЛЮДАЕТСЯ
(PlayerWatcher): где остановились и досмотрели ли до конца. mpv
спрашиваем по JSON IPC, VLC — через его http-интерфейс (status.json);
другой плеер из Настроек спросить нечем — у него знаем только выход
процесса.
"""

import base64
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request

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


def subtitle_args(exe, urls):
    """Ключи плеера для внешних субтитров (сессия 2.3).

    У mpv ключ повторяемый — сколько дорожек дали, столько и подключит.
    VLC берёт только ОДИН файл субтитров (--sub-file), поэтому отдаём
    первый; остальные пользователь при желании добавит сам. Незнакомому
    плееру не передаём ничего: чужие ключи скорее помешают открыть
    видео, чем помогут.
    """
    if not urls:
        return []
    label = label_for(exe)
    if label == "mpv":
        return [f"--sub-file={url}" for url in urls]
    if label == "VLC":
        return [f"--sub-file={urls[0]}"]
    return []


def launch(target, exe, subtitles=()):
    """Открыть URL или файл в найденном плеере (см. resolve).

    Процесс не ждём и не убиваем: плеер живёт своей жизнью, при закрытии
    окна программы он остаётся открытым (решение №6 — отдельный
    процесс), просто оборвётся соединение с нашим сервером.
    """
    return _popen([exe, target] + subtitle_args(exe, list(subtitles)))


def _popen(args):
    flags = _CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen(args, close_fds=True,
                            creationflags=flags,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL)


# ============ наблюдение за просмотром (временные раздачи) ============

KIND_MPV = "mpv"
KIND_VLC = "VLC"
KIND_OTHER = "other"

WATCHED_SHARE = 0.95        # досмотрел: титры и чёрный экран в конце не ждём
POLL_EVERY = 1.0            # с — как часто спрашиваем позицию


def player_kind(exe):
    label = label_for(exe)
    return label if label in (KIND_MPV, KIND_VLC) else KIND_OTHER


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class PlayerSession:
    """Запущенный под наблюдение плеер: процесс и как его спрашивать."""

    def __init__(self, proc, kind, pipe_name="", http_port=0, password=""):
        self.proc = proc
        self.kind = kind
        self.pipe_name = pipe_name      # mpv: \\.\pipe\<имя>
        self.http_port = http_port      # VLC: 127.0.0.1:<порт>/requests/…
        self.password = password


def watch_args(kind, start=None, pipe_name="", http_port=0, password=""):
    """Ключи наблюдения и начальной позиции для плеера.

    VLC: без --no-one-instance при включённом «только один экземпляр»
    наш процесс передаёт ссылку уже открытому окну и сразу выходит —
    конец просмотра не отследить. --play-and-exit — чтобы VLC, как mpv
    по умолчанию, закрывался в конце файла: «досмотрел» наступает сразу,
    а не когда пользователь вспомнит закрыть окно. Позицию VLC берёт
    ключом --start-time, у mpv — --start.
    """
    start = None if start is None else max(0, int(start))
    if kind == KIND_MPV:
        args = [f"--input-ipc-server={pipe_name}"]
        if start:
            args.append(f"--start={start}")
        return args
    if kind == KIND_VLC:
        args = ["--no-one-instance",
                "--no-one-instance-when-started-from-file",
                "--play-and-exit",
                "--extraintf=http", "--http-host=127.0.0.1",
                f"--http-port={http_port}", f"--http-password={password}"]
        if start:
            args.append(f"--start-time={start}")
        return args
    return []                   # незнакомому плееру чужих ключей не даём


def launch_watched(target, exe, subtitles=(), start=None):
    """Запустить плеер так, чтобы за просмотром можно было следить."""
    kind = player_kind(exe)
    token = secrets.token_hex(8)
    pipe_name = f"vd-mpv-{token}" if kind == KIND_MPV else ""
    http_port = _free_port() if kind == KIND_VLC else 0
    args = ([exe, target] + subtitle_args(exe, list(subtitles))
            + watch_args(kind, start, pipe_name, http_port, token))
    return PlayerSession(_popen(args), kind, pipe_name, http_port, token)


class MpvIPC:
    """Клиент JSON IPC mpv поверх именованного канала Windows.

    Строго «запрос — ответ» В ОДНОЙ НИТИ и без фонового читателя
    (находка 39): у синхронного дескриптора канала блокирующий ReadFile
    задерживает WriteFile по тому же дескриптору, и клиент с фоновым
    читателем вставал намертво, как только mpv переставал слать события.
    События читаем только попутно — ради end-file.
    """

    def __init__(self, pipe_name):
        self.path = rf"\\.\pipe\{pipe_name}"
        self.pipe = None
        self._buf = b""
        self.dead = False
        self.end_reason = ""            # reason последнего end-file

    def try_connect(self):
        try:
            self.pipe = open(self.path, "r+b", buffering=0)
            return True
        except OSError:                 # mpv ещё не создал канал
            return False

    def _read_line(self):
        while b"\n" not in self._buf:
            try:
                chunk = self.pipe.read(4096)
            except Exception:
                chunk = b""
            if not chunk:               # mpv закрыл канал
                self.dead = True
                return None
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line.strip()

    def get(self, prop, tries=40):
        """Значение свойства mpv или None."""
        if self.dead or self.pipe is None:
            return None
        payload = json.dumps({"command": ["get_property", prop],
                              "request_id": 1}) + "\n"
        try:
            self.pipe.write(payload.encode("utf-8"))
        except Exception:
            self.dead = True
            return None
        for _ in range(tries):
            line = self._read_line()
            if line is None:
                return None
            try:
                msg = json.loads(line.decode("utf-8", "replace")) if line else {}
            except ValueError:
                continue
            if msg.get("event"):
                if msg["event"] == "end-file":
                    self.end_reason = str(msg.get("reason") or "")
                continue
            if "error" not in msg:
                continue
            return msg.get("data") if msg.get("error") == "success" else None
        return None

    def close(self):
        try:
            if self.pipe is not None:
                self.pipe.close()
        except Exception:
            pass


def _vlc_status(port, password, timeout=1.0):
    """status.json http-интерфейса VLC или None (не ответил)."""
    auth = base64.b64encode(f":{password}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/requests/status.json",
        headers={"Authorization": f"Basic {auth}"})
    # Прокси из системных настроек к 127.0.0.1 не нужен
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


def is_watched(position, duration, share=WATCHED_SHARE):
    return bool(position is not None and duration
                and duration > 0 and position >= share * duration)


class PlayerWatcher:
    """Следит за одним сеансом просмотра в своём потоке.

    Раз в POLL_EVERY секунд запоминает позицию и длительность; когда
    процесс плеера завершился, один раз зовёт
    on_end(watcher) — из СВОЕГО потока (GUI переправляет сигналом).
    Итог — в полях: eof (досмотрел), position, duration, reached (плеер
    хоть раз ответил на опрос).

    Досмотрел = последняя известная позиция >= 95% длительности. Другой
    плеер спросить нечем — у него eof всегда False.
    """

    def __init__(self, session, on_end, poll_every=POLL_EVERY,
                 context=None):
        self.session = session
        self.on_end = on_end
        self.poll_every = poll_every
        self.context = context          # (tid, index) — для вызывающего
        self.position = None
        self.duration = None
        self.eof = False
        self.reached = False
        self.end_reason = ""
        self.started = time.monotonic()
        self.ended = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="player-watcher")

    def start(self):
        self._thread.start()
        return self

    def cancel(self):
        """Перестать следить без on_end (закрытие окна программы)."""
        self._stop.set()

    def join(self, timeout=None):
        self._thread.join(timeout)

    @property
    def running(self):
        return self._thread.is_alive()

    def _remember(self, position, duration):
        try:
            if position is not None:
                self.position = float(position)
            if duration:
                self.duration = float(duration)
        except (TypeError, ValueError):
            pass

    def _run(self):
        proc = self.session.proc
        kind = self.session.kind
        mpv = MpvIPC(self.session.pipe_name) if kind == KIND_MPV else None
        try:
            while not self._stop.is_set() and proc.poll() is None:
                if kind == KIND_MPV:
                    self._poll_mpv(mpv)
                elif kind == KIND_VLC:
                    self._poll_vlc()
                self._stop.wait(self.poll_every)
        finally:
            if mpv is not None:
                # Канал только закрываем. Дочитывать его «на всякий случай»
                # нельзя: если другая сторона жива и молчит, ReadFile встаёт
                # навсегда (та же природа, что находка 39)
                mpv.close()
        if self._stop.is_set():
            return
        self.ended = time.monotonic()
        # end-file reason=eof сам по себе не доказательство: оборванный
        # поток (закрыли программу, сняли раздачу) mpv тоже может счесть
        # концом файла — а по ошибке удалить недосмотренное нельзя.
        # Решает позиция; end_reason остаётся для журнала
        self.end_reason = mpv.end_reason if mpv is not None else ""
        self.eof = kind != KIND_OTHER and is_watched(self.position,
                                                     self.duration)
        try:
            self.on_end(self)
        except Exception:
            pass

    def _poll_mpv(self, mpv):
        if mpv.pipe is None and not mpv.try_connect():
            return
        if mpv.dead:
            return
        position = mpv.get("time-pos")
        duration = mpv.get("duration")
        if position is not None or duration is not None:
            self.reached = True
        self._remember(position, duration)

    def _poll_vlc(self):
        status = _vlc_status(self.session.http_port, self.session.password)
        if status is None:
            return
        self.reached = True
        # В конце списка VLC сбрасывает time/length в 0 — такие снимки
        # позицию не затирают
        length = status.get("length") or 0
        if length > 0:
            self._remember(status.get("time"), length)

