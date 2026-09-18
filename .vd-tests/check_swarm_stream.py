# -*- coding: utf-8 -*-
"""Замеры просмотра на НАСТОЯЩЕМ рое — только домашняя машина (сессия 2.2).

Мерим КОД ПРОГРАММЫ, а не прототип: тот же torrent_engine, тот же
torrent_stream.StreamService, те же настройки, что в приложении. GUI не
поднимаем — он к скорости отношения не имеет.

Что меряем и почему именно это. Средняя скорость для стриминга —
плохая метрика (находки 13 и 15: среднее за окно врёт, а на быстрой
раздаче файл докачивается раньше, чем «проигрывается»). Стриминг — это
«нужный кусок вовремя», и прямой счётчик этого у нас уже есть:
TorrentStream.waits / wait_seconds — сколько раз и сколько секунд чтение
ждало недостающий кусок. Плюс перемотки на случайные ещё не скачанные
позиции: там кусок заведомо нужен «сейчас», и замер не разваливается,
даже если раздача качается быстро.

Условия сравнения (--conditions):
  base       — настройки как в приложении сейчас
  reconnect  — min_reconnect_time=5 вместо 60 (находка 18)
  noincoming — enable_incoming_tcp/utp=false (находка 13, вариант А:
               без брандмауэра и прав администратора)

Правило решения записано ДО прогонов: менять умолчание только если
условие сокращает ожидания кусков и задержку перемоток больше, чем
разброс между прогонами ОДНОГО условия, и на обеих раздачах.

Порядок условий в каждом круге чередуется (base,rec,noinc / noinc,rec,
base): рой живёт своей жизнью, и при одном и том же порядке последнему
условию систематически доставалась бы другая сеть.

Папка раздачи на КАЖДЫЙ прогон своя и удаляется после: иначе прогон
досиживает уже скачанное и цифры получаются ложные (находка 13).

Контент — только открытые фильмы Blender (Sintel, Big Buck Bunny).

  build-venv\\Scripts\\python.exe .vd-tests\\check_swarm_stream.py
      [--only sintel,bbb] [--runs 4] [--seeks 8] [--conditions base,...]

Итог — таблица в консоли и JSON в %TEMP%\\vd-swarm-2.2\\results-*.json.
"""
import argparse
import json
import os
import random
import shutil
import statistics
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config

config.save = lambda settings: None

import torrent_engine as te
import torrent_stream as ts

MB = 1024 * 1024
OUT = os.path.join(tempfile.gettempdir(), "vd-swarm-2.2")
TRACKERS = ["udp://tracker.opentrackr.org:1337/announce",
            "udp://explodie.org:6969/announce"]
SOURCES = {
    "sintel": "08ada5a7a6183aae1e09d831df6748d566095a10",
    "bbb": "dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c",
}
CONDITIONS = {
    "base": {},
    "reconnect": {"min_reconnect_time": 5},
    "noincoming": {"enable_incoming_tcp": False,
                   "enable_incoming_utp": False},
    # --- сессия 2.3, по причине простоя из diag_swarm_head.py ----------
    # Диагностика 16.09.2026: читатель стоит не из-за медленного роя, а
    # ждёт ПОСЛЕДНИЙ блок 16 КБ из куска — 6-7 блоков из 8 приходят
    # сразу, один висит, и libtorrent перебирает под него пиров по
    # 1.6-3.2 с на попытку. Отсюда три ручки и одна своя правка.
    #
    # strict_end_game_mode=False разрешает просить ОДИН И ТОТ ЖЕ блок у
    # нескольких пиров, не дожидаясь конца всей раздачи — ровно наш
    # случай «остался один блок».
    "endgame": {"strict_end_game_mode": False},
    # piece_timeout — сколько libtorrent терпит молчащего пира по куску
    # со сроком. Замер показал перезапросы и без того чаще 20 с, так что
    # ждём малого, но проверить дёшево.
    "piecetimeout": {"piece_timeout": 4},
    # request_queue_time задаёт глубину очереди запросов к пиру. В
    # диагностике у пиров было 4-34 запроса впереди нашего блока при
    # 6-10 КБ/с — это head-of-line: короче очередь, ближе срочный блок.
    "queuetime": {"request_queue_time": 1},
    # Наша правка (без изменения движка — приставкой, см. Rearmer):
    # пока читатель стоит, раз в секунду напоминаем libtorrent о сроке
    # блокирующего куска.
    "rearm": {"_rearm": 1.0},
    "endgame_rearm": {"strict_end_game_mode": False, "_rearm": 1.0},
    # --- сессия 2.9 (18.09.2026), находка 62 ----------------------------
    # Закрытие окна упирается в разрушение сессии libtorrent: 0.02 с, если
    # uTP-соединений к этому моменту нет, и 0.71 с, если есть (A/B на
    # приложении: с выключенным uTP 0 медленных прогонов из 6, с
    # включённым — 3 из 6). Выключить uTP совсем — не оптимизация, а
    # ФУНКЦИОНАЛЬНОЕ решение: часть пиров в рое доступна только по uTP.
    # Здесь меряем его цену на настоящем рое.
    #
    # ПРАВИЛО РЕШЕНИЯ ЗАПИСАНО ДО ПРОГОНОВ (как в 2.2 и 2.3): uTP
    # выключаем только если на ОБЕИХ раздачах медиана времени чтения
    # головы 2 МБ у noutp не хуже base больше, чем размах между
    # прогонами одного условия, И число пиров не падает заметно. Иначе
    # оставляем как есть, а секунду закрытия записываем как свойство
    # libtorrent.
    "noutp": {"enable_incoming_utp": False, "enable_outgoing_utp": False},
}

# Сколько читаем «как плеер» перед перемотками и сколько ждём куски.
# Голову держим маленькой нарочно: пока её читаешь, раздача качается, и
# на быстром рое «ещё не скачанных» позиций попросту не остаётся
HEAD_MB = 2
SEEK_BYTES = 512 * 1024
METADATA_TIMEOUT = 120
SEEK_TIMEOUT = 60


def magnet(infohash):
    tr = "".join(f"&tr={t}" for t in TRACKERS)
    return f"magnet:?xt=urn:btih:{infohash}{tr}"


def say(text):
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def fetch(url, start, count, timeout):
    """Время до первого байта и сколько успели прочитать."""
    request = urllib.request.Request(
        url, headers={"Range": f"bytes={start}-{start + count - 1}"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            first = resp.read(16 * 1024)
            ttfb = time.monotonic() - t0
            rest = resp.read()
            return {"ttfb": round(ttfb, 2), "bytes": len(first) + len(rest),
                    "status": resp.status, "error": ""}
    except Exception as exc:
        return {"ttfb": round(time.monotonic() - t0, 2), "bytes": 0,
                "status": 0, "error": type(exc).__name__}


class Rearmer:
    """Приставка к потоку: пока читатель стоит на куске, напоминать
    libtorrent о его сроке.

    Нарочно НЕ правка torrent_engine.py: сперва доказываем замером, что
    эффект есть, и только потом переносим в движок. Блокирующий кусок
    вычисляем так же, как его видит читатель — самый младший ЕЩЁ НЕ
    скачанный кусок из тех, что под сроком (чтение строго
    последовательное, значит именно он держит показ).

    Считаем и работу: сколько раз переармировали и сколько раз при этом
    кусок был тот же самый, — иначе по итогу не отличить «помогло» от
    «просто не пригодилось».
    """

    def __init__(self, stream, every=1.0):
        self.stream = stream
        self.every = every
        self.rearms = 0
        self.same_piece_streak = 0
        self._last = None
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _blocking_piece(self):
        stream = self.stream
        with stream._cond:
            armed = set().union(*stream._windows.values()) \
                if stream._windows else set()
        for piece in sorted(armed):
            if not stream._have(piece):
                return piece
        return None

    def _loop(self):
        while not self._stop.wait(self.every):
            if self.stream._closed:
                return
            piece = self._blocking_piece()
            if piece is None:
                self._last = None
                continue
            if piece == self._last:
                self.same_piece_streak += 1
            self._last = piece
            try:
                self.stream._handle.set_piece_deadline(piece, 0)
                self.rearms += 1
            except Exception:
                pass


class Run:
    """Один прогон: своя папка, свой движок, свой сервер."""

    def __init__(self, key, condition, seeks):
        self.key = key
        self.condition = condition
        self.seeks = seeks
        self.save_dir = os.path.join(
            OUT, f"{key}-{condition}-{int(time.time() * 1000) % 100000}")
        self.data_dir = os.path.join(self.save_dir, "данные")
        self.engine = None
        self.service = None
        self.samples = []           # (секунда, скорость, пиры)
        self.rearm_every = 0.0
        self.rearmer = None
        self._stop = threading.Event()

    # ------------------------------------------------------------ жизнь

    def start_engine(self):
        extra = dict(CONDITIONS[self.condition])
        # ключи на «_» — наши, не libtorrent: их движку отдавать нельзя
        self.rearm_every = extra.pop("_rearm", 0.0)
        self.engine = te.TorrentEngine(data_dir=self.data_dir,
                                       seed_after_download=False,
                                       extra_settings=extra)
        self.engine.start()
        self.service = ts.StreamService(self.engine)

    def sample_loop(self, tid):
        t0 = time.monotonic()
        while not self._stop.wait(1.0):
            item = self.engine.get(tid)
            if item is None:
                continue
            self.samples.append((round(time.monotonic() - t0, 1),
                                 item.download_rate, item.num_peers))

    def close(self):
        self._stop.set()
        if self.rearmer is not None:
            self.rearmer.stop()
        try:
            if self.service is not None:
                self.service.shutdown(timeout=2.0)
        except Exception:
            pass
        try:
            if self.engine is not None:
                self.engine.shutdown(timeout=5.0)
        except Exception:
            pass
        shutil.rmtree(self.save_dir, ignore_errors=True)

    # ------------------------------------------------------------ замер

    def execute(self):
        result = {"source": self.key, "condition": self.condition,
                  "started": time.strftime("%H:%M:%S")}
        self.start_engine()
        t0 = time.monotonic()
        tid = self.engine.add_magnet(magnet(SOURCES[self.key]),
                                     self.save_dir)
        ok = self.wait(lambda: (self.engine.get(tid) or None)
                       and self.engine.get(tid).has_metadata,
                       METADATA_TIMEOUT)
        result["metadata_s"] = round(time.monotonic() - t0, 2)
        if not ok:
            result["error"] = "метаданные не пришли"
            return result
        item = self.engine.get(tid)
        target = ts.choose_video_file(item.files)
        if target is None:
            result["error"] = "в раздаче нет видеофайла"
            return result
        result["file"] = os.path.basename(target.path)
        result["size_mb"] = round(target.size / MB)

        sampler = threading.Thread(target=self.sample_loop, args=(tid,),
                                   daemon=True)
        sampler.start()

        url = self.service.watch(tid, target.index)
        stream = self.engine._streams[(tid, target.index)]
        if self.rearm_every:
            self.rearmer = Rearmer(stream, self.rearm_every)
            self.rearmer.start()

        # 1. Начало файла — то же, что делает плеер, открывая поток.
        # Время чтения головы и есть главная метрика «кусок вовремя»:
        # последовательное чтение упирается в САМЫЙ МЕДЛЕННЫЙ из первых
        # кусков, и пока он не придёт, остальные, уже скачанные, лежат
        # без дела (диагностика diag_swarm_head.py, 16.09.2026).
        t_head = time.monotonic()
        head = fetch(url, 0, HEAD_MB * MB, SEEK_TIMEOUT)
        result["first_byte_s"] = head["ttfb"]
        result["head_s"] = round(time.monotonic() - t_head, 2)
        result["head_mb"] = round(head["bytes"] / MB, 1)
        result["head_error"] = head["error"]

        # 2. Перемотки на позиции, которых ЕЩЁ НЕТ на диске: только там
        # кусок нужен «сейчас». Первый прогон 16.09.2026 показал, зачем
        # это: Sintel (123 МБ) при 40 пирах скачивается за полминуты, и
        # случайная позиция попадала в уже готовое — 0.01 с, замер ни о
        # чём. Ищем холодную позицию перебором, а если её уже нет —
        # честно останавливаемся и пишем, сколько успели.
        rnd = random.Random(f"{self.key}-{self.condition}")
        seeks = []
        for n in range(self.seeks):
            start = self.cold_offset(stream, target, rnd)
            if start is None:
                say(f"    холодных позиций не осталось "
                    f"(скачано {self.percent(tid, target)}%) — "
                    f"перемоток сделано {len(seeks)}")
                break
            at_percent = round(start * 100 / target.size)
            downloaded = self.percent(tid, target)
            got = fetch(url, start, SEEK_BYTES, SEEK_TIMEOUT)
            got["at_percent"] = at_percent
            got["downloaded_percent"] = downloaded
            seeks.append(got)
            say(f"    перемотка {n + 1}/{self.seeks}: на {at_percent}% "
                f"(скачано {downloaded}%) -> {got['ttfb']} с"
                + (f" ({got['error']})" if got["error"] else ""))
        result["seeks"] = seeks
        result["seeks_done"] = len(seeks)
        good = [s["ttfb"] for s in seeks if not s["error"]]
        result["seek_median_s"] = round(statistics.median(good), 2) if good \
            else None
        result["seek_max_s"] = round(max(good), 2) if good else None
        result["seek_failed"] = sum(1 for s in seeks if s["error"])

        # 3. Прямой счётчик «кусок не пришёл вовремя»
        result["waits"] = stream.waits
        result["wait_seconds"] = round(stream.wait_seconds, 2)
        if self.rearmer is not None:
            result["rearms"] = self.rearmer.rearms
            result["rearm_same_piece"] = self.rearmer.same_piece_streak
        result["piece_reads"] = stream.piece_reads

        item = self.engine.get(tid)
        result["peers_max"] = max((p for _, _, p in self.samples), default=0)
        rates = [r for _, r, _ in self.samples if r]
        result["rate_median_mb"] = round(
            statistics.median(rates) / MB, 2) if rates else 0
        result["progress_percent"] = round(item.progress * 100, 1)
        result["elapsed_s"] = round(time.monotonic() - t0, 1)
        return result

    def cold_offset(self, stream, target, rnd, tries=60):
        """Случайное смещение в файле, кусок которого ещё НЕ скачан.

        Держимся подальше от головы (её движок тянет первой) и от самого
        хвоста: там свой приоритет, и это была бы не перемотка.
        """
        low, high = HEAD_MB * MB, target.size - SEEK_BYTES * 2
        if high <= low:
            return None
        for _ in range(tries):
            start = rnd.randrange(low, high)
            piece = stream._ti.map_file(target.index, start, 1).piece
            if not stream._have(piece):
                return start
        return None

    def percent(self, tid, target):
        try:
            done = self.engine.file_progress(tid)[target.index]
        except Exception:
            return 0
        return round(done * 100 / target.size)

    def wait(self, pred, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if pred():
                    return True
            except Exception:
                pass
            time.sleep(0.1)
        return False


def summarize(rows):
    """Сводка по (раздача, условие). Показываем медиану И размах: по
    размаху между прогонами одного условия и судим, есть ли разница."""
    keys = sorted({(r["source"], r["condition"]) for r in rows
                   if not r.get("error")})
    print()
    head = "голова 2 МБ, с (медиана | размах)"
    print(f"{'раздача':8} {'условие':11} {'прог':>4} {'1-й байт':>9} "
          f"{head:>34} {'перемотка':>10} {'пиров':>6}")
    table = []
    for source, condition in keys:
        mine = [r for r in rows
                if r["source"] == source and r["condition"] == condition
                and not r.get("error")]
        firsts = [r["first_byte_s"] for r in mine]
        heads = [r["head_s"] for r in mine if r.get("head_s")]
        seek_meds = [r["seek_median_s"] for r in mine
                     if r["seek_median_s"] is not None]
        waits = [r["wait_seconds"] for r in mine]
        peers = [r["peers_max"] for r in mine]
        line = {
            "source": source, "condition": condition, "runs": len(mine),
            "first_byte_median": round(statistics.median(firsts), 2),
            "head_median": round(statistics.median(heads), 2) if heads else None,
            # Размах между прогонами ОДНОГО условия — та планка, которую
            # разница между условиями должна перебить, чтобы что-то значить
            "head_spread": (round(min(heads), 2), round(max(heads), 2))
            if heads else None,
            "seek_median": round(statistics.median(seek_meds), 2)
            if seek_meds else None,
            "wait_seconds_median": round(statistics.median(waits), 2),
            "peers_median": round(statistics.median(peers)) if peers else 0,
        }
        table.append(line)
        cell = f"{line['head_median']} | {line['head_spread']}"
        print(f"{source:8} {condition:11} {line['runs']:>4} "
              f"{line['first_byte_median']:>9} {cell:>34} "
              f"{str(line['seek_median']):>10} {line['peers_median']:>6}")
    return table


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="sintel,bbb")
    ap.add_argument("--conditions", default="base,reconnect,noincoming")
    ap.add_argument("--runs", type=int, default=4)
    # Перемоток мало нарочно: на 123 МБ при живом рое «ещё не скачанных»
    # позиций хватает на одну-две, дальше файл уже целиком на диске
    ap.add_argument("--seeks", type=int, default=2)
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    sources = [s for s in args.only.split(",") if s in SOURCES]
    conditions = [c for c in args.conditions.split(",") if c in CONDITIONS]
    rows = []
    total = len(sources) * len(conditions) * args.runs
    n = 0
    for source in sources:
        for circle in range(args.runs):
            # Чередуем порядок условий: рой меняется со временем
            order = conditions if circle % 2 == 0 else list(reversed(conditions))
            for condition in order:
                n += 1
                say(f"прогон {n}/{total}: {source}, {condition}, "
                    f"круг {circle + 1}")
                run = Run(source, condition, args.seeks)
                try:
                    row = run.execute()
                except Exception as exc:
                    row = {"source": source, "condition": condition,
                           "error": f"{type(exc).__name__}: {exc}"}
                finally:
                    run.close()
                row["circle"] = circle + 1
                rows.append(row)
                if row.get("error"):
                    say(f"    ОШИБКА: {row['error']}")
                else:
                    say(f"    метаданные {row['metadata_s']} с, "
                        f"1-й байт {row['first_byte_s']} с, "
                        f"голова {row['head_s']} с, "
                        f"перемотки медиана {row['seek_median_s']} с, "
                        f"ожиданий {row['waits']} "
                        f"({row['wait_seconds']} с), "
                        f"пиров {row['peers_max']}")

    path = os.path.join(OUT, f"results-{time.strftime('%Y%m%d-%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "summary": summarize(rows)}, f,
                  ensure_ascii=False, indent=2)
    print()
    say(f"сырые данные: {path}")
    failed = [r for r in rows if r.get("error")]
    if failed:
        say(f"прогонов с ошибкой: {len(failed)} из {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
