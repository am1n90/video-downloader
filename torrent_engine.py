"""Торрент-движок: libtorrent без GUI (торрент-стриминг, Этап 1.2).

API — по образцу downloader.DownloadManager. Колбэки вызываются из
фонового потока движка (в GUI — через сигналы Qt, как Bridge):
    on_change(item)    — изменилось состояние раздачи (TorrentItem, снимок)
    on_list_change()   — изменился состав списка раздач

Жизненный цикл: TorrentEngine(...).start() -> add_magnet / add_torrent_file
-> set_files / pause / resume / retry / remove -> shutdown().

Учтённые находки прототипа (AGENTS.md, «Торрент-стриминг»):
  - alert.message() может бросить UnicodeDecodeError — _alert_text();
  - занятый файл: file_error_alert, раздача молча уходит в upload_mode и
    сама повторит только через 10 минут — состояние «error» с файлом,
    retry() снимает upload_mode; состояние «error» держится, пока флаг
    стоит у libtorrent (_error_is_live), а не по сохранённому тексту:
    алерты приходят пачкой и «догоняют» уже сделанный retry();
  - частичный выбор файлов оставляет .<infohash>.parts — remove() с
    удалением файлов убирает и его;
  - закрытие сессии с трекерами до 5 с — stop_tracker_timeout,
    shutdown(timeout);
  - шум udp_error/tracker_error (сотни за прогон) — лог с ограничением.

Данные: fastresume каждой раздачи — <data_dir>/resume/<id>.fastresume
(атомарная запись); по нему раздачи восстанавливаются при start().

С сессии 2.1 движок умеет отдавать файл, пока тот качается
(open_stream -> TorrentStream): данные ТОЛЬКО через read_piece(), голова
и хвост файла вперёд всего, окно кусков под set_piece_deadline. HTTP —
в torrent_stream.py, движок про него не знает.
"""

import collections
import dataclasses
import glob
import os
import sys
import threading
import time

import libtorrent as lt

import config

log = config.get_logger("torrent", "torrent.log")

STATE_METADATA = "metadata"        # ждём метаданные по magnet-ссылке
STATE_CHECKING = "checking"        # проверка файлов / fastresume
STATE_DOWNLOADING = "downloading"
STATE_SEEDING = "seeding"          # скачано, раздаётся
STATE_FINISHED = "finished"        # скачано, раздача выключена
STATE_PAUSED = "paused"            # пауза пользователя
STATE_ERROR = "error"              # ошибка файла/раздачи — см. item.error

RESUME_EXT = ".fastresume"
SAVE_RESUME_EVERY = 30             # с — периодическое сохранение fastresume
UPDATE_EVERY = 0.5                 # с — обновление снимков для GUI
ALERT_LOG_FIRST = 3                # шумные алерты: первые N в лог, затем
ALERT_LOG_EVERY = 100              # каждый N-й

_CHECKING_STATES = {"checking_files", "checking_resume_data",
                    "queued_for_checking", "allocating"}

# --- просмотр во время закачки (Этап 2, сессия 2.1) ------------------
MB = 1024 * 1024
STREAM_PRIORITY = 4                # приоритет файла, который смотрят
PIECE_PRIORITY_URGENT = 7          # голова/хвост и куски под дедлайном
HEAD_TAIL_BYTES = 8 * MB           # moov у MP4, cues у MKV — вперёд всего
# Прототип читал вперёд 16 кусков при куске 512 КБ (8 МБ). В настоящих
# раздачах кусок бывает 8-16 МБ, и те же «16 кусков» — это сотни мегабайт
# в памяти GUI-процесса. Поэтому окно и кэш считаем в БАЙТАХ, а число
# кусков только ограничиваем сверху и снизу.
READAHEAD_BYTES = 32 * MB
READAHEAD_MIN_PIECES = 2
READAHEAD_MAX_PIECES = 16
PIECE_CACHE_BYTES = 64 * MB
DEADLINE_STEP_MS = 100             # ступенька срочности внутри окна
PIECE_WAIT_TIMEOUT = 120           # с — сколько ждём кусок, прежде чем
                                   # оборвать ответ плееру
READ_PIECE_RETRY_S = 5             # с — повтор read_piece, если ответа нет
READ_CHUNK = 256 * 1024            # размер куска ответа плееру


def default_data_dir():
    """frozen — %LOCALAPPDATA%\\VideoDownloader\\torrents (рядом с
    settings.json, переживает обновления); dev — torrent-data в корне
    проекта (в .gitignore)."""
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "VideoDownloader", "torrents")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "torrent-data")


def _alert_text(alert):
    """alert.message() безопасно: на русской Windows libtorrent обрезает
    системный текст ошибки посреди многобайтовой буквы."""
    try:
        return alert.message()
    except UnicodeDecodeError as exc:
        return f"<текст ошибки не читается: {exc}>"


def _hex(digest):
    to_bytes = getattr(digest, "to_bytes", None)
    if to_bytes is not None:
        return to_bytes().hex()
    return str(digest)


def _id_from_hashes(hashes):
    """Идентификатор раздачи: v1 info-hash (40 hex), для v2-only — v2."""
    v1 = _hex(hashes.v1)
    if v1.strip("0"):
        return v1
    return _hex(hashes.v2)


def _write_atomic(path, data):
    """tmp + fsync + os.replace (3 попытки: антивирус/индексатор может
    держать файл). Своя функция, а не config.save: там политика именно
    settings.json (JSON, _skip_saving, app.log)."""
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(3):
        try:
            os.replace(tmp, path)
            return
        except OSError:
            if attempt == 2:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            time.sleep(0.1)


@dataclasses.dataclass(frozen=True)
class TorrentFile:
    index: int
    path: str          # путь внутри раздачи
    size: int
    priority: int      # 0 — не качать, 1-7 — качать


@dataclasses.dataclass(frozen=True)
class TorrentItem:
    """Снимок состояния раздачи для GUI (не меняется после создания)."""
    id: str
    name: str
    state: str
    progress: float            # 0..1 по выбранным файлам
    download_rate: int         # байт/с (полезные данные)
    upload_rate: int
    num_peers: int
    num_seeds: int
    wanted_size: int           # байт по ЦЕЛЫМ кускам выбранных файлов
    wanted_done: int           # (libtorrent total_wanted: кусок на границе
                               # выбранного файла учитывается целиком) —
                               # на них считается progress
    total_size: int            # байт — вся раздача (0 без метаданных)
    save_path: str
    has_metadata: bool
    files: tuple = ()          # TorrentFile, после получения метаданных
    selected_size: int = 0     # байт — сумма размеров выбранных файлов (для GUI)
    error: str = ""
    error_file: str = ""


class TorrentStream:
    """Чтение файла раздачи по диапазонам, пока он ещё качается.

    Источник для torrent_stream.StreamServer; создаётся движком
    (TorrentEngine.open_stream), напрямую не конструируется — кэш кусков
    наполняется из общего цикла алертов движка (pop_alerts может быть
    только у одного потребителя).

    Данные берутся ТОЛЬКО через read_piece(): в libtorrent 2.x кусок
    помечается готовым сразу после проверки хэша, а запись ещё в очереди
    дискового потока, и sparse-файл читается в этот момент НУЛЯМИ —
    первая версия сервера в прототипе отдавала плееру нули (находка 7
    Этапа 0.2). Ни движок, ни плеер не читают недокачанный файл с диска.
    """

    def __init__(self, engine, tid, handle, index, ti):
        self.tid = tid
        self.index = index
        self._engine = engine
        self._handle = handle
        self._ti = ti
        files = ti.files()
        self.path = files.file_path(index)
        self.name = os.path.basename(self.path)
        self.size = files.file_size(index)
        self.piece_length = ti.piece_length()
        self.readahead = max(READAHEAD_MIN_PIECES,
                             min(READAHEAD_MAX_PIECES,
                                 READAHEAD_BYTES // max(1, self.piece_length)))
        self._cache_limit = max(PIECE_CACHE_BYTES,
                                (self.readahead + 2) * self.piece_length)
        self._cond = threading.Condition()
        self._closed = False
        self._cache = collections.OrderedDict()   # кусок -> bytes
        self._cache_bytes = 0
        self._pending = {}          # кусок -> когда просили read_piece
        self._windows = {}          # id запроса -> куски под дедлайном
        self._next_req = 0
        self._raised = {}           # кусок -> приоритет до просмотра
        self._head_tail_done = False
        self._expected = None       # приоритеты файлов, применения которых ждём
        self._file_prio = STREAM_PRIORITY   # к нему возвращаем куски окна
        self.waits = 0              # счётчики для лога и тестов
        self.piece_reads = 0
        self.wait_seconds = 0.0
        self._prepare()

    @property
    def closed(self):
        return self._closed

    # ------------------------------------------------------- подготовка

    def _prepare(self):
        """Файл — в закачку; голова и хвост — следом, см. _raise_head_tail."""
        priorities = [int(p) for p in self._handle.get_file_priorities()]
        if self.index < len(priorities) and not priorities[self.index]:
            priorities[self.index] = STREAM_PRIORITY
            self._handle.prioritize_files(priorities)
            self._expected = priorities
        self._raise_head_tail()

    def rearm_head_tail(self, expected=None):
        """Снова поднять голову и хвост: любой prioritize_files по этой
        раздаче (пользователь открыл дерево файлов во время просмотра)
        переписывает приоритеты кусков приоритетом файла.

        expected — список приоритетов, который только что применили:
        ждать придётся именно его, потому что «приоритет нашего файла
        больше нуля» здесь уже выполнялось и ДО изменения.
        """
        with self._cond:
            self._head_tail_done = False
            self._raised.clear()
            self._expected = [int(p) for p in expected] if expected else None
        self._raise_head_tail()

    def _raise_head_tail(self):
        """Голова и хвост файла — вперёд всего: там moov у MP4 (в
        прототипе он встречался в конце файла) и cues у MKV, без них
        плеер не разберёт контейнер.

        Поднимаем ТОЛЬКО когда движок уже применил приоритет файла.
        prioritize_files асинхронный (находка 21), и когда он наконец
        применяется, он переписывает приоритеты ВСЕХ кусков файла его
        собственным приоритетом. Вызванный сразу за ним piece_priority
        читался обратно семёркой, а через 0.2 с молча становился
        четвёркой (диагностика 16.09.2026) — то есть срочность головы и
        хвоста терялась незаметно. Признак применения — приоритет файла,
        прочитанный обратно: get_file_priorities отдаёт новое значение
        только после того, как изменение обработано (в замере — 21 мс).
        """
        if self._head_tail_done or not self.size:
            return
        try:
            current = [int(p) for p in self._handle.get_file_priorities()]
        except Exception:
            return
        expected = self._expected
        applied = (current == expected) if expected is not None \
            else bool(current[self.index])
        if not applied:
            return                      # изменение ещё не применилось
        with self._cond:
            if self._head_tail_done:
                return
            self._head_tail_done = True
            self._expected = None
            self._file_prio = current[self.index] or STREAM_PRIORITY
        span = min(HEAD_TAIL_BYTES, self.size)
        pieces = set(self._pieces(0, span))
        pieces |= set(self._pieces(self.size - span, span))
        for piece in sorted(pieces):
            if self._have(piece):
                continue
            old = int(self._handle.piece_priority(piece))
            if old >= PIECE_PRIORITY_URGENT:
                continue
            self._raised[piece] = old
            self._handle.piece_priority(piece, PIECE_PRIORITY_URGENT)

    def _pieces(self, offset, length):
        first = self._ti.map_file(self.index, offset, 1).piece
        last = self._ti.map_file(self.index, offset + length - 1, 1).piece
        return range(first, last + 1)

    def _have(self, piece):
        try:
            return self._handle.have_piece(piece)
        except Exception:           # раздачу удалили — handle уже невалиден
            return False

    # ----------------------------------------------------------- запросы

    def open_request(self):
        with self._cond:
            self._next_req += 1
            self._windows[self._next_req] = set()
            return self._next_req

    def close_request(self, rid):
        """Плеер при перемотке рвёт соединение — снимаем срочность с
        кусков, которые были нужны только этому запросу."""
        with self._cond:
            mine = self._windows.pop(rid, set())
            others = set().union(*self._windows.values()) \
                if self._windows else set()
        self._reset_deadlines(mine - others)

    def _request_window(self, rid, piece):
        self._raise_head_tail()     # ждали, пока применится приоритет файла
        last = min(piece + self.readahead, self._ti.num_pieces() - 1)
        added = []
        for step, other in enumerate(range(piece, last + 1)):
            if self._have(other):
                continue
            try:
                self._handle.set_piece_deadline(other, step * DEADLINE_STEP_MS)
            except Exception:
                continue
            added.append(other)
        if not added:
            return
        with self._cond:
            window = self._windows.get(rid)
            if window is not None:
                window.update(added)

    def _reset_deadlines(self, pieces):
        """Снять срочность с кусков и ВЕРНУТЬ им приоритет.

        reset_piece_deadline не возвращает прежний приоритет, а ставит 1
        (диагностика 16.09.2026: было 7 — стало 1, было 4 — стало 1;
        заодно выяснилось, что set_piece_deadline сам поднимает кусок до
        7). Без восстановления куски прямо перед позицией плеера после
        каждой перемотки оказывались бы В КОНЦЕ очереди — ровно те, что
        нужны раньше всех, — а срочность головы и хвоста пропадала бы на
        первом же закрытии запроса.
        """
        for piece in pieces:
            try:
                if self._handle.have_piece(piece):
                    continue
                self._handle.reset_piece_deadline(piece)
                self._handle.piece_priority(
                    piece, PIECE_PRIORITY_URGENT if piece in self._raised
                    else self._file_prio)
            except Exception:
                pass

    # ------------------------------------------------------------ чтение

    def iter_range(self, rid, start, end):
        """Байты файла [start, end] включительно, кусками READ_CHUNK."""
        pos = start
        while pos <= end:
            if self._closed:
                return
            mapped = self._ti.map_file(self.index, pos, 1)
            self._request_window(rid, mapped.piece)
            data = self._get_piece(mapped.piece)
            if data is None:                    # поток закрыли
                return
            count = min(len(data) - mapped.start, end - pos + 1)
            if count <= 0:
                raise ValueError(f"кусок {mapped.piece}: длина {len(data)}, "
                                 f"смещение {mapped.start}")
            view = memoryview(data)[mapped.start:mapped.start + count]
            for offset in range(0, count, READ_CHUNK):
                yield view[offset:offset + READ_CHUNK]
            pos += count

    def _get_piece(self, piece):
        """Байты куска; если куска ещё нет — ждём его скачивания."""
        started = time.monotonic()
        missing = not self._have(piece)
        if missing:
            self.waits += 1
        data = None
        while True:
            with self._cond:
                if self._closed:
                    return None
                data = self._cache.get(piece)
                if data is not None:
                    self._cache.move_to_end(piece)
                    break
            if time.monotonic() - started > PIECE_WAIT_TIMEOUT:
                raise TimeoutError(f"кусок {piece} не пришёл за "
                                   f"{PIECE_WAIT_TIMEOUT} с")
            if self._have(piece):
                self._ask_piece(piece)
            with self._cond:
                if not self._closed and piece not in self._cache:
                    self._cond.wait(0.25)
        if missing:
            self.wait_seconds += time.monotonic() - started
        return data

    def _ask_piece(self, piece):
        now = time.monotonic()
        with self._cond:
            asked = self._pending.get(piece)
            if asked is not None and now - asked <= READ_PIECE_RETRY_S:
                return
            self._pending[piece] = now
        try:
            self._handle.read_piece(piece)
        except Exception as exc:
            log.warning("read_piece %s: %r", self.tid, exc)
            return
        self.piece_reads += 1

    def on_read_piece(self, alert):
        """read_piece_alert из цикла алертов движка."""
        piece = alert.piece
        error = getattr(alert, "error", None)
        if error is not None and error.value():
            log.warning("read_piece_alert %s: %s", self.tid,
                        _alert_text(alert))
            with self._cond:
                self._pending.pop(piece, None)
                self._cond.notify_all()
            return
        data = bytes(alert.buffer)
        with self._cond:
            self._pending.pop(piece, None)
            old = self._cache.pop(piece, None)
            if old is not None:
                self._cache_bytes -= len(old)
            self._cache[piece] = data
            self._cache_bytes += len(data)
            while self._cache_bytes > self._cache_limit and len(self._cache) > 1:
                _, evicted = self._cache.popitem(last=False)
                self._cache_bytes -= len(evicted)
            self._cond.notify_all()

    def wake(self):
        """Пришёл новый кусок — разбудить ожидающих."""
        with self._cond:
            self._cond.notify_all()

    # ---------------------------------------------------------- закрытие

    def close(self):
        """Снять дедлайны, вернуть приоритеты, разбудить ожидающих.

        Повторный вызов ничего не делает. Ожидающие куска запросы выходят
        немедленно — от этого зависит закрытие окна во время просмотра.
        """
        with self._cond:
            if self._closed:
                return
            self._closed = True
            pieces = set().union(*self._windows.values()) \
                if self._windows else set()
            self._windows.clear()
            self._cache.clear()
            self._cache_bytes = 0
            self._pending.clear()
            raised, self._raised = self._raised, {}
            self._cond.notify_all()
        self._reset_deadlines(pieces)
        # Голову и хвост возвращаем к приоритету САМОГО ФАЙЛА, а не к
        # запомненному: если файл был не выбран, _prepare включил его, и
        # запомненный ноль вернул бы дыры в уже начатой закачке. Ноль
        # уместен только если файл сняли галочкой прямо во время просмотра.
        try:
            file_priority = int(
                self._handle.get_file_priorities()[self.index])
        except Exception:
            file_priority = STREAM_PRIORITY
        for piece, old in raised.items():
            try:
                self._handle.piece_priority(piece,
                                            file_priority or old)
            except Exception:
                pass
        log.info("stream closed %s#%d: read_piece %d, ожиданий %d (%.1f с)",
                 self.tid, self.index, self.piece_reads, self.waits,
                 self.wait_seconds)


class TorrentEngine:
    def __init__(self, data_dir=None, on_change=None, on_list_change=None,
                 seed_after_download=True, listen_interfaces=None,
                 extra_settings=None):
        self.data_dir = data_dir or default_data_dir()
        self.resume_dir = os.path.join(self.data_dir, "resume")
        self.on_change = on_change
        self.on_list_change = on_list_change
        self._seed_after_download = bool(seed_after_download)
        self._settings = {
            # piece_progress — ради piece_finished_alert: им будится
            # просмотр, ждущий недостающий кусок (read_piece_alert —
            # это storage). В лог эти алерты не идут, шума нет.
            "alert_mask": int(lt.alert_category.error)
            | int(lt.alert_category.status)
            | int(lt.alert_category.storage)
            | int(lt.alert_category.piece_progress),
            "stop_tracker_timeout": 1,
            "user_agent": f"VideoDownloader/{config.APP_VERSION} "
                          f"libtorrent/{lt.__version__}",
        }
        if listen_interfaces:
            self._settings["listen_interfaces"] = listen_interfaces
        if extra_settings:
            self._settings.update(extra_settings)
        self._ses = None
        self._lock = threading.RLock()
        self._handles = {}          # id -> torrent_handle
        self._files = {}            # id -> tuple(TorrentFile) (после метаданных)
        self._errors = {}           # id -> (текст, файл)
        self._removed = set()       # id удалённых (fastresume не писать)
        self._streams = {}          # (id, индекс файла) -> TorrentStream
        self._pending_saves = set() # id с запрошенным fastresume (shutdown)
        self._pending_flush = set() # id с запрошенным сбросом кэша (shutdown)
        self._saves_done = threading.Condition(self._lock)
        self._alert_counts = {}
        self._stop = threading.Event()
        self._thread = None

    # ------------------------------------------------------------ жизнь

    def start(self):
        """Создать сессию, восстановить раздачи из fastresume, запустить
        фоновый поток алертов. Повторный вызов ничего не делает."""
        with self._lock:
            if self._ses is not None:
                return
            os.makedirs(self.resume_dir, exist_ok=True)
            params = lt.session_params()
            params.settings = self._settings
            self._ses = lt.session(params)
            restored = self._restore()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="torrent-engine")
        self._thread.start()
        log.info("engine started, data=%s, restored=%d", self.data_dir, restored)
        if restored:
            self._notify_list()

    def shutdown(self, timeout=3.0):
        """Сохранить fastresume всех раздач (не дольше timeout) и закрыть
        сессию. Возвращает {'saved', 'unsaved', 'unflushed', 'seconds'}.
        После вызова колбэки больше не вызываются.

        Порядок: пауза сессии -> flush_cache() и ожидание cache_flushed_alert
        -> save_resume_data. fastresume содержит только куски, уже
        записанные на диск: без сброса кэша в него не попадали проверенные,
        но ещё не записанные куски, и после перезапуска их качали заново
        (устаревший fastresume в 2 из 6 прогонов; флаг flush_disk_cache у
        save_resume_data этого не лечит — ответ приходит через 1 мс; со
        сбросом — 0 из 6, сброс 5-19 мс; проверено 16.09.2026)."""
        t0 = time.monotonic()
        deadline = t0 + timeout
        # Просмотр закрываем ПЕРВЫМ: обработчики HTTP могут ждать кусок
        # (до PIECE_WAIT_TIMEOUT), а пауза сессии ниже этот кусок уже не
        # принесёт — без close() окно висело бы до таймаута
        self._close_all_streams()
        with self._lock:
            ses = self._ses
            if ses is None:
                return {"saved": 0, "unsaved": 0, "unflushed": 0,
                        "seconds": 0.0}
            ses.pause()
            handles = [(tid, h) for tid, h in self._handles.items()
                       if h.is_valid()]
            for tid, handle in handles:
                # Без метаданных у раздачи нет хранилища: cache_flushed_alert
                # на flush_cache() не приходит НИКОГДА, и ожидание съедало
                # весь timeout — закрытие окна 3 с, а на save_resume_data
                # остальных раздач времени уже не оставалось (находка 42)
                if not handle.status().has_metadata:
                    continue
                self._pending_flush.add(tid)
                handle.flush_cache()
            while self._pending_flush and time.monotonic() < deadline:
                self._saves_done.wait(deadline - time.monotonic())
            unflushed = len(self._pending_flush)
            self._pending_flush.clear()
            for tid, handle in handles:
                self._pending_saves.add(tid)
                handle.save_resume_data(lt.save_resume_flags_t.save_info_dict)
            asked = len(self._pending_saves)
            while self._pending_saves and time.monotonic() < deadline:
                self._saves_done.wait(deadline - time.monotonic())
            unsaved = len(self._pending_saves)
            self._pending_saves.clear()
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2.0)
        with self._lock:
            self.on_change = self.on_list_change = None
            self._handles.clear()
            self._ses = None
        del ses                                  # закрытие сессии (трекеры)
        seconds = round(time.monotonic() - t0, 2)
        log.info("engine shutdown: saved %d/%d (unflushed %d) in %.2f s",
                 asked - unsaved, asked, unflushed, seconds)
        return {"saved": asked - unsaved, "unsaved": unsaved,
                "unflushed": unflushed, "seconds": seconds}

    # ------------------------------------------------------- настройки

    @property
    def seed_after_download(self):
        return self._seed_after_download

    def set_seed_after_download(self, enabled):
        """Раздача после скачивания (решение владельца №4: по умолчанию
        включена). Выключение ставит на паузу уже скачанные раздачи."""
        self._seed_after_download = bool(enabled)
        with self._lock:
            handles = list(self._handles.items())
        for tid, handle in handles:
            st = handle.status()
            if st.is_finished and st.has_metadata and not self._error_is_live(st):
                if enabled:
                    self._resume_handle(handle)
                else:
                    self._pause_handle(handle)
                self._emit(tid, handle)

    # --------------------------------------------------------- раздачи

    def add_magnet(self, uri, save_path, peers=None):
        atp = lt.parse_magnet_uri(uri)
        return self._add(atp, save_path, peers)

    def add_torrent_file(self, path, save_path, file_priorities=None,
                         peers=None):
        atp = lt.add_torrent_params()
        atp.ti = lt.torrent_info(path)
        if file_priorities is not None:
            atp.file_priorities = list(file_priorities)
        return self._add(atp, save_path, peers)

    def items(self):
        with self._lock:
            handles = list(self._handles.items())
        return [self._snapshot(tid, h) for tid, h in handles]

    def get(self, tid):
        handle = self._handle(tid)
        return None if handle is None else self._snapshot(tid, handle)

    def set_files(self, tid, priorities):
        """Выбор файлов раздачи: priorities[i] = 0 (не качать) или 1-7."""
        handle = self._require(tid)
        if not handle.status().has_metadata:
            raise RuntimeError("метаданные раздачи ещё не получены")
        handle.prioritize_files(list(priorities))
        with self._lock:
            self._files.pop(tid, None)          # пересобрать с приоритетами
        # Сняли галочку с файла, который смотрят: выбор пользователя
        # важнее просмотра — поток закрываем, плеер упрётся в 404
        for stream in self._streams_of(tid):
            if stream.index >= len(priorities) or not priorities[stream.index]:
                self.close_stream(tid, stream.index)
            else:
                # Просмотр продолжается, но prioritize_files сейчас сотрёт
                # срочность головы и хвоста — поднимем её заново
                stream.rearm_head_tail(priorities)
        self._request_save(handle)
        self._emit(tid, handle)

    def file_progress(self, tid):
        """Скачано байт по каждому файлу (точность до куска)."""
        handle = self._require(tid)
        if not handle.status().has_metadata:
            return []
        return list(handle.file_progress(lt.torrent_handle.piece_granularity))

    # ------------------------------------------- просмотр во время закачки

    def open_stream(self, tid, index):
        """Отдавать файл раздачи, пока он качается (см. TorrentStream).

        Движок допускает несколько потоков (ключ — раздача + файл);
        «один просмотр за раз» — правило GUI, не движка. Раздача на
        паузе снимается с паузы: смотреть то, что не качается, нельзя.
        """
        handle = self._require(tid)
        st = handle.status()
        ti = handle.torrent_file() if st.has_metadata else None
        if ti is None:
            raise RuntimeError("метаданные раздачи ещё не получены")
        if not 0 <= index < ti.num_files():
            raise IndexError(f"в раздаче нет файла с номером {index}")
        with self._lock:
            stream = self._streams.get((tid, index))
            if stream is not None and not stream.closed:
                return stream
        stream = TorrentStream(self, tid, handle, index, ti)
        with self._lock:
            self._streams[(tid, index)] = stream
        if st.paused and not self._error_is_live(st):
            self._resume_handle(handle)
        log.info("stream opened %s#%d (%s, кусок %d КБ, окно %d кусков)",
                 tid, index, stream.name, stream.piece_length // 1024,
                 stream.readahead)
        self._emit(tid, handle)
        return stream

    def close_stream(self, tid, index=None):
        """Закрыть поток(и) раздачи; index=None — все её потоки."""
        with self._lock:
            keys = [key for key in self._streams
                    if key[0] == tid and (index is None or key[1] == index)]
            streams = [self._streams.pop(key) for key in keys]
        for stream in streams:
            stream.close()
        return len(streams)

    def streams(self):
        with self._lock:
            return list(self._streams.values())

    def _streams_of(self, tid):
        with self._lock:
            return [stream for key, stream in self._streams.items()
                    if key[0] == tid]

    def _close_all_streams(self):
        with self._lock:
            streams = list(self._streams.values())
            self._streams.clear()
        for stream in streams:
            stream.close()

    def pause(self, tid):
        handle = self._require(tid)
        self._pause_handle(handle)
        self._request_save(handle)
        self._emit(tid, handle)

    def resume(self, tid):
        handle = self._require(tid)
        self._resume_handle(handle)
        self._emit(tid, handle)

    def retry(self, tid):
        """После ошибки файла (например, файл был занят): libtorrent держит
        раздачу в upload_mode и сам повторит лишь через 10 минут."""
        handle = self._require(tid)
        with self._lock:
            self._errors.pop(tid, None)
        handle.clear_error()
        handle.unset_flags(lt.torrent_flags.upload_mode)
        self._resume_handle(handle)
        self._emit(tid, handle)

    def remove(self, tid, delete_files=False):
        """Убрать раздачу; delete_files — удалить и скачанные файлы
        (вместе со служебным .<infohash>.parts)."""
        self.close_stream(tid)       # handle сейчас станет невалидным
        with self._lock:
            handle = self._handles.pop(tid, None)
            self._files.pop(tid, None)
            self._errors.pop(tid, None)
            self._removed.add(tid)
            ses = self._ses
        if handle is None or ses is None:
            return
        save_path = handle.status().save_path
        ses.remove_torrent(handle, lt.session.delete_files if delete_files else 0)
        for path in (self._resume_path(tid), self._resume_path(tid) + ".tmp"):
            try:
                os.remove(path)
            except OSError:
                pass
        if delete_files:
            self._delete_parts_later(save_path, tid)
        log.info("removed %s (delete_files=%s)", tid, delete_files)
        self._notify_list()

    # ------------------------------------------------------ внутреннее

    def _add(self, atp, save_path, peers):
        with self._lock:
            ses = self._ses
            if ses is None:
                raise RuntimeError("движок не запущен (start())")
            hashes = atp.ti.info_hashes() if atp.ti is not None else atp.info_hashes
            tid = _id_from_hashes(hashes)
            if tid in self._handles:
                return tid
            os.makedirs(save_path, exist_ok=True)
            atp.save_path = save_path
            if peers:
                atp.peers = [tuple(p) for p in peers]
            handle = ses.add_torrent(atp)
            self._handles[tid] = handle
            self._removed.discard(tid)
        self._request_save(handle)       # магнит сохраняется и без метаданных
        log.info("added %s -> %s", tid, save_path)
        self._notify_list()
        self._emit(tid, handle)
        return tid

    def _restore(self):
        count = 0
        for path in sorted(glob.glob(os.path.join(self.resume_dir,
                                                  "*" + RESUME_EXT))):
            try:
                with open(path, "rb") as f:
                    atp = lt.read_resume_data(f.read())
                hashes = atp.ti.info_hashes() if atp.ti is not None \
                    else atp.info_hashes
                tid = _id_from_hashes(hashes)
                handle = self._ses.add_torrent(atp)
            except Exception as exc:     # битый fastresume не ломает старт
                corrupt = path + ".corrupt"
                try:
                    os.replace(path, corrupt)
                except OSError:
                    corrupt = "(не переименован)"
                log.warning("fastresume not loaded %s: %r -> %s",
                            os.path.basename(path), exc, corrupt)
                continue
            self._handles[tid] = handle
            count += 1
        return count

    def _loop(self):
        last_update = last_save = 0.0
        while not self._stop.is_set():
            ses = self._ses
            if ses is None:
                return
            ses.wait_for_alert(250)
            for alert in ses.pop_alerts():
                try:
                    self._on_alert(alert)
                except Exception as exc:     # один алерт не роняет поток
                    log.warning("alert %s handling failed: %r",
                                type(alert).__name__, exc)
            now = time.monotonic()
            if now - last_update >= UPDATE_EVERY:
                ses.post_torrent_updates()
                last_update = now
            if now - last_save >= SAVE_RESUME_EVERY:
                with self._lock:
                    handles = list(self._handles.values())
                for handle in handles:
                    if handle.is_valid() and handle.need_save_resume_data():
                        self._request_save(handle)
                last_save = now

    def _on_alert(self, alert):
        name = type(alert).__name__
        if name == "state_update_alert":
            for st in alert.status:
                tid = _id_from_hashes(st.info_hashes)
                handle = self._handle(tid)
                if handle is not None:
                    self._emit(tid, handle, st)
            return
        handle = getattr(alert, "handle", None)
        tid = None
        if handle is not None and handle.is_valid():
            tid = _id_from_hashes(handle.info_hashes())

        # Просмотр: read_piece_alert приносит байты куска, piece_finished
        # будит тех, кто ждёт недостающий. Без потоков — сразу мимо
        # (piece_finished приходит на каждый кусок любой раздачи).
        if name in ("read_piece_alert", "piece_finished_alert"):
            if self._streams and tid:
                for stream in self._streams_of(tid):
                    if name == "read_piece_alert":
                        stream.on_read_piece(alert)
                    else:
                        stream.wake()
            return

        if name == "cache_flushed_alert":
            with self._lock:
                self._pending_flush.discard(tid)
                if not self._pending_flush:
                    self._saves_done.notify_all()
        elif name == "save_resume_data_alert":
            self._write_resume(tid, alert)
        elif name == "save_resume_data_failed_alert":
            self._save_finished(tid)
            self._log_alert(name, alert)
        elif name == "metadata_received_alert" and tid:
            with self._lock:
                self._files.pop(tid, None)
            self._request_save(handle)
            self._emit(tid, handle)
        elif name == "torrent_finished_alert" and tid:
            if not self._seed_after_download:
                self._pause_handle(handle)
            self._request_save(handle)
            self._emit(tid, handle)
        elif name == "file_error_alert" and tid:
            text = _alert_text(alert)
            error_file = getattr(alert, "filename", "")
            if callable(error_file):
                error_file = error_file()
            with self._lock:
                self._errors[tid] = (text, str(error_file or ""))
            log.warning("file error %s: %s", tid, text)
            self._emit(tid, handle)
        elif name in ("torrent_error_alert",) and tid:
            with self._lock:
                self._errors[tid] = (_alert_text(alert), "")
            self._log_alert(name, alert)
            self._emit(tid, handle)
        elif any(w in name for w in ("error", "failed", "rejected")):
            self._log_alert(name, alert)

    def _log_alert(self, name, alert):
        n = self._alert_counts.get(name, 0) + 1
        self._alert_counts[name] = n
        if n <= ALERT_LOG_FIRST or n % ALERT_LOG_EVERY == 0:
            log.warning("%s (#%d): %s", name, n, _alert_text(alert))

    def _request_save(self, handle):
        if handle is not None and handle.is_valid():
            handle.save_resume_data(lt.save_resume_flags_t.save_info_dict)

    def _write_resume(self, tid, alert):
        try:
            if tid and tid not in self._removed:
                _write_atomic(self._resume_path(tid),
                              lt.write_resume_data_buf(alert.params))
        except OSError as exc:
            log.warning("fastresume not written %s: %r", tid, exc)
        finally:
            self._save_finished(tid)

    def _save_finished(self, tid):
        with self._lock:
            self._pending_saves.discard(tid)
            if not self._pending_saves:
                self._saves_done.notify_all()

    def _resume_path(self, tid):
        return os.path.join(self.resume_dir, tid + RESUME_EXT)

    def _delete_parts_later(self, save_path, tid):
        """libtorrent удаляет файлы асинхронно; .parts может остаться —
        добираем его отдельным коротким потоком."""
        parts = os.path.join(save_path, f".{tid}.parts")

        def worker():
            for _ in range(50):
                try:
                    os.remove(parts)
                    return
                except FileNotFoundError:
                    time.sleep(0.1)
                except OSError:
                    time.sleep(0.1)

        threading.Thread(target=worker, daemon=True).start()

    def _handle(self, tid):
        with self._lock:
            return self._handles.get(tid)

    def _require(self, tid):
        handle = self._handle(tid)
        if handle is None:
            raise KeyError(f"раздача не найдена: {tid}")
        return handle

    @staticmethod
    def _pause_handle(handle):
        handle.unset_flags(lt.torrent_flags.auto_managed)
        handle.pause()

    @staticmethod
    def _resume_handle(handle):
        handle.set_flags(lt.torrent_flags.auto_managed)
        handle.resume()

    def _files_of(self, tid, handle):
        """Файлы раздачи; пути и размеры кэшируются, приоритеты — нет.

        prioritize_files асинхронный (находка 21): сразу после set_files
        libtorrent ещё отдаёт СТАРЫЕ приоритеты, поэтому кэш, собранный в
        этот момент, оставался неверным навсегда — выбор файлов применялся
        (качался только выбранный), но в снимке и в дереве с галочками
        по-прежнему числились все. Сверяем приоритеты на каждом снимке и
        пересобираем кортеж, только когда они действительно изменились.
        """
        priorities = handle.get_file_priorities()
        with self._lock:
            cached = self._files.get(tid)
        if cached is not None and len(cached) == len(priorities) and all(
                f.priority == p for f, p in zip(cached, priorities)):
            return cached
        ti = handle.torrent_file()
        if ti is None:
            return cached or ()
        fs = ti.files()
        files = tuple(
            TorrentFile(i, fs.file_path(i), fs.file_size(i),
                        priorities[i] if i < len(priorities) else 4)
            for i in range(ti.num_files()))
        with self._lock:
            self._files[tid] = files
        return files

    @staticmethod
    def _error_is_live(st):
        """Держит ли ошибку сам libtorrent прямо сейчас.

        На дисковой ошибке errc пустой, а раздача молча припаркована в
        upload_mode (находка 2 Этапа 0.2) — этот флаг и есть признак
        «стоит из-за ошибки». Состояние нельзя строить на одном лишь
        сохранённом тексте: file_error_alert приходит пачкой (16-19 штук
        на один занятый файл), и те, что разобраны уже ПОСЛЕ retry(),
        взводили состояние заново — навсегда, снять его было некому
        (раздача при этом спокойно докачивалась и раздавалась)."""
        errc = getattr(st, "errc", None)
        if errc is not None and errc.value():
            return True
        return bool(int(getattr(st, "flags", 0))
                    & int(lt.torrent_flags.upload_mode))

    def _snapshot(self, tid, handle, st=None):
        st = st or handle.status()
        with self._lock:
            error, error_file = self._errors.get(tid, ("", ""))
        if self._error_is_live(st):
            if not error:
                errc = getattr(st, "errc", None)
                error = (errc.message() if errc is not None and errc.value()
                         else "раздача остановлена из-за ошибки файла")
        else:
            error = error_file = ""      # текст — отголосок исправленной
        raw = str(st.state)
        if error:
            state = STATE_ERROR
        elif raw == "downloading_metadata":
            state = STATE_METADATA
        elif raw in _CHECKING_STATES:
            state = STATE_CHECKING
        elif st.is_finished and st.has_metadata:
            state = STATE_FINISHED if st.paused else STATE_SEEDING
        elif st.paused and not st.auto_managed:
            state = STATE_PAUSED
        else:
            state = STATE_DOWNLOADING
        files = self._files_of(tid, handle) if st.has_metadata else ()
        return TorrentItem(
            id=tid, name=st.name, state=state, progress=float(st.progress),
            download_rate=int(st.download_payload_rate),
            upload_rate=int(st.upload_payload_rate),
            num_peers=int(st.num_peers), num_seeds=int(st.num_seeds),
            wanted_size=int(st.total_wanted), wanted_done=int(st.total_wanted_done),
            total_size=sum(f.size for f in files),
            save_path=st.save_path, has_metadata=bool(st.has_metadata),
            files=files,
            selected_size=sum(f.size for f in files if f.priority > 0),
            error=error, error_file=error_file)

    def _emit(self, tid, handle, st=None):
        callback = self.on_change
        if callback is None or not handle.is_valid():
            return
        try:
            callback(self._snapshot(tid, handle, st))
        except Exception as exc:
            log.warning("on_change failed: %r", exc)

    def _notify_list(self):
        callback = self.on_list_change
        if callback is None:
            return
        try:
            callback()
        except Exception as exc:
            log.warning("on_list_change failed: %r", exc)
