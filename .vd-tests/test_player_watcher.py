# -*- coding: utf-8 -*-
"""Офлайн-тест наблюдения за плеером (player.PlayerWatcher).

Настоящие mpv и VLC тут не нужны и не запускаются: вместо mpv — свой
именованный канал, отвечающий на get_property, вместо VLC — свой
http-сервер со status.json. «Плеер» — обычный процесс python, который
спит, пока мы его не закроем: наблюдатель следит именно за выходом
процесса.

Сеть — только 127.0.0.1.
"""
import base64
import ctypes
import ctypes.wintypes as wt
import http.server
import json
import os
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import player

PASS, FAIL = [], []


def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details, flush=True)


def sleeper():
    """«Плеер»: процесс, который просто живёт, пока его не закроют."""
    return subprocess.Popen([sys.executable, "-c", "import time;time.sleep(120)"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def wait_for(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


# ---------------------------------------------------------------- mpv

kernel32 = ctypes.windll.kernel32
PIPE_ACCESS_DUPLEX = 0x00000003
PIPE_TYPE_BYTE = 0x00000000
PIPE_WAIT = 0x00000000
INVALID_HANDLE = wt.HANDLE(-1).value


class FakeMpv(threading.Thread):
    """Канал mpv: отвечает на get_property тем, что скажет сценарий.

    Отвечает строго по одному ответу на команду — так же, как настоящий
    mpv, и так же, как читает наш клиент (находка 39: без фонового
    читателя).
    """

    def __init__(self, pipe_name, positions, duration=100.0, events=()):
        super().__init__(daemon=True)
        self.path = rf"\\.\pipe\{pipe_name}"
        self.positions = list(positions)
        self.duration = duration
        self.events = list(events)
        self.asked = []
        self.stop = threading.Event()
        self.handle = kernel32.CreateNamedPipeW(
            self.path, PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_BYTE | PIPE_WAIT, 4, 65536, 65536, 0, None)
        if self.handle == INVALID_HANDLE:
            raise OSError("не создан канал " + self.path)

    def _send(self, obj):
        data = (json.dumps(obj) + "\n").encode("utf-8")
        written = wt.DWORD(0)
        kernel32.WriteFile(wt.HANDLE(self.handle), data, len(data),
                           ctypes.byref(written), None)

    def run(self):
        kernel32.ConnectNamedPipe(wt.HANDLE(self.handle), None)
        buf = ctypes.create_string_buffer(4096)
        read = wt.DWORD(0)
        rest = b""
        while not self.stop.is_set():
            ok = kernel32.ReadFile(wt.HANDLE(self.handle), buf, 4096,
                                   ctypes.byref(read), None)
            if not ok or not read.value:
                return
            rest += buf.raw[:read.value]
            while b"\n" in rest:
                line, _, rest = rest.partition(b"\n")
                try:
                    msg = json.loads(line.decode("utf-8"))
                except ValueError:
                    continue
                prop = msg.get("command", ["", ""])[1]
                self.asked.append(prop)
                for event in self.events:
                    self._send(event)
                self.events = []
                if prop == "duration":
                    value = self.duration
                elif self.positions:
                    value = self.positions.pop(0) if len(self.positions) > 1 \
                        else self.positions[0]
                else:
                    value = None
                self._send({"data": value, "error": "success",
                            "request_id": msg.get("request_id", 1)})

    def close(self):
        self.stop.set()
        try:
            kernel32.DisconnectNamedPipe(wt.HANDLE(self.handle))
            kernel32.CloseHandle(wt.HANDLE(self.handle))
        except Exception:
            pass


# ---------------------------------------------------------------- VLC

class FakeVlc:
    """http-интерфейс VLC: только /requests/status.json и basic auth."""

    def __init__(self, password, snapshots):
        self.password = password
        self.snapshots = list(snapshots)
        self.requests = 0
        self.bad_auth = 0
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                want = "Basic " + base64.b64encode(
                    f":{outer.password}".encode()).decode()
                if self.headers.get("Authorization") != want:
                    outer.bad_auth += 1
                    self.send_response(401)
                    self.end_headers()
                    return
                if self.path != "/requests/status.json":
                    self.send_response(404)
                    self.end_headers()
                    return
                outer.requests += 1
                snap = outer.snapshots[0] if len(outer.snapshots) == 1 \
                    else outer.snapshots.pop(0)
                body = json.dumps(snap).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


# ------------------------------------------------------- сбор итогов

class Ended:
    def __init__(self):
        self.calls = []
        self.event = threading.Event()

    def __call__(self, watcher):
        self.calls.append(watcher)
        self.event.set()


def run_watch(session, poll=0.05, close_after=0.4, cancel=False):
    ended = Ended()
    watcher = player.PlayerWatcher(session, ended, poll_every=poll).start()
    time.sleep(close_after)
    if cancel:
        watcher.cancel()
    session.proc.terminate()
    ended.event.wait(5)
    watcher.join(3)
    return watcher, ended


# ---- 1: ключи командной строки ----
mpv_args = player.watch_args(player.KIND_MPV, start=130, pipe_name="vd-mpv-x")
check("1 mpv: канал IPC и позиция --start",
      mpv_args == ["--input-ipc-server=vd-mpv-x", "--start=130"],
      str(mpv_args))
vlc_args = player.watch_args(player.KIND_VLC, start=130, http_port=8123,
                             password="tok")
check("1 VLC: позиция — --start-time (ключа --start у VLC нет)",
      "--start-time=130" in vlc_args and not any(
          a.startswith("--start=") for a in vlc_args), str(vlc_args))
check("1 VLC: один экземпляр выключен, иначе конец просмотра не отследить",
      "--no-one-instance" in vlc_args
      and "--no-one-instance-when-started-from-file" in vlc_args)
check("1 VLC: http-интерфейс с паролем на 127.0.0.1",
      "--extraintf=http" in vlc_args and "--http-host=127.0.0.1" in vlc_args
      and "--http-port=8123" in vlc_args and "--http-password=tok" in vlc_args,
      str(vlc_args))
check("1 без позиции ключа начала нет",
      not any("start" in a for a in
              player.watch_args(player.KIND_MPV, None, pipe_name="p")))
check("1 другому плееру ключей не передаём",
      player.watch_args(player.KIND_OTHER, start=10) == [])
check("1 плеер определяется по имени файла",
      player.player_kind(r"C:\mpv\mpv.exe") == player.KIND_MPV
      and player.player_kind(r"C:\VideoLAN\VLC\vlc.exe") == player.KIND_VLC
      and player.player_kind(r"C:\Prog\mpc-hc64.exe") == player.KIND_OTHER)

# ---- 2: правило «досмотрел» ----
check("2 досмотрел: 95% длительности — да, 94% — нет",
      player.is_watched(95.0, 100.0) and not player.is_watched(94.0, 100.0))
check("2 без длительности досмотра не бывает",
      not player.is_watched(50.0, 0) and not player.is_watched(None, 100.0))

# ---- 3: mpv — закрыли на середине ----
name3 = "vd-test-mpv-3"
fake3 = FakeMpv(name3, positions=[10.0, 20.0, 30.0])
fake3.start()
proc3 = sleeper()
session3 = player.PlayerSession(proc3, player.KIND_MPV, pipe_name=name3)
w3, e3 = run_watch(session3)
fake3.close()
check("3 mpv: закрыли на середине — не досмотрел, позиция сохранена",
      len(e3.calls) == 1 and not w3.eof and w3.position == 30.0
      and w3.duration == 100.0,
      f"eof={w3.eof} позиция={w3.position} длительность={w3.duration}")
check("3 mpv: спрашиваем именно позицию и длительность",
      set(fake3.asked) == {"time-pos", "duration"}, str(set(fake3.asked)))
check("3 mpv: плеер отозвался (reached)", w3.reached)

# ---- 4: mpv — досмотрел до конца ----
name4 = "vd-test-mpv-4"
fake4 = FakeMpv(name4, positions=[50.0, 96.0],
                events=[{"event": "end-file", "reason": "eof"}])
fake4.start()
session4 = player.PlayerSession(sleeper(), player.KIND_MPV, pipe_name=name4)
w4, e4 = run_watch(session4)
fake4.close()
check("4 mpv: позиция за 95% — досмотрел",
      len(e4.calls) == 1 and w4.eof and w4.position == 96.0,
      f"eof={w4.eof} позиция={w4.position}")

# ---- 5: mpv — оборванный поток не считается досмотром ----
# mpv и на разорванном соединении присылает end-file reason=eof, поэтому
# решает ПОЗИЦИЯ: по ошибке удалить недосмотренное нельзя
name5 = "vd-test-mpv-5"
fake5 = FakeMpv(name5, positions=[12.0],
                events=[{"event": "end-file", "reason": "eof"}])
fake5.start()
session5 = player.PlayerSession(sleeper(), player.KIND_MPV, pipe_name=name5)
w5, e5 = run_watch(session5)
fake5.close()
check("5 mpv: end-file reason=eof в начале файла — НЕ досмотрел",
      len(e5.calls) == 1 and not w5.eof and w5.position == 12.0,
      f"eof={w5.eof} позиция={w5.position} reason={w5.end_reason}")

# ---- 6: VLC — status.json ----
vlc6 = FakeVlc("tok6", [{"state": "playing", "time": 40, "length": 100},
                        {"state": "playing", "time": 97, "length": 100},
                        {"state": "stopped", "time": 0, "length": 0}])
session6 = player.PlayerSession(sleeper(), player.KIND_VLC,
                                http_port=vlc6.port, password="tok6")
w6, e6 = run_watch(session6, poll=0.05, close_after=0.6)
check("6 VLC: позиция из status.json, досмотрел",
      len(e6.calls) == 1 and w6.eof and w6.position == 97.0
      and w6.duration == 100.0,
      f"eof={w6.eof} позиция={w6.position} запросов={vlc6.requests}")
check("6 VLC: пустой снимок в конце списка не затирает позицию",
      w6.position == 97.0 and vlc6.requests >= 3, str(vlc6.requests))
check("6 VLC: обращаемся с паролем", vlc6.bad_auth == 0, str(vlc6.bad_auth))
vlc6.close()

vlc7 = FakeVlc("правильный", [{"state": "playing", "time": 99, "length": 100}])
session7 = player.PlayerSession(sleeper(), player.KIND_VLC,
                                http_port=vlc7.port, password="чужой")
w7, e7 = run_watch(session7)
check("7 VLC: с чужим паролем позиции нет — просмотр не досмотрен",
      len(e7.calls) == 1 and not w7.eof and w7.position is None
      and not w7.reached, f"{w7.position}, отказов {vlc7.bad_auth}")
vlc7.close()

# ---- 8: другой плеер ----
session8 = player.PlayerSession(sleeper(), player.KIND_OTHER)
w8, e8 = run_watch(session8)
check("8 другой плеер: спросить нечем — всегда «не досмотрел»",
      len(e8.calls) == 1 and not w8.eof and w8.position is None
      and not w8.reached)

# ---- 9: отмена наблюдения (закрытие окна программы) ----
name9 = "vd-test-mpv-9"
fake9 = FakeMpv(name9, positions=[33.0])
fake9.start()
session9 = player.PlayerSession(sleeper(), player.KIND_MPV, pipe_name=name9)
ended9 = Ended()
w9 = player.PlayerWatcher(session9, ended9, poll_every=0.05).start()
wait_for(lambda: w9.position == 33.0, 3)
w9.cancel()
w9.join(3)
check("9 отмена: on_end не зовётся, последняя позиция известна",
      not ended9.calls and w9.position == 33.0 and not w9.running,
      f"{ended9.calls}, {w9.position}")
session9.proc.terminate()
fake9.close()

# ---- 10: mpv не создал канал (плеер упал сразу) ----
session10 = player.PlayerSession(sleeper(), player.KIND_MPV,
                                 pipe_name="vd-test-mpv-нет-такого")
w10, e10 = run_watch(session10, close_after=0.3)
check("10 канала mpv нет — наблюдатель не виснет и сообщает «не досмотрел»",
      len(e10.calls) == 1 and not w10.eof and not w10.reached)

print()
print(f"ИТОГ: {len(PASS)} PASS, {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
