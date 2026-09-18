"""Запуск приложения ИЗ ИСХОДНИКОВ с трассировкой закрытия.

Кода приложения не меняет: оборачивает методы, которые зовёт closeEvent,
и пишет в лог абсолютное время (time.time()) входа и выхода каждого.
Нужно, чтобы увидеть, что именно занимает секунду между «нажал закрыть»
и «процесс исчез» — снаружи видны только mtime файлов и выход процесса.

Лог: %TEMP%\\vd-close-diag\\trace-<pid>.log, строка на событие:
    <время> <событие> [<секунд>]

Запуск (обычно его запускает diag_close_time.py в режиме trace):
    build-venv\\Scripts\\python.exe .vd-tests\\run_app_traced.py
"""
import atexit
import gc
import os
import sys
import tempfile
import threading
import time

_GC_EVENTS = []
gc.callbacks.append(
    lambda phase, info: _GC_EVENTS.append((phase, info.get("generation"))))

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

LOG = os.path.join(tempfile.gettempdir(), "vd-close-diag",
                   f"trace-{os.getpid()}.log")
os.makedirs(os.path.dirname(LOG), exist_ok=True)
_f = open(LOG, "w", encoding="utf-8", buffering=1)


def note(event, seconds=None):
    _f.write(f"{time.time():.4f} {event}"
             + (f" {seconds:.3f}" if seconds is not None else "") + "\n")


def wrap(owner, name, label):
    """Обернуть метод замером, сохранив поведение."""
    original = getattr(owner, name)

    def wrapper(*args, **kwargs):
        note(f"{label}:начало")
        t0 = time.monotonic()
        try:
            return original(*args, **kwargs)
        finally:
            note(f"{label}:конец", time.monotonic() - t0)

    setattr(owner, name, wrapper)


import config                                                # noqa: E402
import gui                                                   # noqa: E402
import gui_torrent                                           # noqa: E402
import torrent_engine                                        # noqa: E402
import torrent_stream                                        # noqa: E402
import win_conns                                             # noqa: E402
from PySide6.QtCore import QThread                           # noqa: E402

# Опыты A/B над настройками сессии, без правки кода приложения:
#   set VD_TRACE_SETTINGS={"enable_dht": false}
_EXTRA = os.environ.get("VD_TRACE_SETTINGS")
if _EXTRA:
    import json

    _extra = json.loads(_EXTRA)
    _orig_init = torrent_engine.TorrentEngine.__init__

    def _patched_init(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        self._settings.update(_extra)

    torrent_engine.TorrentEngine.__init__ = _patched_init
    note(f"настройки сессии изменены: {_extra}")


INTERESTING = ("dht.dht_nodes", "peer.num_peers_connected",
               "peer.num_peers_half_open", "net.has_incoming_connections",
               "disk.num_blocks_written", "disk.queued_write_bytes",
               "disk.disk_blocks_in_use", "ses.num_outgoing_connections")


def session_facts(ses):
    """Счётчики сессии перед разрушением: чем медленный прогон отличается
    от быстрого. Поток алертов уже остановлен, поэтому статистику
    спрашиваем и забираем сами."""
    try:
        ses.post_session_stats()
        end = time.monotonic() + 1.0
        while time.monotonic() < end:
            ses.wait_for_alert(100)
            for alert in ses.pop_alerts():
                if type(alert).__name__ != "session_stats_alert":
                    continue
                values = alert.values
                parts = [f"{n}={values[n]}" for n in INTERESTING
                         if n in values]
                parts.append(f"dht={ses.is_dht_running()}")
                parts.append(f"port={ses.listen_port()}")
                for handle in ses.get_torrents():
                    for info in handle.get_peer_info():
                        parts.append(f"пир {info.ip[0]}:{info.ip[1]}"
                                     f"/{info.connection_type}"
                                     f"/флаги{int(info.flags)}")
                return " ".join(parts)
    except Exception as exc:
        return f"не собрано: {exc!r}"
    return "статистика не пришла"


class ConnSampler(threading.Thread):
    """Кто из сокетов доживает до конца `del ses` — по снимкам раз в 20 мс."""

    def __init__(self, interval=0.02):
        super().__init__(daemon=True)
        self.interval = interval
        self.stop = threading.Event()
        self.seen = {}               # сокет -> (впервые, последний раз)
        self.t0 = time.monotonic()

    def run(self):
        while not self.stop.is_set():
            now = time.monotonic() - self.t0
            try:
                current = win_conns.snapshot(os.getpid())
            except Exception:
                return
            for item in current:
                first, _last = self.seen.get(item, (now, now))
                self.seen[item] = (first, now)
            self.stop.wait(self.interval)

    def report(self):
        self.stop.set()
        self.join(1.0)
        if not self.seen:
            return ["снимков нет"]
        end = max(last for _f, last in self.seen.values())
        lines = []
        for item, (first, last) in sorted(self.seen.items(),
                                          key=lambda kv: -kv[1][1]):
            mark = " <- дожил до конца" if last >= end - 0.05 else ""
            lines.append(f"{item}: {first:.2f}…{last:.2f}{mark}")
        return lines[:12]


def traced_shutdown(self, timeout=3.0):
    """Тот же torrent_engine.shutdown(), но с отметкой после каждого шага.

    Повторяет код движка дословно (сверено с torrent_engine.py на
    18.09.2026); нужен потому, что снаружи видно только общее время
    shutdown, а оно оказалось двумодальным: 0.28 или 0.98 с.
    """
    lt = torrent_engine.lt
    t0 = time.monotonic()
    deadline = t0 + timeout
    # VD_TRACE_EARLY=noutp — выключить uTP В НАЧАЛЕ закрытия: хватит ли
    # этого, чтобы уже открытые uTP-соединения не задержали деструктор
    if os.environ.get("VD_TRACE_EARLY") == "noutp":
        with self._lock:
            if self._ses is not None:
                self._ses.apply_settings({"enable_incoming_utp": False,
                                          "enable_outgoing_utp": False})
        note("  шаг выключение uTP")
    self._close_all_streams()
    note("  шаг streams", time.monotonic() - t0)
    with self._lock:
        ses = self._ses
        if ses is None:
            return {"saved": 0, "unsaved": 0, "unflushed": 0, "seconds": 0.0}
        t = time.monotonic()
        ses.pause()
        note("  шаг pause", time.monotonic() - t)
        handles = [(tid, h) for tid, h in self._handles.items() if h.is_valid()]
        t = time.monotonic()
        for tid, handle in handles:
            if not handle.status().has_metadata:
                continue
            self._pending_flush.add(tid)
            handle.flush_cache()
        while self._pending_flush and time.monotonic() < deadline:
            self._saves_done.wait(deadline - time.monotonic())
        unflushed = len(self._pending_flush)
        self._pending_flush.clear()
        note(f"  шаг flush (не сброшено {unflushed})", time.monotonic() - t)
        t = time.monotonic()
        for tid, handle in handles:
            self._pending_saves.add(tid)
            handle.save_resume_data(lt.save_resume_flags_t.save_info_dict)
        asked = len(self._pending_saves)
        while self._pending_saves and time.monotonic() < deadline:
            self._saves_done.wait(deadline - time.monotonic())
        unsaved = len(self._pending_saves)
        self._pending_saves.clear()
        note(f"  шаг save (запрошено {asked}, не сохранено {unsaved})",
             time.monotonic() - t)
    t = time.monotonic()
    self._stop.set()
    # VD_TRACE_NOJOIN=1 — не ждать поток алертов. Это и способ
    # воспроизвести медленный случай: 0.25 с ожидания как раз и дают
    # пирам время отключиться, а без них разрушение сессии быстрое
    if self._thread is not None and not os.environ.get("VD_TRACE_NOJOIN"):
        self._thread.join(2.0)
        note(f"  шаг join (поток жив: {self._thread.is_alive()})",
             time.monotonic() - t)
    with self._lock:
        self.on_change = self.on_list_change = None
        self._handles.clear()
        self._ses = None
    del handles
    note("  состояние сессии: " + session_facts(ses))
    # Опыт «откуда отсчитывается секунда»: если срок взведён при
    # ses.pause(), то сон перед разрушением укоротит сам `del ses` ровно
    # на столько же; если при разрушении — не изменит ничего
    presleep = float(os.environ.get("VD_TRACE_PRESLEEP", "0") or 0)
    if presleep:
        time.sleep(presleep)
        note("  сон перед del ses", presleep)
    # Контроль: не сборка ли мусора Python? `del ses` роняет последнюю
    # ссылку, и на большом куче приложения это могло бы запустить проход
    # поколения 2. С VD_TRACE_GC=1 сборка отключается и делается ЯВНО
    # до разрушения — если время уедет туда, виноват не libtorrent
    # Кандидаты в исправление, проверяемые без правки приложения:
    #   remove — убрать раздачи из сессии перед разрушением;
    #   limit0 — обнулить лимит соединений (пиры отключатся сами).
    prestep = os.environ.get("VD_TRACE_PRESTEP", "")
    if prestep:
        t = time.monotonic()
        if prestep == "remove":
            for handle in ses.get_torrents():
                ses.remove_torrent(handle)
        elif prestep == "limit0":
            ses.apply_settings({"connections_limit": 0})
        note(f"  шаг {prestep}", time.monotonic() - t)
    if os.environ.get("VD_TRACE_GC"):
        gc.disable()
        t = time.monotonic()
        collected = gc.collect()
        note(f"  gc.collect ({collected} объектов)", time.monotonic() - t)
    del _GC_EVENTS[:]
    sampler = ConnSampler()
    sampler.start()
    t = time.monotonic()
    del ses
    note("  шаг del ses", time.monotonic() - t)
    note(f"  сборок мусора за этот шаг: {len(_GC_EVENTS) // 2} "
         f"{_GC_EVENTS[:6]}")
    for line in sampler.report():
        note("  сокет " + line)
    return {"saved": asked - unsaved, "unsaved": unsaved,
            "unflushed": unflushed, "seconds": time.monotonic() - t0}


torrent_engine.TorrentEngine.shutdown = traced_shutdown

wrap(gui.MainWindow, "closeEvent", "closeEvent")
wrap(torrent_engine.TorrentEngine, "shutdown", "engine.shutdown")
wrap(torrent_stream.StreamService, "shutdown", "stream.shutdown")
wrap(gui_torrent.TorrentPage, "stop_watchers", "stop_watchers")
wrap(gui_torrent.TorrentPage, "_end_prepare", "end_prepare")
wrap(gui.LibraryPage, "stop_workers", "library.stop_workers")
wrap(config, "save", "config.save")
wrap(QThread, "wait", "QThread.wait")
wrap(QThread, "quit", "QThread.quit")

atexit.register(lambda: note("atexit (начало финализации Python)"))
note("старт")
gui.run()
