# -*- coding: utf-8 -*-
"""Диагностика: почему чтение головы файла на рое идёт медленнее закачки.

Первый замер 2.2 (16.09.2026, Sintel): первый байт 3.1 с, а 2 МБ головы
читались ~24 с — при том что вся раздача 123 МБ пришла за 31 с. Вопрос
один: куски головы ЕЩЁ НЕ ПРИШЛИ от роя — или уже пришли, а наш читатель
их не забрал?

Считаем обе стороны на одном прогоне:
  - когда libtorrent считает кусок скачанным (have_piece, опрос 0.2 с);
  - когда наш TorrentStream отдал байты этого куска наружу.
Разница между ними — наша; общее время до have_piece — роя.

Только домашняя машина (настоящий рой), контент — Sintel (Blender).
"""
import os
import shutil
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config

config.save = lambda settings: None

import torrent_engine as te
import torrent_stream as ts

MB = 1024 * 1024
HEAD_MB = 2
INFOHASH = "08ada5a7a6183aae1e09d831df6748d566095a10"
TRACKERS = ["udp://tracker.opentrackr.org:1337/announce",
            "udp://explodie.org:6969/announce"]
BASE = os.path.join(tempfile.gettempdir(), "vd-swarm-diag")


def say(text):
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def main():
    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(BASE, exist_ok=True)
    engine = te.TorrentEngine(data_dir=os.path.join(BASE, "данные"),
                              seed_after_download=False)
    engine.start()
    t0 = time.monotonic()
    tr = "".join(f"&tr={t}" for t in TRACKERS)
    tid = engine.add_magnet(f"magnet:?xt=urn:btih:{INFOHASH}{tr}", BASE)
    while not (engine.get(tid) and engine.get(tid).has_metadata):
        if time.monotonic() - t0 > 120:
            say("метаданные не пришли")
            return 1
        time.sleep(0.1)
    say(f"метаданные за {time.monotonic() - t0:.1f} с")

    item = engine.get(tid)
    target = ts.choose_video_file(item.files)
    stream = engine.open_stream(tid, target.index)
    ti = stream._ti
    piece_len = stream.piece_length
    head_pieces = []
    pos = 0
    while pos < HEAD_MB * MB:
        head_pieces.append(ti.map_file(target.index, pos, 1).piece)
        pos += piece_len
    say(f"файл {target.path} ({target.size / MB:.0f} МБ), "
        f"кусок {piece_len // 1024} КБ, голова — куски {head_pieces}")

    have_at = {}
    stop = threading.Event()

    def watch_have():
        while not stop.wait(0.2):
            for piece in head_pieces:
                if piece not in have_at and stream._have(piece):
                    have_at[piece] = round(time.monotonic() - t1, 2)

    t1 = time.monotonic()
    watcher = threading.Thread(target=watch_have, daemon=True)
    watcher.start()

    # Читаем голову последовательно, как HTTP-обработчик, но запрашиваем
    # ровно по куску: иначе момент «этот кусок отдан» не привязать —
    # чанк отдачи (256 КБ) крупнее куска раздачи (128 КБ)
    rid = stream.open_request()
    read_at = {}
    got = 0
    for n, piece in enumerate(head_pieces):
        start = n * piece_len
        end = min(start + piece_len, HEAD_MB * MB) - 1
        for chunk in stream.iter_range(rid, start, end):
            got += len(chunk)
        read_at[piece] = round(time.monotonic() - t1, 2)
    stream.close_request(rid)
    read_done = time.monotonic() - t1
    stop.set()
    time.sleep(0.3)

    st = engine.get(tid)
    say(f"голова {got / MB:.1f} МБ прочитана за {read_done:.1f} с; "
        f"ожиданий {stream.waits} ({stream.wait_seconds:.1f} с), "
        f"read_piece {stream.piece_reads}; "
        f"раздача скачана на {st.progress * 100:.0f}%, пиров {st.num_peers}")
    print()
    print(f"{'кусок':>8} {'есть у libtorrent, с':>22} {'отдан читателем, с':>20}"
          f" {'наша задержка, с':>18}")
    for piece in head_pieces:
        have = have_at.get(piece)
        read = read_at.get(piece)
        lag = round(read - have, 2) if (have is not None and read is not None) \
            else None
        print(f"{piece:>8} {str(have):>22} {str(read):>20} {str(lag):>18}")

    engine.close_stream(tid, target.index)
    engine.shutdown(timeout=5.0)
    shutil.rmtree(BASE, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
