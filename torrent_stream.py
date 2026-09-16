"""Просмотр во время закачки: HTTP Range-сервер (торрент-стриминг, 2.1).

Отдаёт файл раздачи внешнему плееру (mpv/VLC), пока раздача качается:
  http://127.0.0.1:<порт>/<токен>/<ключ>/<имя файла>

Модуль намеренно НЕ знает про libtorrent: он работает с «источником» —
объектом с size / name / path / closed и методами open_request,
close_request, iter_range, close. Источник режима Torrent —
torrent_engine.TorrentStream; в тестах — поддельный объект, поэтому весь
разбор Range, токен, Host и обрывы клиента проверяются без сети и без
libtorrent.

Безопасность (как в прототипе Этапа 0.2): слушаем только 127.0.0.1,
случайный токен в пути, проверка заголовка Host (защита от DNS
rebinding из браузера). Сокет на loopback запроса брандмауэра не
вызывает — в отличие от самой сессии libtorrent (находка Этапа 0.1).

Учтённые находки прототипа:
  - VLC при перемотке сбрасывает keep-alive соединение: ConnectionReset-
    Error [WinError 10054] при чтении следующего запроса, socketserver
    печатает traceback — штатный разрыв, гасим в Handler.handle()
    (находка 9);
  - непонятный заголовок Range по RFC 9110 игнорируется (отдаём файл
    целиком), суффиксный диапазон и 416 — как в probe_http.
"""

import http.server
import os
import re
import secrets
import threading
import time
import urllib.parse

import config

log = config.get_logger("torrent", "torrent.log")

CONTENT_TYPES = {
    ".mkv": "video/x-matroska", ".mp4": "video/mp4", ".m4v": "video/mp4",
    ".webm": "video/webm", ".avi": "video/x-msvideo", ".mov": "video/quicktime",
    ".ts": "video/mp2t", ".m2ts": "video/mp2t", ".flv": "video/x-flv",
    ".wmv": "video/x-ms-wmv", ".mpg": "video/mpeg", ".mpeg": "video/mpeg",
    ".srt": "application/x-subrip", ".ass": "text/x-ssa",
}
VIDEO_EXTS = (".mkv", ".mp4", ".m4v", ".webm", ".avi", ".mov", ".ts",
              ".m2ts", ".flv", ".wmv", ".mpg", ".mpeg")

READ_CHUNK = 256 * 1024


def content_type(path):
    return CONTENT_TYPES.get(os.path.splitext(path)[1].lower(),
                             "application/octet-stream")


def is_video(path):
    return os.path.splitext(path)[1].lower() in VIDEO_EXTS


def watchable_files(files):
    """Что вообще можно смотреть: видеофайлы среди ВЫБРАННЫХ.

    Порядок — как в раздаче: у сериала имена серий обычно идут по
    порядку, и пользователю в диалоге (2.2) привычнее видеть их так, а
    не по размеру. Файлы со снятой галочкой не предлагаем: их libtorrent
    не качает.
    """
    return [f for f in files if f.priority > 0 and is_video(f.path)]


def choose_video_file(files):
    """Что смотреть, когда выбирать не из чего (или не у кого спросить):
    самый большой видеофайл среди выбранных.

    С 2.2 при двух и более видео GUI показывает диалог; это правило
    осталось умолчанием для одного файла и предвыбором в диалоге.
    """
    videos = watchable_files(files)
    if not videos:
        return None
    return max(videos, key=lambda f: f.size)


def parse_range(header, size):
    """(start, end, partial) либо "unsatisfiable".

    Непонятный Range по RFC 9110 игнорируется — отдаём файл целиком.
    """
    if not header:
        return 0, size - 1, False
    m = re.fullmatch(r"\s*bytes=(\d*)-(\d*)\s*", header)
    if not m or (m.group(1) == "" and m.group(2) == ""):
        return 0, size - 1, False
    first, last = m.group(1), m.group(2)
    if first == "":
        suffix = int(last)
        if suffix == 0:
            return "unsatisfiable"
        return max(0, size - suffix), size - 1, True
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        return "unsatisfiable"
    return start, end, True


class StreamStats:
    """Счётчики одного потока (одного ключа сервера).

    Общих счётчиков сервера для 2.2 не хватает: индикатор «готовим
    плеер» должен знать, обратился ли плеер именно к ЭТОМУ потоку, а
    замеры на рое — сколько байт и за сколько пришло по конкретному
    файлу. Время первого запроса и первого байта — по монотонным часам
    от момента serve().
    """

    __slots__ = ("started", "requests", "active", "bytes", "seconds",
                 "first_request", "first_byte")

    def __init__(self):
        self.started = time.monotonic()
        self.requests = 0
        self.active = 0
        self.bytes = 0
        self.seconds = 0.0
        self.first_request = None       # с момента started, секунды
        self.first_byte = None

    def snapshot(self):
        """Копия для чтения из GUI: сам объект меняется в потоках
        сервера, читать его по полю снаружи — гонка."""
        copy = StreamStats.__new__(StreamStats)
        for name in StreamStats.__slots__:
            setattr(copy, name, getattr(self, name))
        return copy


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VideoDownloader-stream/1.0"

    def log_message(self, fmt, *args):
        pass                    # свой лог — torrent.log, не stderr

    def handle(self):
        # Плеер закрывает соединение при перемотке — для нас это штатно
        # (находка 9): без перехвата socketserver печатает traceback.
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError,
                BrokenPipeError):
            pass

    # ---------------------------------------------------------- ответы

    def _empty(self, code, extra=None):
        self.send_response(code)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _source(self):
        """(ключ, источник) либо (None, None) — ответ уже отправлен."""
        owner = self.server.vd_owner
        port = self.server.server_port
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            owner.rejected += 1
            self._empty(403)
            return None, None
        parts = urllib.parse.unquote(
            urllib.parse.urlsplit(self.path).path).strip("/").split("/")
        if len(parts) < 2 or not secrets.compare_digest(parts[0], owner.token):
            owner.rejected += 1
            self._empty(404)
            return None, None
        source = owner.lookup(parts[1])
        if source is None or source.closed:
            # Раздачу удалили или сняли галочку с файла, пока плеер играл
            self._empty(404)
            return None, None
        return parts[1], source

    def do_HEAD(self):
        self._serve(head=True)

    def do_GET(self):
        self._serve(head=False)

    def _serve(self, head):
        key, source = self._source()
        if source is None:
            return
        size = source.size
        header = self.headers.get("Range")
        parsed = parse_range(header, size)
        if parsed == "unsatisfiable":
            self._empty(416, {"Content-Range": f"bytes */{size}"})
            return
        start, end, partial = parsed
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", content_type(source.path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head:
            return
        owner = self.server.vd_owner
        rid = source.open_request()
        owner.begin_request(key)
        t0 = time.monotonic()
        sent = 0
        try:
            for chunk in source.iter_range(rid, start, end):
                self.wfile.write(chunk)
                sent += len(chunk)
                owner.add_bytes(key, len(chunk))
        except (ConnectionError, OSError):
            self.close_connection = True        # плеер ушёл — это штатно
        except TimeoutError as exc:
            log.warning("stream: кусок не дождался (%s)", exc)
            self.close_connection = True
        except Exception as exc:                # один запрос не роняет сервер
            log.warning("stream: запрос прерван: %r", exc)
            self.close_connection = True
        finally:
            source.close_request(rid)
            owner.finish_request(key, sent, time.monotonic() - t0)
            if sent != end - start + 1:
                self.close_connection = True


class StreamServer:
    """ThreadingHTTPServer на 127.0.0.1 со случайным токеном в пути."""

    def __init__(self, host="127.0.0.1", port=0):
        self._host = host
        self._port = port
        self._srv = None
        self._lock = threading.Lock()
        self._sources = {}
        self._stats = {}            # ключ -> StreamStats (см. serve)
        self.token = ""
        self.requests = 0
        self.active = 0
        self.bytes = 0
        self.seconds = 0.0
        self.rejected = 0

    # ------------------------------------------------------------ жизнь

    def start(self):
        """Поднять сервер. Повторный вызов ничего не делает."""
        with self._lock:
            if self._srv is not None:
                return self._srv.server_port
            self.token = secrets.token_urlsafe(16)
            srv = http.server.ThreadingHTTPServer((self._host, self._port),
                                                  _Handler)
            srv.daemon_threads = True
            srv.vd_owner = self
            self._srv = srv
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.2},
                         daemon=True, name="torrent-stream").start()
        log.info("stream server on %s:%d", self._host, srv.server_port)
        return srv.server_port

    @property
    def port(self):
        srv = self._srv
        return srv.server_port if srv is not None else 0

    @property
    def running(self):
        return self._srv is not None

    def shutdown(self, timeout=1.0):
        """Закрыть сервер, не дожидаясь плеера.

        Обработчики могут ждать недостающий кусок (до PIECE_WAIT_TIMEOUT),
        поэтому сначала закрываем источники — это будит ожидающих, — и
        только потом останавливаем цикл сервера, с ограничением по
        времени: закрытие окна не должно зависеть от плеера.
        """
        t0 = time.monotonic()
        with self._lock:
            srv, self._srv = self._srv, None
            sources = list(self._sources.values())
            self._sources.clear()
            self._stats.clear()
        for source in sources:
            try:
                source.close()
            except Exception as exc:
                log.warning("stream: источник не закрылся: %r", exc)
        if srv is not None:
            done = threading.Thread(target=self._stop_server, args=(srv,),
                                    daemon=True)
            done.start()
            done.join(max(0.0, timeout))
        seconds = round(time.monotonic() - t0, 2)
        log.info("stream server closed in %.2f s (requests %d, %.1f MB)",
                 seconds, self.requests, self.bytes / (1024 * 1024))
        return seconds

    @staticmethod
    def _stop_server(srv):
        try:
            srv.shutdown()
            srv.server_close()
        except Exception as exc:
            log.warning("stream: сервер не остановился: %r", exc)

    # --------------------------------------------------------- источники

    def serve(self, key, source):
        """Отдавать источник по ключу; возвращает URL для плеера."""
        self.start()
        with self._lock:
            self._sources[key] = source
            # Счётчики нового просмотра начинаются с нуля: повторное
            # «Смотреть» тот же файл не должно выглядеть как «плеер уже
            # обратился» (индикатор подготовки ждёт именно первого
            # обращения ЭТОГО просмотра)
            self._stats[key] = StreamStats()
        return self.url_for(key, source.name)

    def lookup(self, key):
        with self._lock:
            return self._sources.get(key)

    def drop(self, key):
        with self._lock:
            self._stats.pop(key, None)
            return self._sources.pop(key, None)

    def url_for(self, key, name):
        return (f"http://{self._host}:{self.port}/{self.token}/{key}/"
                f"{urllib.parse.quote(name)}")

    # Счётчики — единственная видимость происходящего снаружи (лог при
    # закрытии, живые проверки, замеры 2.2). Байты считаем ПО ХОДУ, а не
    # в конце запроса: плеер держит один GET открытым всё время
    # воспроизведения, и счётчик «по завершении» показывал 1 МБ там, где
    # реально шли десятки (живая проверка 16.09.2026)

    def begin_request(self, key=None):
        with self._lock:
            self.requests += 1
            self.active += 1
            stats = self._stats.get(key)
            if stats is not None:
                stats.requests += 1
                stats.active += 1
                if stats.first_request is None:
                    stats.first_request = time.monotonic() - stats.started

    def add_bytes(self, key, count):
        with self._lock:
            self.bytes += count
            stats = self._stats.get(key)
            if stats is not None:
                stats.bytes += count
                if stats.first_byte is None:
                    stats.first_byte = time.monotonic() - stats.started

    def finish_request(self, key, sent, seconds):
        with self._lock:
            self.active -= 1
            self.seconds += seconds
            stats = self._stats.get(key)
            if stats is not None:
                stats.active -= 1
                stats.seconds += seconds

    def stats(self, key):
        """Снимок счётчиков потока или None, если такого ключа нет."""
        with self._lock:
            stats = self._stats.get(key)
            return stats.snapshot() if stats is not None else None


class StreamService:
    """Движок + сервер + одна активная раздача-просмотр.

    Просмотр в 2.1 один за раз (решение владельца): второй «Смотреть»
    останавливает первый. Сервис не знает про GUI и не запускает плеер —
    он только отдаёт URL.
    """

    def __init__(self, engine, server=None):
        self.engine = engine
        self.server = server if server is not None else StreamServer()
        self._lock = threading.Lock()
        self._active = None                 # (tid, index)

    @property
    def active(self):
        with self._lock:
            return self._active

    def is_watching(self, tid, index=None):
        active = self.active
        if active is None:
            return False
        return active[0] == tid and (index is None or active[1] == index)

    @staticmethod
    def _key(tid, index):
        return f"{tid}-{index}"

    def stats(self):
        """Счётчики активного просмотра (StreamStats) или None.

        По ним GUI понимает, ожил ли плеер: между его запуском и первым
        обращением к серверу бывает ~20 с — Защитник проверяет файлы
        плеера при первом запуске (находка 11 Этапа 0.2).
        """
        active = self.active
        if active is None:
            return None
        return self.server.stats(self._key(*active))

    def watch(self, tid, index):
        """Открыть поток и начать его отдавать; возвращает URL."""
        self.stop()
        stream = self.engine.open_stream(tid, index)
        url = self.server.serve(self._key(tid, index), stream)
        with self._lock:
            self._active = (tid, index)
        log.info("watch %s#%d -> %s", tid, index, stream.name)
        return url

    def stop(self):
        """Остановить просмотр. Плеер не трогаем — он отдельный процесс
        (решение №6), у него просто оборвётся соединение."""
        with self._lock:
            active, self._active = self._active, None
        if active is None:
            return False
        tid, index = active
        self.server.drop(self._key(tid, index))
        try:
            self.engine.close_stream(tid, index)
        except Exception as exc:
            log.warning("stream: поток не закрылся: %r", exc)
        return True

    def shutdown(self, timeout=1.0):
        """Закрытие окна: сначала сервис, потом engine.shutdown()."""
        with self._lock:
            self._active = None
        return self.server.shutdown(timeout)
