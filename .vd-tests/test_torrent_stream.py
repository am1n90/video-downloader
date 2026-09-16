# -*- coding: utf-8 -*-
"""Офлайн-тест HTTP Range-сервера просмотра (сессия 2.1).

Без libtorrent и без сети: источник поддельный (байты в памяти), поэтому
проверки детерминированные и быстрые. Настоящее чтение кусков раздачи
проверяет test_torrent_engine.py на локальном сиде.

Сценарии — по таблице прототипа (RESULTS.md, замер 7): HEAD, диапазоны и
суффиксы, 416, мусор в Range, чужой токен, чужой Host, обрыв клиента,
keep-alive, параллельные запросы, слушаем только 127.0.0.1. Плюс то,
чего в прототипе не было: закрытый источник -> 404 и главное для
закрытия окна — shutdown() будит запрос, застрявший в ожидании куска.
"""
import http.client
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import torrent_stream as ts

PASS, FAIL = [], []


def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details, flush=True)


SIZE = 3 * 1024 * 1024 + 12345
DATA = bytes((i * 7 + i // 251) % 256 for i in range(SIZE))


class FakeSource:
    """Источник с теми же методами, что TorrentStream."""

    def __init__(self, data=DATA, name="видео 1.mkv", block=False):
        self.data = data
        self.size = len(data)
        self.name = name
        self.path = "Тестовая раздача/" + name
        self.closed = False
        self.opened = []
        self.finished = []
        self._block = block
        self._wake = threading.Event()
        self.entered = threading.Event()

    def open_request(self):
        rid = len(self.opened) + 1
        self.opened.append(rid)
        return rid

    def close_request(self, rid):
        self.finished.append(rid)

    def iter_range(self, rid, start, end):
        pos = start
        while pos <= end:
            if self.closed:
                return
            if self._block:
                # Ведём себя как ожидание недостающего куска
                self.entered.set()
                while not self.closed and not self._wake.wait(0.05):
                    pass
                if self.closed:
                    return
            count = min(64 * 1024, end - pos + 1)
            yield memoryview(self.data)[pos:pos + count]
            pos += count

    def close(self):
        self.closed = True
        self._wake.set()


def url(server, key, name="видео 1.mkv"):
    return server.url_for(key, name)


def get(link, headers=None):
    req = urllib.request.Request(link, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return exc.code, dict(exc.headers), body


def head(link):
    req = urllib.request.Request(link, method="HEAD")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status, dict(resp.headers)


def raw(port, path, host=None, extra=""):
    """Сырой запрос — нужен для чужого Host и обрыва соединения."""
    conn = socket.create_connection(("127.0.0.1", port), timeout=10)
    request = (f"GET {path} HTTP/1.1\r\nHost: {host or f'127.0.0.1:{port}'}"
               f"\r\n{extra}Connection: close\r\n\r\n")
    conn.sendall(request.encode("ascii"))
    chunks = []
    while True:
        part = conn.recv(65536)
        if not part:
            break
        chunks.append(part)
    conn.close()
    return b"".join(chunks)


def main():
    server = ts.StreamServer()
    source = FakeSource()
    link = server.serve("abc-0", source)
    port = server.port
    print("URL", link, flush=True)

    # 1. HEAD
    status, headers = head(link)
    check("1. HEAD: 200, размер, Accept-Ranges, тип",
          status == 200
          and headers.get("Content-Length") == str(SIZE)
          and headers.get("Accept-Ranges") == "bytes"
          and headers.get("Content-Type") == "video/x-matroska",
          f"{status} {headers.get('Content-Type')}")

    # 2. Весь файл
    status, headers, body = get(link)
    check("2. GET целиком: 200 и байты совпадают",
          status == 200 and body == DATA, f"{status}, {len(body)} байт")

    # 3. Обычный диапазон
    status, headers, body = get(link, {"Range": "bytes=1000-1999"})
    check("3. Range 1000-1999: 206, Content-Range, байты",
          status == 206 and body == DATA[1000:2000]
          and headers.get("Content-Range") == f"bytes 1000-1999/{SIZE}",
          f"{status} {headers.get('Content-Range')}")

    # 4. Суффикс
    status, _, body = get(link, {"Range": "bytes=-1000"})
    check("4. Суффиксный Range -1000", status == 206 and body == DATA[-1000:],
          f"{status}, {len(body)} байт")

    # 5. Открытый диапазон до конца
    start = SIZE - 5000
    status, _, body = get(link, {"Range": f"bytes={start}-"})
    check("5. Range от смещения до конца",
          status == 206 and body == DATA[start:], f"{status}, {len(body)}")

    # 6. За концом файла
    status, headers, _ = get(link, {"Range": f"bytes={SIZE}-"})
    check("6. Range за концом файла: 416 + Content-Range */размер",
          status == 416 and headers.get("Content-Range") == f"bytes */{SIZE}",
          f"{status} {headers.get('Content-Range')}")

    # 7. Мусор в Range игнорируется (RFC 9110)
    status, _, body = get(link, {"Range": "pieces=1-2"})
    check("7. Непонятный Range -> файл целиком, 200",
          status == 200 and len(body) == SIZE, str(status))

    # 8. Чужой токен
    bad = f"http://127.0.0.1:{port}/{'x' * 22}/abc-0/v.mkv"
    status, _, _ = get(bad)
    check("8. Чужой токен -> 404", status == 404, str(status))

    # 9. Чужой Host (защита от DNS rebinding)
    answer = raw(port, f"/{server.token}/abc-0/v.mkv",
                 host="video-downloader.example")
    check("9. Чужой Host -> 403", b"403" in answer.split(b"\r\n")[0],
          answer.split(b"\r\n")[0].decode("ascii", "replace"))

    # 10. Неизвестный ключ
    unknown = f"http://127.0.0.1:{port}/{server.token}/ffff-9/x.mkv"
    status, _, _ = get(unknown)
    check("10. Неизвестный ключ -> 404", status == 404, str(status))

    # 11. Обрыв клиента посреди ответа не ломает сервер
    conn = socket.create_connection(("127.0.0.1", port), timeout=10)
    conn.sendall(f"GET /{server.token}/abc-0/v.mkv HTTP/1.1\r\n"
                 f"Host: 127.0.0.1:{port}\r\n\r\n".encode("ascii"))
    conn.recv(1024)
    conn.close()
    time.sleep(0.3)
    status, _, body = get(link, {"Range": "bytes=0-99"})
    check("11. После обрыва клиента сервер отвечает дальше",
          status == 206 and body == DATA[:100], str(status))

    # 12. keep-alive: два запроса в одном соединении
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    path = f"/{server.token}/abc-0/v.mkv"
    conn.request("GET", path, headers={"Range": "bytes=0-9"})
    first = conn.getresponse().read()
    conn.request("GET", path, headers={"Range": "bytes=10-19"})
    second = conn.getresponse().read()
    conn.close()
    check("12. keep-alive: два запроса в одном соединении",
          first == DATA[:10] and second == DATA[10:20],
          f"{first!r} {second!r}")

    # 13. Параллельные диапазоны
    results = {}

    def fetch(n):
        begin = n * 512 * 1024
        status, _, body = get(link,
                              {"Range": f"bytes={begin}-{begin + 262143}"})
        results[n] = (status, body == DATA[begin:begin + 262144])

    threads = [threading.Thread(target=fetch, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    check("13. Четыре параллельных диапазона",
          len(results) == 4 and all(s == 206 and ok
                                    for s, ok in results.values()),
          str(results))

    # 14. На каждый ответ закрыт свой запрос (снимаются дедлайны)
    check("14. close_request на каждый open_request",
          len(source.opened) == len(source.finished) and source.opened,
          f"открыто {len(source.opened)}, закрыто {len(source.finished)}")

    # 15. Слушаем только loopback
    lan = ""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        lan = probe.getsockname()[0]
        probe.close()
    except OSError:
        pass
    if lan and not lan.startswith("127."):
        try:
            socket.create_connection((lan, port), timeout=3).close()
            reachable = True
        except OSError:
            reachable = False
        check("15. Снаружи loopback порт недоступен", not reachable,
              f"{lan}:{port}")
    else:
        check("15. Снаружи loopback порт недоступен", True,
              "нет внешнего адреса — проверка пропущена")

    # 16. Закрытый источник -> 404 (раздачу удалили во время просмотра)
    source.close()
    status, _, _ = get(link)
    check("16. Закрытый источник -> 404", status == 404, str(status))

    # 17. Источник убран из сервера
    server.drop("abc-0")
    status, _, _ = get(link)
    check("17. После drop() -> 404", status == 404, str(status))

    # 18. Закрытие окна во время ожидания куска: shutdown будит запрос
    blocked = FakeSource(block=True)
    link2 = server.serve("block-0", blocked)
    holder = {}

    def slow():
        # Ответ оборвётся на середине — это и проверяем, traceback не нужен
        try:
            holder["result"] = get(link2, {"Range": "bytes=0-1048575"})
        except Exception as exc:
            holder["result"] = type(exc).__name__

    worker = threading.Thread(target=slow, daemon=True)
    worker.start()
    check("18a. Запрос дошёл до ожидания куска",
          blocked.entered.wait(10), "")
    t0 = time.monotonic()
    seconds = server.shutdown(timeout=1.0)
    elapsed = time.monotonic() - t0
    worker.join(5)
    check("18b. shutdown во время ожидания укладывается в бюджет",
          elapsed < 1.5, f"{elapsed:.2f} с (сервер отчитался {seconds} с)")
    check("18c. Источник закрыт, поток запроса завершился",
          blocked.closed and not worker.is_alive(),
          f"closed={blocked.closed}, alive={worker.is_alive()}")

    # 19. Повторный shutdown безвреден
    again = server.shutdown(timeout=1.0)
    check("19. Повторный shutdown ничего не ломает", again < 1.0,
          f"{again} с")

    # 20. Выбор файла для просмотра
    class F:
        def __init__(self, index, path, size, priority=4):
            self.index, self.path = index, path
            self.size, self.priority = size, priority

    files = (F(0, "Раздача/серия 1.mkv", 700),
             F(1, "Раздача/серия 2.mkv", 900),
             F(2, "Раздача/описание.txt", 5000),
             F(3, "Раздача/большая.avi", 5000, priority=0))
    picked = ts.choose_video_file(files)
    check("20. Смотрим самый большой ВЫБРАННЫЙ видеофайл",
          picked is not None and picked.index == 1,
          getattr(picked, "path", None))
    check("20b. Без видеофайлов — смотреть нечего",
          ts.choose_video_file((F(0, "a/описание.txt", 10),)) is None, "")

    # 21. StreamService поверх поддельного движка
    class FakeEngine:
        def __init__(self):
            self.opened = []
            self.closed = []
            self.source = FakeSource(name="фильм.mp4")

        def open_stream(self, tid, index):
            self.opened.append((tid, index))
            return self.source

        def close_stream(self, tid, index=None):
            self.closed.append((tid, index))
            self.source.close()
            return 1

    engine = FakeEngine()
    service = ts.StreamService(engine, ts.StreamServer())
    watch_url = service.watch("aa" * 20, 3)
    check("21. watch(): поток открыт, URL ведёт на сервер",
          engine.opened == [("aa" * 20, 3)]
          and watch_url.startswith(f"http://127.0.0.1:{service.server.port}/")
          and watch_url.endswith("%D1%84%D0%B8%D0%BB%D1%8C%D0%BC.mp4"),
          watch_url)
    check("21b. active и is_watching",
          service.active == ("aa" * 20, 3)
          and service.is_watching("aa" * 20)
          and not service.is_watching("bb" * 20), str(service.active))
    check("21c. stop(): поток закрыт, active пуст",
          service.stop() and service.active is None
          and engine.closed == [("aa" * 20, 3)], str(engine.closed))
    check("21d. Повторный stop() ничего не делает", not service.stop(), "")
    service.shutdown(timeout=1.0)

    print(f"\nPASS {len(PASS)} / FAIL {len(FAIL)}", flush=True)
    if FAIL:
        print("НЕ ПРОШЛИ:", ", ".join(FAIL), flush=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
