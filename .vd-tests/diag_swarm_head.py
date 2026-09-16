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

Сессия 2.3 (находка 33) добавила БЛОЧНЫЙ уровень. Ответ «рой медленный»
2.2 не устроил: голова читалась 16.6 с при том, что вся раздача
приходила за 31 с, а соседние куски были готовы раньше блокирующего.
Поэтому пока читатель стоит на куске, раз в POLL_S пишем, что с этим
куском происходит:
  - сколько пиров в рое ИМЕЮТ его (piece_availability);
  - запрошен ли он вообще и сколько блоков собрано (get_download_queue);
  - У КОГО висят запросы на его блоки и МЕНЯЕТСЯ ли этот набор
    (перезапрашивает ли libtorrent застрявший блок у другого пира);
  - состояние этих пиров: snubbed, remote_choked, длина очереди, скорость.
Плюс спай на set_piece_deadline/piece_priority самого TorrentStream —
что именно мы просили и когда.

Из этого складывается вердикт по каждому простою: кусок не запрошен /
висит у одного пира / ждём последний блок / его почти ни у кого нет.

Только домашняя машина (настоящий рой), контент — открытые фильмы
Blender (Sintel, Big Buck Bunny).

  build-venv\\Scripts\\python.exe .vd-tests\\diag_swarm_head.py
      [--source sintel|bbb] [--head-mb 2]
"""
import argparse
import collections
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
SOURCES = {
    "sintel": "08ada5a7a6183aae1e09d831df6748d566095a10",
    "bbb": "dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c",
}
TRACKERS = ["udp://tracker.opentrackr.org:1337/announce",
            "udp://explodie.org:6969/announce"]
BASE = os.path.join(tempfile.gettempdir(), "vd-swarm-diag")
POLL_S = 0.5            # как часто снимать состояние блокирующего куска
BLOCK_STATES = {0: "нет", 1: "запрошен", 2: "пишется", 3: "готов"}


def say(text):
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


class DeadlineSpy:
    """Прозрачная обёртка над torrent_handle: записывает, что именно
    TorrentStream просил у libtorrent и когда.

    Подменяем handle ТОЛЬКО у потока — движок держит свой экземпляр и
    работает как обычно.
    """

    def __init__(self, handle, t0):
        self._handle = handle
        self._t0 = t0
        self.calls = []                 # (t, метод, кусок, значение)
        self.asked_at = {}              # кусок -> [когда звали read_piece]
        self._lock = threading.Lock()

    def __getattr__(self, name):
        return getattr(self._handle, name)

    def _log(self, method, piece, value=None):
        with self._lock:
            self.calls.append((round(time.monotonic() - self._t0, 2),
                               method, int(piece), value))

    def set_piece_deadline(self, piece, deadline, *args, **kw):
        self._log("deadline", piece, deadline)
        return self._handle.set_piece_deadline(piece, deadline, *args, **kw)

    def reset_piece_deadline(self, piece, *args, **kw):
        self._log("reset", piece)
        return self._handle.reset_piece_deadline(piece, *args, **kw)

    def piece_priority(self, piece, *args, **kw):
        if args:
            self._log("priority", piece, args[0])
        return self._handle.piece_priority(piece, *args, **kw)

    def read_piece(self, piece, *args, **kw):
        self._log("read_piece", piece)
        with self._lock:
            self.asked_at.setdefault(int(piece), []).append(
                round(time.monotonic() - self._t0, 2))
        return self._handle.read_piece(piece, *args, **kw)


def _dq_entry(handle, piece):
    """Запись очереди скачивания по куску (или None, если не запрошен).

    Форму возврата get_download_queue() у обёртки 2.1.1 не угадываем:
    поддерживаем и словари, и объекты.
    """
    try:
        queue = handle.get_download_queue()
    except Exception:
        return None
    for entry in queue:
        index = entry["piece_index"] if isinstance(entry, dict) \
            else getattr(entry, "piece_index", None)
        if index == piece:
            return entry
    return None


def _entry_field(entry, name, default=0):
    if isinstance(entry, dict):
        return entry.get(name, default)
    return getattr(entry, name, default)


def _peer_key(block):
    """ip:port пира, у которого висит блок."""
    peer = _entry_field(block, "peer", None)
    if peer is None:
        return None
    if isinstance(peer, (tuple, list)) and len(peer) >= 2:
        return f"{peer[0]}:{peer[1]}"
    return str(peer)


class HeadProbe:
    """Снимки состояния куска, на котором стоит читатель."""

    def __init__(self, handle, t0):
        self.handle = handle
        self.t0 = t0
        self.samples = collections.defaultdict(list)   # кусок -> [снимок]
        self.shape_dumped = False

    def sample(self, piece, window=()):
        now = round(time.monotonic() - self.t0, 2)
        out = {"t": now}
        # Чем занят рой, пока читатель стоит: НАШИМ куском или остальным
        # окном упреждающего чтения? Если вторым — значит срочность
        # размазана по 17 кускам вместо одного, который держит показ.
        ahead = {"pieces": 0, "requested": 0, "writing": 0}
        try:
            for entry in self.handle.get_download_queue():
                index = _entry_field(entry, "piece_index", -1)
                if index == piece or index not in window:
                    continue
                ahead["pieces"] += 1
                for block in _entry_field(entry, "blocks", []) or []:
                    state = int(_entry_field(block, "state", 0))
                    if state == 1:
                        ahead["requested"] += 1
                    elif state == 2:
                        ahead["writing"] += 1
        except Exception:
            pass
        out["ahead"] = ahead
        try:
            out["have"] = bool(self.handle.have_piece(piece))
        except Exception:
            out["have"] = False
        try:
            avail = self.handle.piece_availability()
            out["avail"] = avail[piece] if piece < len(avail) else -1
        except Exception:
            out["avail"] = -1

        entry = _dq_entry(self.handle, piece)
        if entry is None:
            out["requested"] = False
            out["blocks"] = 0
            out["finished"] = 0
            out["peers"] = []
        else:
            if not self.shape_dumped:
                self.shape_dumped = True
                say(f"форма записи очереди: {entry!r}"[:400])
            blocks = _entry_field(entry, "blocks", []) or []
            out["requested"] = True
            out["blocks"] = len(blocks)
            states = collections.Counter()
            peers = []
            per_block = []
            for n, block in enumerate(blocks):
                state = int(_entry_field(block, "state", 0))
                states[state] += 1
                key = _peer_key(block)
                per_block.append((n, state, key if state else None))
                if state in (1, 2) and key:
                    peers.append(key)
            out["per_block"] = per_block
            out["finished"] = states.get(3, 0)
            out["writing"] = states.get(2, 0)
            out["asked"] = states.get(1, 0)
            out["peers"] = sorted(set(peers))

        # состояние пиров, которые ИМЕЮТ кусок и которые его качают
        # ВНИМАНИЕ: snubbed/remote_choked/choked у peer_info — это
        # ФЛАГОВЫЕ КОНСТАНТЫ класса, а не поля экземпляра. Прочитанные с
        # объекта, они всегда дают ненулевое число (то есть «истину») —
        # первый прогон 2.3 из-за этого показывал, что снаббнуты и
        # заглушены поголовно все пиры. Состояние живёт в info.flags.
        import libtorrent as lt
        pi = lt.peer_info
        have_it = downloading = snubbed = choked_us = 0
        details = []
        try:
            for info in self.handle.get_peer_info():
                flags = int(getattr(info, "flags", 0))
                is_snubbed = bool(flags & int(pi.snubbed))
                is_choked = bool(flags & int(pi.remote_choked))
                try:
                    has = bool(info.pieces[piece])
                except Exception:
                    has = False
                if has:
                    have_it += 1
                    snubbed += is_snubbed
                    choked_us += is_choked
                if getattr(info, "downloading_piece_index", -1) == piece:
                    downloading += 1
                    details.append(
                        f"{info.ip[0]}:{info.ip[1]} "
                        f"{info.down_speed / 1024:.0f} КБ/с "
                        f"очередь {info.download_queue_length} "
                        f"блок {info.downloading_block_index} "
                        f"{info.downloading_progress}/{info.downloading_total} Б"
                        + (" SNUBBED" if is_snubbed else "")
                        + (" CHOKED" if is_choked else ""))
        except Exception:
            pass
        out["have_it"] = have_it
        out["downloading"] = downloading
        out["snubbed"] = snubbed
        out["choked_us"] = choked_us
        out["details"] = details
        self.samples[piece].append(out)
        return out


def block_timeline(samples):
    """Хронология каждого блока куска: у кого он висел и сколько.

    Главный вопрос находки 33: если блок запрошен у медленного пира и не
    приходит, ПЕРЕЗАПРАШИВАЕТ ли его libtorrent у другого — и через
    сколько секунд. Видно по смене пира на одном и том же блоке.
    """
    history = collections.defaultdict(list)     # блок -> [(t, state, peer)]
    for s in samples:
        for n, state, peer in s.get("per_block", []):
            key = (state, peer)
            if not history[n] or history[n][-1][1:] != key:
                history[n].append((s["t"], state, peer))
    out = []
    for n in sorted(history):
        steps = history[n]
        parts = []
        for i, (t, state, peer) in enumerate(steps):
            held = (steps[i + 1][0] - t) if i + 1 < len(steps) \
                else (samples[-1]["t"] - t)
            parts.append(f"{t:.1f}с {BLOCK_STATES.get(state, state)}"
                         + (f" у {peer}" if peer else "")
                         + f" ({held:.1f}с)")
        changes = len({p for _, st, p in steps if p and st == 1})
        out.append(f"блок {n}: " + " -> ".join(parts)
                   + f"   [пиров на запросе: {changes}]")
    return out


def verdict(samples):
    """Кто виноват в простое на куске — по его снимкам."""
    if not samples:
        return "простоя не было"
    if all(s.get("have") for s in samples):
        return ("кусок ВСЁ ВРЕМЯ был у libtorrent — ждали не рой, "
                "а свой путь read_piece -> read_piece_alert")
    if samples[-1].get("have") and not samples[0].get("have"):
        waited_have = [s for s in samples if s.get("have")]
        return (f"кусок пришёл от роя, но простой продолжался ещё "
                f"{len(waited_have)} снимков — часть ожидания наша "
                f"(read_piece)")
    avail = max(s["avail"] for s in samples)
    blocks = max(s["blocks"] for s in samples)
    finished = max(s["finished"] for s in samples)
    requested = [s for s in samples if s["requested"]]
    peer_sets = [tuple(s["peers"]) for s in samples if s["peers"]]
    distinct = {p for s in samples for p in s["peers"]}

    if avail <= 0:
        return f"куска почти ни у кого нет (avail={avail}) — это рой"
    if not requested:
        return (f"кусок НЕ ЗАПРОШЕН вовсе, хотя есть у {avail} пиров — "
                f"это мы/libtorrent, а не рой")
    if blocks and finished >= blocks - 1:
        return (f"ждём ПОСЛЕДНИЙ блок ({finished}/{blocks} готовы) — "
                f"head-of-line, перезапрос последнего блока")
    if len(distinct) == 1 and len(peer_sets) > 1:
        return (f"висит у ОДНОГО пира {next(iter(distinct))} всё время "
                f"простоя, перезапроса у другого не было "
                f"({finished}/{blocks} блоков) — перезапрос не сработал")
    return (f"запрошен у {len(distinct)} пиров, собрано {finished}/{blocks} "
            f"блоков — набор пиров менялся")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=sorted(SOURCES), default="sintel")
    ap.add_argument("--head-mb", type=float, default=2.0)
    args = ap.parse_args()

    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(BASE, exist_ok=True)
    head_bytes = int(args.head_mb * MB)
    engine = te.TorrentEngine(data_dir=os.path.join(BASE, "данные"),
                              seed_after_download=False)
    engine.start()
    t0 = time.monotonic()
    tr = "".join(f"&tr={t}" for t in TRACKERS)
    tid = engine.add_magnet(
        f"magnet:?xt=urn:btih:{SOURCES[args.source]}{tr}", BASE)
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
    # ВАЖНО: файл в многофайловой раздаче обычно начинается НЕ с границы
    # куска. Если нарезать голову ровно по piece_len, каждый диапазон
    # залезает в следующий кусок, и читатель ждёт кусок N+1, а замер
    # записывает это время куску N. Первый прогон 2.3 показал такой сдвиг
    # на единицу (кусок «отдан» ровно тогда, когда приходил следующий), и
    # он же был в замере находки 33. Поэтому границы считаем от реального
    # смещения файла внутри первого куска.
    first_start = ti.map_file(target.index, 0, 1).start
    head_pieces, bounds = [], []
    pos = 0
    while pos < head_bytes:
        head_pieces.append(ti.map_file(target.index, pos, 1).piece)
        end = (piece_len - first_start) + len(bounds) * piece_len - 1
        bounds.append((pos, min(end, head_bytes - 1)))
        pos = end + 1
    say(f"файл начинается со смещения {first_start} Б внутри куска "
        f"{head_pieces[0]} — границы головы сдвинуты на эту величину")
    say(f"файл {target.path} ({target.size / MB:.0f} МБ), "
        f"кусок {piece_len // 1024} КБ, окно чтения {stream.readahead} кусков,"
        f" голова — куски {head_pieces[0]}..{head_pieces[-1]} "
        f"({len(head_pieces)} шт.)")

    t1 = time.monotonic()
    spy = DeadlineSpy(stream._handle, t1)
    stream._handle = spy
    probe = HeadProbe(spy, t1)

    # Байты куска приходят read_piece_alert'ом — засекаем момент, когда
    # они легли в кэш потока. Движок зовёт stream.on_read_piece(alert),
    # то есть подмена на самом объекте потока работает.
    alert_at = {}
    orig_on_read = stream.on_read_piece

    def hooked_on_read(alert):
        alert_at.setdefault(int(alert.piece),
                            round(time.monotonic() - t1, 2))
        return orig_on_read(alert)

    stream.on_read_piece = hooked_on_read

    # Где именно стоит читатель: в ожидании куска (_get_piece) или ещё в
    # подготовке окна (_request_window, а внутри него _raise_head_tail).
    # Обе зовутся как self.<имя> — подмена на объекте работает.
    spans = collections.defaultdict(dict)
    orig_get_piece = stream._get_piece
    orig_window = stream._request_window

    def hooked_get_piece(piece):
        spans[piece]["get_in"] = round(time.monotonic() - t1, 2)
        try:
            return orig_get_piece(piece)
        finally:
            spans[piece]["get_out"] = round(time.monotonic() - t1, 2)

    def hooked_window(rid, piece):
        spans[piece]["win_in"] = round(time.monotonic() - t1, 2)
        try:
            return orig_window(rid, piece)
        finally:
            spans[piece]["win_out"] = round(time.monotonic() - t1, 2)

    stream._get_piece = hooked_get_piece
    stream._request_window = hooked_window

    have_at = {}
    stop = threading.Event()
    waiting = {"piece": None}

    def watch():
        """Один опросчик на обе задачи: когда кусок появился у
        libtorrent и что происходит с тем, которого мы ждём.

        Снимки берём НЕЗАВИСИМО от того, есть ли кусок: первый прогон
        2.3 показал простой 3.6 с на куске, который libtorrent уже имел,
        — прежняя версия такой простой не видела вовсе, потому что
        снимала только отсутствующие куски.
        """
        last = 0.0
        while not stop.wait(0.05):
            now = time.monotonic()
            for piece in head_pieces:
                if piece not in have_at and stream._have(piece):
                    have_at[piece] = round(now - t1, 2)
            piece = waiting["piece"]
            if piece is not None and now - last >= POLL_S:
                last = now
                # Окно упреждающего чтения — те куски, что мы поставили
                # под срок вместе с этим: по ним видно, занят ли рой
                # нашим куском или растащен по остальному окну
                probe.sample(piece,
                             range(piece + 1, piece + stream.readahead + 1))

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()

    # Читаем голову последовательно, как HTTP-обработчик, но запрашиваем
    # ровно по куску: иначе момент «этот кусок отдан» не привязать —
    # чанк отдачи (256 КБ) крупнее куска раздачи (128 КБ)
    rid = stream.open_request()
    read_at = {}
    block_seconds = {}
    got = 0
    for n, piece in enumerate(head_pieces):
        start, end = bounds[n]
        began = time.monotonic()
        missing = not stream._have(piece)
        waiting["piece"] = piece
        for chunk in stream.iter_range(rid, start, end):
            got += len(chunk)
        waiting["piece"] = None
        read_at[piece] = round(time.monotonic() - t1, 2)
        if missing:
            block_seconds[piece] = round(time.monotonic() - began, 2)
    stream.close_request(rid)
    read_done = time.monotonic() - t1
    stop.set()
    time.sleep(0.3)

    st = engine.get(tid)
    say(f"голова {got / MB:.1f} МБ прочитана за {read_done:.1f} с; "
        f"ожиданий {stream.waits} ({stream.wait_seconds:.1f} с), "
        f"read_piece {stream.piece_reads}; "
        f"раздача скачана на {st.progress * 100:.0f}%, пиров {st.num_peers}")

    armed_at = {}
    for t, method, piece, value in spy.calls:
        if method == "deadline":
            armed_at.setdefault(piece, t)

    def diff(a, b):
        return round(a - b, 2) if (a is not None and b is not None) else None

    def fmt(v):
        return "-" if v is None else f"{v:.2f}"

    print()
    print("=== стадии по каждому куску головы (с от начала чтения) ===")
    print("  рой      = от дедлайна до have_piece (ждём сеть)")
    print("  до ask   = от have_piece до нашего read_piece (опрос 0.25 с)")
    print("  alert    = от read_piece до read_piece_alert (диск libtorrent)")
    print()
    print(f"{'кусок':>6} {'дедлайн':>8} {'have':>7} {'ask':>7} {'alert':>7}"
          f" {'отдан':>7} | {'рой':>6} {'до ask':>7} {'alert':>7}"
          f" {'окно':>6} {'_get':>12}")
    swarm_total = ask_total = alert_total = 0.0
    for piece in head_pieces:
        armed = armed_at.get(piece)
        have = have_at.get(piece)
        asked = (spy.asked_at.get(piece) or [None])[0]
        alerted = alert_at.get(piece)
        read = read_at.get(piece)
        swarm = diff(have, armed)
        to_ask = diff(asked, have)
        to_alert = diff(alerted, asked)
        for value, name in ((swarm, "swarm"), (to_ask, "ask"),
                            (to_alert, "alert")):
            if value and value > 0:
                if name == "swarm":
                    swarm_total += value
                elif name == "ask":
                    ask_total += value
                else:
                    alert_total += value
        span = spans.get(piece, {})
        window = diff(span.get("win_out"), span.get("win_in"))
        get_span = (f"{fmt(span.get('get_in'))}->{fmt(span.get('get_out'))}"
                    if span.get("get_in") is not None else "-")
        print(f"{piece:>6} {fmt(armed):>8} {fmt(have):>7} {fmt(asked):>7}"
              f" {fmt(alerted):>7} {fmt(read):>7} | {fmt(swarm):>6}"
              f" {fmt(to_ask):>7} {fmt(to_alert):>7}"
              f" {fmt(window):>6} {get_span:>12}")
    print(f"\nсумма по стадиям: рой {swarm_total:.2f} с, "
          f"до ask {ask_total:.2f} с, read_piece->alert {alert_total:.2f} с")

    print()
    print("=== дедлайны, которые ставил TorrentStream ===")
    for t, method, piece, value in spy.calls[:40]:
        print(f"  {t:>6.2f} с  {method:<9} кусок {piece:<6} {value}")
    if len(spy.calls) > 40:
        print(f"  ... всего вызовов {len(spy.calls)}")

    print()
    print("=== что происходило с кусками, на которых стоял читатель ===")
    for piece in head_pieces:
        samples = probe.samples.get(piece)
        if not samples:
            continue
        print(f"\nкусок {piece}: простой {block_seconds.get(piece, 0)} с, "
              f"снимков {len(samples)}")
        print(f"  ВЕРДИКТ: {verdict(samples)}")
        for s in samples:
            print(f"    {s['t']:>6.2f} с  у пиров {s['have_it']:>3} "
                  f"(avail {s['avail']:>3}, snubbed {s['snubbed']}, "
                  f"choked {s['choked_us']})  "
                  f"запрошен={s['requested']} "
                  f"блоки {s.get('finished', 0)}готовы/"
                  f"{s.get('writing', 0)}пишутся/{s.get('asked', 0)}ждём "
                  f"из {s['blocks']}  качают {s['downloading']}  "
                  f"| остальное окно: кусков {s['ahead']['pieces']}, "
                  f"блоков ждём {s['ahead']['requested']}, "
                  f"пишется {s['ahead']['writing']}")
            for line in s["details"]:
                print(f"        {line}")
        print("  по блокам (кто держит и меняется ли):")
        for line in block_timeline(samples):
            print(f"    {line}")

    engine.close_stream(tid, target.index)
    engine.shutdown(timeout=5.0)
    shutil.rmtree(BASE, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
