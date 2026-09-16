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
"""

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
            "alert_mask": int(lt.alert_category.error)
            | int(lt.alert_category.status)
            | int(lt.alert_category.storage),
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
        with self._lock:
            ses = self._ses
            if ses is None:
                return {"saved": 0, "unsaved": 0, "unflushed": 0,
                        "seconds": 0.0}
            ses.pause()
            handles = [(tid, h) for tid, h in self._handles.items()
                       if h.is_valid()]
            for tid, handle in handles:
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
        self._request_save(handle)
        self._emit(tid, handle)

    def file_progress(self, tid):
        """Скачано байт по каждому файлу (точность до куска)."""
        handle = self._require(tid)
        if not handle.status().has_metadata:
            return []
        return list(handle.file_progress(lt.torrent_handle.piece_granularity))

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
