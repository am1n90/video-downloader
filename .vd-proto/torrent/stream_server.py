"""Этап 0.2: HTTP Range-сервер поверх libtorrent — прототип ядра Этапа 2.

Отдаёт файл раздачи плееру и ffprobe, пока раздача ещё качается:
  http://127.0.0.1:<port>/<token>/<индекс файла>/<имя>
Недостающие куски запрашиваются через set_piece_deadline, ответ ждёт их
появления — сервер никогда не отдаёт незаписанные данные (нули).
Безопасность: слушает только 127.0.0.1, случайный токен в пути, проверка
заголовка Host (защита от DNS rebinding из браузера).

Ручной запуск (local_seed.py уже работает):
  python stream_server.py                      -> печатает URL для mpv/VLC
  python stream_server.py --magnet "magnet:?"  (настоящий рой — только дома)
"""
import argparse
import datetime
import gc
import http.server
import os
import re
import secrets
import threading
import time
import urllib.parse
from collections import OrderedDict

import libtorrent as lt

from common import (MB, PROTO_TMP, Report, alert_mask, alert_message,
                    make_session, memory_mb)

DEFAULT_TORRENT = os.path.join(PROTO_TMP, "torrents", "local-test.torrent")
CONTENT_TYPES = {
    ".mkv": "video/x-matroska", ".mp4": "video/mp4", ".m4v": "video/mp4",
    ".webm": "video/webm", ".avi": "video/x-msvideo",
    ".srt": "application/x-subrip",
}
VIDEO_EXTS = (".mkv", ".mp4", ".m4v", ".webm", ".avi")
READ_CHUNK = 256 * 1024
WAIT_TIMEOUT_S = 120
DEADLINE_STEP_MS = 100
# Данные берутся через h.read_piece(), НЕ чтением файла с диска: have_piece()
# становится true сразу после проверки хэша, а запись ещё в очереди
# дискового потока — sparse-файл в этот момент читается НУЛЯМИ (не короткое
# чтение). Найдено 15.09.2026 (diag: 100% нулей, от границы куска/блока).
PIECE_CACHE_BYTES = 64 * MB
READ_PIECE_RETRY_S = 5


class Streamer:
    """Раздача + чтение диапазонов файла с ожиданием недостающих кусков."""

    def __init__(self, ses, h, save_path, report, readahead=16,
                 seed_port=None):
        self.ses = ses
        self.h = h
        self.save_path = save_path
        self.report = report
        self.readahead = readahead
        self.seed_port = seed_port
        self.ti = None
        self.file_index = None
        self.cond = threading.Condition()
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self._windows = {}          # id запроса -> куски с дедлайном
        self._next_req = 0
        self.counters = {"requests": 0, "bytes": 0, "disconnects": 0,
                         "wait_s": 0.0, "max_request_wait_s": 0.0}
        self._cache = OrderedDict()     # кусок -> bytes (из read_piece)
        self._cache_bytes = 0
        self._pending = {}              # кусок -> время запроса read_piece
        threading.Thread(target=self._alert_loop, daemon=True).start()
        threading.Thread(target=self._stats_loop, daemon=True).start()

    # ---------- фоновые потоки

    def _alert_loop(self):
        wake = ("piece_finished_alert", "metadata_received_alert",
                "torrent_finished_alert", "file_completed_alert")
        ses = self.ses
        while not self.stop.is_set():
            ses.wait_for_alert(250)
            woke = False
            for a in ses.pop_alerts():
                name = type(a).__name__
                if name == "read_piece_alert":
                    self._on_read_piece(a)
                    woke = True
                elif name in wake:
                    woke = True
                elif any(w in name for w in ("error", "failed", "rejected")):
                    self.report.alert(name, alert_message(a))
            if woke:
                with self.cond:
                    self.cond.notify_all()

    def _on_read_piece(self, a):
        err = getattr(a, "error", None)
        if err is not None and err.value():
            self.report.alert("read_piece_alert_error", alert_message(a))
            with self._lock:
                self._pending.pop(a.piece, None)
            return
        data = bytes(a.buffer)
        with self._lock:
            self._pending.pop(a.piece, None)
            old = self._cache.pop(a.piece, None)
            if old is not None:
                self._cache_bytes -= len(old)
            self._cache[a.piece] = data
            self._cache_bytes += len(data)
            while self._cache_bytes > PIECE_CACHE_BYTES and len(self._cache) > 1:
                _, evicted = self._cache.popitem(last=False)
                self._cache_bytes -= len(evicted)

    def _stats_loop(self):
        h = self.h
        last_connect = 0.0
        while not self.stop.wait(1.0):
            st = h.status()
            now = time.monotonic()
            if self.seed_port and st.num_peers == 0 and now - last_connect > 2:
                h.connect_peer(("127.0.0.1", self.seed_port))
                last_connect = now
            rec = {"progress": round(st.progress, 4),
                   "rate_mb_s": round(st.download_payload_rate / MB, 2),
                   "peers": st.num_peers,
                   "private_mb": round(memory_mb()[0])}
            if self.file_index is not None and self.ti is not None:
                size = self.ti.files().file_size(self.file_index)
                done = h.file_progress()[self.file_index]
                rec["file_done_pct"] = round(100 * done / max(1, size), 1)
            self.report.record(quiet=True, event="sample", **rec)

    # ---------- подготовка

    def wait_metadata(self, timeout=180):
        t0 = time.monotonic()
        while not self.h.status().has_metadata:
            if time.monotonic() - t0 > timeout:
                raise TimeoutError("metadata timeout")
            with self.cond:
                self.cond.wait(0.25)
        self.ti = self.h.torrent_file()
        return round(time.monotonic() - t0, 2)

    def choose_file(self, name_part=None):
        fs = self.ti.files()
        found = []
        for i in range(self.ti.num_files()):
            path = fs.file_path(i).lower()
            if (name_part.lower() in path) if name_part else \
                    path.endswith(VIDEO_EXTS):
                found.append(i)
        if not found:
            raise ValueError(f"нет файла {name_part!r} в раздаче")
        return max(found, key=fs.file_size)

    def prepare_file(self, idx, head_tail_mb=8):
        """Качать только этот файл; голова и хвост (moov MP4, cues MKV) —
        максимальный приоритет, чтобы плеер быстрее разобрал контейнер."""
        self.file_index = idx
        prios = [0] * self.ti.num_files()
        prios[idx] = 4
        self.h.prioritize_files(prios)
        size = self.ti.files().file_size(idx)
        if head_tail_mb:
            span = min(head_tail_mb * MB, size)
            pieces = set(self._pieces(idx, 0, span)) | \
                set(self._pieces(idx, size - span, span))
            for p in pieces:
                self.h.piece_priority(p, 7)
        return size

    def _pieces(self, idx, offset, length):
        first = self.ti.map_file(idx, offset, 1).piece
        last = self.ti.map_file(idx, offset + length - 1, 1).piece
        return range(first, last + 1)

    # ---------- чтение диапазона

    def count(self, sent, disconnected, wait_s):
        with self._lock:
            c = self.counters
            c["requests"] += 1
            c["bytes"] += sent
            c["disconnects"] += int(disconnected)
            c["wait_s"] = round(c["wait_s"] + wait_s, 3)
            c["max_request_wait_s"] = round(max(c["max_request_wait_s"],
                                                wait_s), 3)

    def open_request(self):
        with self._lock:
            self._next_req += 1
            self._windows[self._next_req] = set()
            return self._next_req

    def close_request(self, rid):
        """Снять дедлайны, которые нужны были только этому запросу (плеер
        закрывает соединение при перемотке — не качать срочно ненужное)."""
        with self._lock:
            mine = self._windows.pop(rid, set())
            others = set().union(*self._windows.values()) \
                if self._windows else set()
        for p in mine - others:
            if not self.h.have_piece(p):
                self.h.reset_piece_deadline(p)

    def _request_window(self, rid, piece):
        last = min(piece + self.readahead, self.ti.num_pieces() - 1)
        added = []
        for i, q in enumerate(range(piece, last + 1)):
            if not self.h.have_piece(q):
                self.h.set_piece_deadline(q, i * DEADLINE_STEP_MS)
                added.append(q)
        with self._lock:
            window = self._windows.get(rid)
            if window is not None:
                window.update(added)

    def _get_piece(self, piece, stat):
        """Байты куска через read_piece (включая ещё не записанные на диск
        буферы libtorrent). Если куска нет — ждём его скачивания."""
        t0 = time.monotonic()
        missing = not self.h.have_piece(piece)
        if missing:
            stat["waits"] += 1
        while True:
            if self.stop.is_set():
                return None
            with self._lock:
                data = self._cache.get(piece)
                if data is not None:
                    self._cache.move_to_end(piece)
            if data is not None:
                if missing:
                    stat["wait_s"] += time.monotonic() - t0
                return data
            if time.monotonic() - t0 > WAIT_TIMEOUT_S:
                raise TimeoutError(f"piece {piece}")
            if self.h.have_piece(piece):
                now = time.monotonic()
                with self._lock:
                    asked = self._pending.get(piece)
                    need = asked is None or now - asked > READ_PIECE_RETRY_S
                    if need:
                        self._pending[piece] = now
                if need:
                    self.h.read_piece(piece)
                    stat["piece_reads"] += 1
            with self.cond:
                self.cond.wait(0.25)

    def iter_range(self, rid, idx, start, end, stat):
        pos = start
        while pos <= end:
            if self.stop.is_set():
                return
            req = self.ti.map_file(idx, pos, 1)
            self._request_window(rid, req.piece)
            data = self._get_piece(req.piece, stat)
            if data is None:
                return
            n = min(len(data) - req.start, end - pos + 1)
            if n <= 0:
                raise ValueError(f"piece {req.piece}: size {len(data)}, "
                                 f"offset {req.start}")
            view = memoryview(data)[req.start:req.start + n]
            for i in range(0, n, READ_CHUNK):
                yield view[i:i + READ_CHUNK]
            pos += n

    def close(self):
        self.stop.set()
        with self.cond:
            self.cond.notify_all()
        time.sleep(0.4)                 # фоновые потоки выходят из циклов
        holder = [self.ses, self.h]
        self.ses = self.h = None
        t = time.monotonic()
        holder.clear()
        gc.collect()
        return round(time.monotonic() - t, 2)


def _parse_range(header, size):
    """(start, end, partial) | "unsatisfiable". Непонятный Range —
    игнорируется (RFC 9110): отдаём файл целиком."""
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


def make_handler(streamer, token, report):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "vd-proto-stream/0.2"

        def log_message(self, fmt, *args):
            pass

        def handle(self):
            # Плеер (VLC) сбрасывает keep-alive соединение при перемотке:
            # чтение следующей строки запроса -> ConnectionResetError
            # [WinError 10054], и socketserver печатает traceback. Для
            # сервера это штатный разрыв — учитываем тихо (найдено 15.09).
            try:
                super().handle()
            except (ConnectionResetError, ConnectionAbortedError,
                    BrokenPipeError) as exc:
                report.record(quiet=True, event="http_connection_reset",
                              error=type(exc).__name__,
                              client_port=self.client_address[1])

        def _empty(self, code, extra=None):
            self.send_response(code)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _file_index(self):
            port = self.server.server_port
            host = self.headers.get("Host", "")
            if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                report.record(quiet=True, event="http_rejected", code=403,
                              host=host)
                self._empty(403)
                return None
            parts = urllib.parse.urlsplit(self.path).path.strip("/").split("/")
            if len(parts) < 2 or not secrets.compare_digest(parts[0], token) \
                    or not parts[1].isdigit() \
                    or int(parts[1]) >= streamer.ti.num_files():
                report.record(quiet=True, event="http_rejected", code=404)
                self._empty(404)
                return None
            return int(parts[1])

        def do_HEAD(self):
            self._serve(head=True)

        def do_GET(self):
            self._serve(head=False)

        def _serve(self, head):
            idx = self._file_index()
            if idx is None:
                return
            fs = streamer.ti.files()
            size = fs.file_size(idx)
            range_header = self.headers.get("Range")
            rng = _parse_range(range_header, size)
            if rng == "unsatisfiable":
                report.record(quiet=True, event="http", method=self.command,
                              range=range_header, status=416)
                self._empty(416, {"Content-Range": f"bytes */{size}"})
                return
            start, end, partial = rng
            ext = os.path.splitext(fs.file_path(idx))[1].lower()
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type",
                             CONTENT_TYPES.get(ext, "application/octet-stream"))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            log = {"event": "http", "method": self.command,
                   "range": range_header, "status": 206 if partial else 200,
                   "start": start, "end": end,
                   "client_port": self.client_address[1]}
            if head:
                report.record(quiet=True, **log)
                return
            stat = {"waits": 0, "wait_s": 0.0, "piece_reads": 0}
            rid = streamer.open_request()
            t0 = time.monotonic()
            sent = 0
            first_byte = None
            try:
                for chunk in streamer.iter_range(rid, idx, start, end, stat):
                    self.wfile.write(chunk)
                    sent += len(chunk)
                    if first_byte is None:
                        first_byte = time.monotonic() - t0
            except (ConnectionError, OSError) as exc:
                log["disconnect"] = type(exc).__name__
                self.close_connection = True
            except TimeoutError as exc:
                log["timeout"] = str(exc)
                self.close_connection = True
            finally:
                streamer.close_request(rid)
                streamer.count(sent, "disconnect" in log, stat["wait_s"])
                log.update(sent=sent, complete=sent == end - start + 1,
                           ttfb_s=None if first_byte is None
                           else round(first_byte, 3),
                           seconds=round(time.monotonic() - t0, 3),
                           waits=stat["waits"],
                           wait_s=round(stat["wait_s"], 3),
                           piece_reads=stat["piece_reads"])
                report.record(quiet=True, **log)

    return Handler


def start_server(streamer, report, port=0):
    token = secrets.token_urlsafe(16)
    srv = http.server.ThreadingHTTPServer(
        ("127.0.0.1", port), make_handler(streamer, token, report))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, token


def url_for(srv, token, ti, idx):
    name = os.path.basename(ti.files().file_path(idx))
    return (f"http://127.0.0.1:{srv.server_port}/{token}/{idx}/"
            f"{urllib.parse.quote(name)}")


def _session_extra():
    return {"alert_mask": alert_mask() | int(lt.alert_category.piece_progress)}


def open_local(report, torrent, seed_port, save_path, readahead=16):
    ses = make_session("127.0.0.1:0", local_only=True, extra=_session_extra())
    ti = lt.torrent_info(torrent)
    atp = lt.add_torrent_params()
    atp.ti = ti
    atp.save_path = save_path
    atp.file_priorities = [0] * ti.num_files()
    h = ses.add_torrent(atp)
    h.connect_peer(("127.0.0.1", seed_port))
    return Streamer(ses, h, save_path, report, readahead, seed_port)


def open_magnet(report, uri, save_path, readahead=16):
    ses = make_session("0.0.0.0:0,[::]:0", extra=_session_extra())
    atp = lt.parse_magnet_uri(uri)
    atp.save_path = save_path
    h = ses.add_torrent(atp)
    return Streamer(ses, h, save_path, report, readahead)


def new_save_dir(prefix):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(PROTO_TMP, "stream", f"{prefix}-{stamp}")
    os.makedirs(path, exist_ok=True)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--torrent", default=DEFAULT_TORRENT)
    ap.add_argument("--seed-port", type=int, default=6890)
    ap.add_argument("--magnet")
    ap.add_argument("--file", help="часть имени файла (по умолчанию — "
                                   "самое большое видео)")
    ap.add_argument("--save")
    ap.add_argument("--seconds", type=int, default=0, help="0 = до Ctrl+C")
    ap.add_argument("--readahead", type=int, default=16)
    ap.add_argument("--head-tail-mb", type=int, default=8)
    args = ap.parse_args()

    report = Report("stream_server")
    save = args.save or new_save_dir("manual")
    os.makedirs(save, exist_ok=True)
    if args.magnet:
        streamer = open_magnet(report, args.magnet, save, args.readahead)
    else:
        streamer = open_local(report, args.torrent, args.seed_port, save,
                              args.readahead)
    meta_s = streamer.wait_metadata()
    idx = streamer.choose_file(args.file)
    size = streamer.prepare_file(idx, args.head_tail_mb)
    srv, token = start_server(streamer, report)
    url = url_for(srv, token, streamer.ti, idx)
    report.record(event="serving", url=url,
                  file=streamer.ti.files().file_path(idx),
                  size_mb=round(size / MB), metadata_s=meta_s, save=save)
    print("URL", url, flush=True)
    t0 = time.monotonic()
    try:
        while not args.seconds or time.monotonic() - t0 < args.seconds:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.shutdown()
        report.record(event="closed", close_s=streamer.close(),
                      alerts=report.alert_counts())


if __name__ == "__main__":
    main()
