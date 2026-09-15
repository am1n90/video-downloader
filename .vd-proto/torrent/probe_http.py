"""Этап 0.2, замер 4: корректность HTTP Range-сервера на раздаче, которая
ещё качается (локальный сид с ограничением отдачи, свежая папка).

Каждая проверка сверяет полученные байты с исходным файлом сида — так
заодно доказывается, что сервер не отдаёт незаписанные данные.
Сид: local_seed.py --up-limit 5242880 (порт 6890).
"""
import argparse
import http.client
import os
import shutil
import subprocess
import threading
import time
import traceback
import urllib.parse

from common import MB, PROTO_TMP, Report
import stream_server as ss

SEED_ROOT = os.path.join(PROTO_TMP, "seed")


def src_bytes(rel, offset, n):
    with open(os.path.join(SEED_ROOT, rel), "rb") as f:
        f.seek(offset)
        return f.read(n)


def call(port, path, method="GET", headers=None, read=None, host=None,
         conn=None):
    c = conn or http.client.HTTPConnection("127.0.0.1", port, timeout=180)
    hdrs = {"Host": host or f"127.0.0.1:{port}"}
    hdrs.update(headers or {})
    t0 = time.monotonic()
    c.request(method, path, headers=hdrs)
    r = c.getresponse()
    body = r.read() if read is None else r.read(read)
    return c, r, body, round(time.monotonic() - t0, 3)


class Checks:
    def __init__(self, report):
        self.report = report
        self.passed = 0
        self.failed = 0

    def run(self, name, fn):
        try:
            ok, detail = fn()
        except Exception as exc:  # проверка упала — это FAIL, не падение
            ok, detail = False, {"exception": repr(exc),
                                 "trace": traceback.format_exc()[-600:]}
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        self.report.record(event="check", name=name, ok=bool(ok), **detail)


def listening_addresses(port):
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                         text=True, errors="replace").stdout
    addrs = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1].endswith(f":{port}") and \
                parts[3] in ("LISTENING", "ПРОСЛУШИВАНИЕ"):
            addrs.add(parts[1])
    return sorted(addrs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--torrent", default=ss.DEFAULT_TORRENT)
    ap.add_argument("--seed-port", type=int, default=6890)
    ap.add_argument("--file", default=".mkv")
    ap.add_argument("--readahead", type=int, default=16)
    args = ap.parse_args()

    report = Report("probe_http")
    checks = Checks(report)
    save = ss.new_save_dir("http")
    streamer = ss.open_local(report, args.torrent, args.seed_port, save,
                             args.readahead)
    meta_s = streamer.wait_metadata()
    idx = streamer.choose_file(args.file)
    size = streamer.prepare_file(idx)
    rel = streamer.ti.files().file_path(idx)
    srv, token = ss.start_server(streamer, report)
    port = srv.server_port
    path = urllib.parse.urlsplit(ss.url_for(srv, token, streamer.ti, idx)).path
    report.record(event="start", file=rel, size_mb=round(size / MB),
                  metadata_s=meta_s, port=port)

    def head():
        c, r, body, dt = call(port, path, "HEAD")
        c.close()
        ok = (r.status == 200 and body == b"" and
              int(r.getheader("Content-Length")) == size and
              r.getheader("Accept-Ranges") == "bytes")
        return ok, {"status": r.status, "seconds": dt,
                    "content_type": r.getheader("Content-Type")}

    def first_100():
        c, r, body, dt = call(port, path, headers={"Range": "bytes=0-99"})
        c.close()
        ok = (r.status == 206 and body == src_bytes(rel, 0, 100) and
              r.getheader("Content-Range") == f"bytes 0-99/{size}")
        return ok, {"status": r.status, "seconds": dt}

    def suffix_1000():
        c, r, body, dt = call(port, path, headers={"Range": "bytes=-1000"})
        c.close()
        ok = (r.status == 206 and body == src_bytes(rel, size - 1000, 1000) and
              r.getheader("Content-Range") ==
              f"bytes {size - 1000}-{size - 1}/{size}")
        return ok, {"status": r.status, "seconds": dt}

    def unsatisfiable():
        c, r, _, dt = call(port, path, headers={"Range": f"bytes={size}-"})
        c.close()
        ok = r.status == 416 and r.getheader("Content-Range") == f"bytes */{size}"
        return ok, {"status": r.status}

    def middle_2mb():
        off = int(size * 0.6)
        piece = streamer.ti.map_file(idx, off, 1).piece
        had = streamer.h.have_piece(piece)
        c, r, body, dt = call(port, path,
                              headers={"Range": f"bytes={off}-{off + 2 * MB - 1}"})
        c.close()
        ok = r.status == 206 and body == src_bytes(rel, off, 2 * MB)
        return ok, {"status": r.status, "seconds": dt, "had_before": had}

    def client_abort_then_next():
        c, r, body, dt = call(port, path, read=MB)
        full_ok = r.status == 200 and int(r.getheader("Content-Length")) == size
        c.close()                       # обрыв посреди ответа
        time.sleep(0.5)
        c2, r2, body2, dt2 = call(port, path, headers={"Range": "bytes=100-199"})
        c2.close()
        ok = (full_ok and body == src_bytes(rel, 0, MB) and r2.status == 206 and
              body2 == src_bytes(rel, 100, 100))
        return ok, {"first_status": r.status, "next_status": r2.status,
                    "next_seconds": dt2}

    def wrong_token():
        c, r, _, _ = call(port, path.replace(token, "x" * len(token)))
        c.close()
        return r.status == 404, {"status": r.status}

    def wrong_host():
        c, r, _, _ = call(port, path, host=f"evil.example:{port}")
        c.close()
        return r.status == 403, {"status": r.status}

    def parallel_4():
        results = {}

        def worker(frac):
            off = int(size * frac)
            c, r, body, dt = call(port, path,
                                  headers={"Range": f"bytes={off}-{off + 4 * MB - 1}"})
            c.close()
            results[frac] = (r.status == 206 and
                             body == src_bytes(rel, off, 4 * MB), dt)

        threads = [threading.Thread(target=worker, args=(f,))
                   for f in (0.1, 0.3, 0.5, 0.9)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(300)
        ok = len(results) == 4 and all(v[0] for v in results.values())
        return ok, {"seconds": {str(k): v[1] for k, v in results.items()}}

    def keep_alive():
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        _, r1, b1, _ = call(port, path, headers={"Range": "bytes=0-9"}, conn=c)
        _, r2, b2, _ = call(port, path, headers={"Range": "bytes=10-19"}, conn=c)
        c.close()
        ok = (r1.status == r2.status == 206 and b1 == src_bytes(rel, 0, 10) and
              b2 == src_bytes(rel, 10, 10))
        return ok, {}

    def bind_localhost_only():
        addrs = listening_addresses(port)
        ok = srv.server_address[0] == "127.0.0.1" and addrs == [f"127.0.0.1:{port}"]
        return ok, {"listening": addrs}

    for name, fn in (("HEAD", head), ("Range 0-99", first_100),
                     ("Range суффикс -1000", suffix_1000),
                     ("416 за пределами файла", unsatisfiable),
                     ("Range 2 МБ на 60% (ожидание кусков)", middle_2mb),
                     ("обрыв клиента -> следующий запрос", client_abort_then_next),
                     ("чужой токен -> 404", wrong_token),
                     ("чужой Host -> 403", wrong_host),
                     ("4 параллельных диапазона", parallel_4),
                     ("keep-alive: 2 запроса в одном соединении", keep_alive),
                     ("слушает только 127.0.0.1", bind_localhost_only)):
        checks.run(name, fn)

    st = streamer.h.status()
    report.record(event="summary", passed=checks.passed, failed=checks.failed,
                  progress=round(st.progress, 3), alerts=report.alert_counts())
    srv.shutdown()
    report.record(event="closed", close_s=streamer.close())
    shutil.rmtree(save, ignore_errors=True)
    print(f"ИТОГ: {checks.passed} PASS, {checks.failed} FAIL", flush=True)


if __name__ == "__main__":
    main()
