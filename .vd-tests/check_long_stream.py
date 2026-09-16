# -*- coding: utf-8 -*-
"""Долгий просмотр РЕАЛЬНО недокачанного файла + замер подвисаний картинки.

Закрывает два пробела сессии 2.2 сразу.

1. «Долгий просмотр недокачанного файла не проверялся». Sintel и BBB
   докачиваются за 30-60 с, и все прежние замеры пришлись на этот
   короткий отрезок: дальше плеер читал готовый файл с диска, а не
   поток. Вместо поиска раздачи больше домашнего канала (это несколько
   ГБ и невоспроизводимо) ограничиваем скорость движка чуть выше
   битрейта видео — тогда фронт закачки всё время идёт немного впереди
   головы воспроизведения, и КАЖДОЕ чтение попадает в недокачанное.
   Скрипт это не предполагает, а ДОКАЗЫВАЕТ: пишет запас (сколько
   мегабайт скачано впереди позиции плеера) на каждом снимке и честно
   помечает прогон негодным, если файл успел докачаться целиком.

2. «Подвисания КАРТИНКИ не мерились» — считались ожидания кусков внутри
   TorrentStream и отсутствие ошибок в логе плеера, а не то, замирало ли
   изображение. Здесь меряем сам показ: mpv через IPC отдаёт time-pos,
   и подвисание — это когда стенные часы идут, а позиция
   воспроизведения стоит. Плюс собственный признак mpv
   paused-for-cache (плеер сам говорит, что ему нечего показывать).

Заодно это первая живая проверка mpv (до 2.3 вживую гоняли только VLC).

Только домашняя машина (настоящий рой). Контент — открытые фильмы
Blender.

  build-venv\\Scripts\\python.exe .vd-tests\\check_long_stream.py
      --source sintel --limit-kb 180 --minutes 10
"""
import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config

config.save = lambda settings: None

import player
import torrent_engine as te
import torrent_stream as ts

MB = 1024 * 1024
OUT = os.path.join(tempfile.gettempdir(), "vd-long-stream")
TRACKERS = ["udp://tracker.opentrackr.org:1337/announce",
            "udp://explodie.org:6969/announce"]
SOURCES = {
    "sintel": "08ada5a7a6183aae1e09d831df6748d566095a10",
    "bbb": "dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c",
}
# Портативный mpv из прототипа Этапа 0.2 — на этой машине плееры не
# установлены, player.resolve() их не находит.
PROTO_MPV = os.path.join(tempfile.gettempdir(), "vd-torrent-proto",
                         "players", "mpv", "mpv.exe")
POLL_S = 0.25
# Подвисание: стенные часы ушли на STALL_WALL, а позиция показа почти не
# двинулась. Порог по позиции не ноль — time-pos у mpv дискретен по
# кадрам, и на 0.25 с он может не измениться на ровном месте.
STALL_WALL = 0.6
STALL_POS = 0.15

PASS, FAIL = [], []


def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details, flush=True)


def say(text):
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


class MpvIPC:
    """Клиент JSON IPC mpv поверх именованного канала Windows.

    Нужен ровно для одного: спрашивать позицию показа и почему он стоит.

    Строго «запрос — ответ», В ОДНОЙ НИТИ и без фонового читателя. Так
    не из аккуратности, а по необходимости: первая версия читала канал
    отдельной нитью, и прогон намертво вставал через минуту. Причина —
    свойство Windows: у синхронного (не overlapped) дескриптора
    блокирующий ReadFile задерживает и WriteFile по тому же
    дескриптору. Пока mpv слал события, читатель то и дело
    разблокировался и запись успевала пройти; как только показ
    устаканивался и события прекращались, читатель вставал в ReadFile
    навсегда, а следующая команда не могла записаться. Событий mpv
    нам не нужно, поэтому просто читаем ответ сразу за своей командой.
    """

    def __init__(self, pipe_name):
        self.path = rf"\\.\pipe\{pipe_name}"
        self.pipe = None
        self._buf = b""
        self.dead = False
        self.skipped_events = 0

    def connect(self, timeout=30.0):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                self.pipe = open(self.path, "r+b", buffering=0)
                break
            except OSError as exc:          # mpv ещё не создал канал
                last = exc
                time.sleep(0.2)
        if self.pipe is None:
            raise RuntimeError(f"mpv IPC не открылся за {timeout} с: {last!r}")
        return True

    def _read_line(self):
        while b"\n" not in self._buf:
            try:
                chunk = self.pipe.read(4096)
            except Exception:
                self.dead = True
                return None
            if not chunk:                   # mpv закрыл канал
                self.dead = True
                return None
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line.strip()

    def get(self, prop, tries=40):
        """Значение свойства mpv или None.

        tries ограничивает, сколько чужих строк (событий) готовы
        пропустить, прежде чем сдаться: своя команда всегда получает
        ответ, но перед ним может прийти пачка событий.
        """
        if self.dead or self.pipe is None:
            return None
        payload = json.dumps({"command": ["get_property", prop],
                              "request_id": 1}) + "\n"
        try:
            self.pipe.write(payload.encode("utf-8"))
        except Exception:
            self.dead = True
            return None
        for _ in range(tries):
            line = self._read_line()
            if line is None:
                return None
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if msg.get("event"):
                self.skipped_events += 1
                continue
            if msg.get("error") != "success":
                return None
            return msg.get("data")
        return None

    def close(self):
        try:
            if self.pipe is not None:
                self.pipe.close()
        except Exception:
            pass


def contiguous_ahead_mb(stream, pos_bytes):
    """Сколько мегабайт скачано ПОДРЯД начиная с позиции показа.

    Общий объём скачанного для стриминга обманчив: движок нарочно тянет
    вперёд всего голову и хвост по 8 МБ (moov/cues), и они попадают в
    «скачано», хотя до них показу ещё час. Плееру же нужен непрерывный
    кусок прямо перед позицией — его и меряем.
    """
    try:
        if pos_bytes >= stream.size:
            return 0.0
        first = stream._ti.map_file(stream.index, int(pos_bytes), 1).piece
        total = stream._ti.num_pieces()
        last = first
        while last < total and stream._have(last):
            last += 1
        return (last - first) * stream.piece_length / MB
    except Exception:
        return None


def stalls_from(samples):
    """Подвисания картинки: часы идут, позиция показа стоит.

    Меряем ВРЕМЯ С МОМЕНТА, когда позиция последний раз сдвинулась, а не
    разницу соседних снимков. Первая версия сравнивала соседей и
    требовала разрыв >= 0.6 с МЕЖДУ НИМИ — при опросе раз в 0.25 с такое
    условие почти никогда не выполняется, и прогон, где показ отставал
    от реального времени в полтора раза, показал «подвисаний 0».
    Замирание короче интервала опроса так и останется незамеченным, но
    всё, что дольше порога, теперь видно независимо от частоты опроса.
    """
    episodes = []
    last_move_t = None
    last_pos = None
    for s in samples:
        pos = s["pos"]
        if pos is None:
            continue
        if last_pos is None:
            last_move_t, last_pos = s["t"], pos
            continue
        if pos - last_pos >= STALL_POS:
            gap = s["t"] - last_move_t
            if gap >= STALL_WALL:
                episodes.append({
                    "from": round(last_move_t, 2), "to": round(s["t"], 2),
                    "seconds": round(gap, 2), "pos": round(last_pos, 1),
                    "cache": bool(s.get("paused_for_cache")),
                    "done_mb": s.get("done_mb"),
                    "ahead_mb": s.get("ahead_mb"),
                })
            last_move_t, last_pos = s["t"], pos
    # Хвост: показ мог встать и не двинуться до конца прогона
    if last_move_t is not None and samples:
        gap = samples[-1]["t"] - last_move_t
        if gap >= STALL_WALL:
            episodes.append({
                "from": round(last_move_t, 2), "to": round(samples[-1]["t"], 2),
                "seconds": round(gap, 2), "pos": round(last_pos, 1),
                "cache": bool(samples[-1].get("paused_for_cache")),
                "done_mb": samples[-1].get("done_mb"),
                "ahead_mb": samples[-1].get("ahead_mb"),
            })
    return episodes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=sorted(SOURCES), default="sintel")
    ap.add_argument("--limit-kb", type=int, default=180,
                    help="ограничение скорости движка, КБ/с (0 — без него)")
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--mpv", default=PROTO_MPV)
    ap.add_argument("--window", action="store_true",
                    help="показывать окно плеера (по умолчанию без вывода)")
    args = ap.parse_args()

    if not os.path.isfile(args.mpv):
        print(f"mpv не найден: {args.mpv}")
        return 2
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)

    extra = {}
    if args.limit_kb:
        extra["download_rate_limit"] = args.limit_kb * 1024
    engine = te.TorrentEngine(data_dir=os.path.join(OUT, "данные"),
                              seed_after_download=False,
                              extra_settings=extra)
    engine.start()
    # Ограничение — основа всего замера: если оно не применилось, файл
    # докачается за полминуты и прогон снова окажется про готовый файл,
    # как в 2.2. Проверяем не намерение, а то, что реально стоит в
    # сессии (один прогон 2.3 показал скорость выше лимита, причину
    # найти не удалось — поэтому проверка осталась в скрипте).
    applied = engine._ses.get_settings().get("download_rate_limit")
    check("ограничение скорости применено к сессии",
          applied == args.limit_kb * 1024,
          f"в сессии {applied} Б/с, просили {args.limit_kb * 1024} Б/с")
    service = ts.StreamService(engine)
    mpv = proc = None
    result = {"source": args.source, "limit_kb": args.limit_kb,
              "minutes": args.minutes}
    try:
        t0 = time.monotonic()
        tr = "".join(f"&tr={t}" for t in TRACKERS)
        tid = engine.add_magnet(
            f"magnet:?xt=urn:btih:{SOURCES[args.source]}{tr}", OUT)
        while not (engine.get(tid) and engine.get(tid).has_metadata):
            if time.monotonic() - t0 > 120:
                check("метаданные получены", False, "не пришли за 120 с")
                return 1
            time.sleep(0.1)
        check("метаданные получены", True,
              f"{time.monotonic() - t0:.1f} с")

        item = engine.get(tid)
        target = ts.choose_video_file(item.files)
        url = service.watch(tid, target.index)
        stream = engine._streams[(tid, target.index)]
        size_mb = target.size / MB
        say(f"{target.path} ({size_mb:.0f} МБ), ограничение "
            f"{args.limit_kb} КБ/с, смотрим {args.minutes} мин")

        pipe_name = f"vd-mpv-{os.getpid()}"
        log_path = os.path.join(OUT, "mpv.log")
        cmd = [args.mpv, url,
               f"--input-ipc-server={pipe_name}",
               "--no-config", "--no-terminal",
               f"--log-file={log_path}", "--msg-level=all=v",
               "--keep-open=no", "--ao=null"]
        if not args.window:
            # Картинку всё равно декодируем и держим реальный темп —
            # проверяем это ниже по ходу времени показа.
            cmd.append("--vo=null")
        t_launch = time.monotonic()
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess,
                                                      "CREATE_NO_WINDOW", 0))
        mpv = MpvIPC(pipe_name)
        mpv.connect(timeout=45)
        check("mpv отозвался по IPC", True,
              f"{time.monotonic() - t_launch:.1f} с после запуска")

        # Ждём долго нарочно: при ограничении скорости mpv разбирает
        # контейнер около минуты — он читает начало файла через наш
        # поток, а поток идёт со скоростью роя, урезанной лимитом.
        duration = None
        t_parse = time.monotonic()
        while time.monotonic() - t_parse < 180:
            duration = mpv.get("duration")
            if duration or proc.poll() is not None:
                break
            time.sleep(0.5)
        check("mpv разобрал контейнер", bool(duration),
              f"длительность {duration} за {time.monotonic() - t_parse:.0f} с"
              if duration else "duration не пришла за 180 с")
        result["parse_s"] = round(time.monotonic() - t_parse, 1)
        result["duration"] = duration
        if duration:
            bitrate_kb = target.size / duration / 1024
            result["bitrate_kb"] = round(bitrate_kb)
            say(f"битрейт файла {bitrate_kb:.0f} КБ/с, ограничение "
                f"{args.limit_kb} КБ/с "
                f"({args.limit_kb / bitrate_kb:.2f}x)")

        samples = []
        t_watch = time.monotonic()
        deadline = t_watch + args.minutes * 60
        complete_at = None
        last_say = 0.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                say(f"mpv завершился сам (код {proc.returncode})")
                break
            now = time.monotonic()
            pos = mpv.get("time-pos")
            st = engine.get(tid)
            # file_progress отдаёт СПИСОК байт по файлам раздачи
            progress_by_file = engine.file_progress(tid)
            done_bytes = progress_by_file[target.index] \
                if target.index < len(progress_by_file) else 0
            sample = {
                "t": round(now - t_watch, 2),
                "pos": pos,
                "paused_for_cache": mpv.get("paused-for-cache"),
                "cache_time": mpv.get("demuxer-cache-time"),
                "done_mb": round(done_bytes / MB, 1),
                "progress": round(st.progress, 3) if st else None,
                "rate_kb": round(st.download_rate / 1024) if st else None,
            }
            if pos is not None and duration:
                played_mb = pos / duration * size_mb
                sample["ahead_mb"] = round(sample["done_mb"] - played_mb, 1)
                buffered = contiguous_ahead_mb(stream,
                                               pos / duration * target.size)
                sample["buffered_mb"] = round(buffered, 1) \
                    if buffered is not None else None
            samples.append(sample)
            if st and st.progress >= 1.0 and complete_at is None:
                complete_at = sample["t"]
            if now - last_say >= 60:
                last_say = now
                say(f"    {sample['t']:.0f} с: показ {pos and round(pos)} с, "
                    f"скачано {sample['done_mb']} МБ "
                    f"({(sample['progress'] or 0) * 100:.0f}%), "
                    f"подряд впереди показа "
                    f"{sample.get('buffered_mb')} МБ, "
                    f"{sample['rate_kb']} КБ/с, "
                    f"ожиданий {stream.waits} ({stream.wait_seconds:.0f} с)")
            time.sleep(POLL_S)

        watched = time.monotonic() - t_watch
        positions = [s["pos"] for s in samples if s["pos"] is not None]
        played = (max(positions) - min(positions)) if positions else 0
        result["watched_s"] = round(watched, 1)
        result["played_s"] = round(played, 1)
        result["samples"] = len(samples)

        # --- проверка 1: показ шёл в реальном темпе -------------------
        # Без этого весь замер бессмысленнен: если mpv с --vo=null гонит
        # файл как может, «подвисаний» не будет по построению.
        ratio = played / watched if watched else 0
        result["play_ratio"] = round(ratio, 2)
        # Годность замера: mpv НЕ должен гнать быстрее реального времени
        # (иначе подвисания невозможны по построению). А вот отставание —
        # это уже результат, а не поломка стенда, и отдельной строкой.
        check("mpv не гнал быстрее реального времени", ratio <= 1.15,
              f"проиграно {played:.0f} с за {watched:.0f} с ({ratio:.2f}x)")
        say(f"темп показа {ratio:.2f}x реального времени"
            + ("" if ratio >= 0.97 else " — показ ОТСТАЁТ от часов"))

        # --- проверка 2: файл ВСЁ ВРЕМЯ был недокачан -----------------
        aheads = [s["buffered_mb"] for s in samples
                  if s.get("buffered_mb") is not None]
        result["complete_at_s"] = complete_at
        result["ahead_median_mb"] = round(statistics.median(aheads), 1) \
            if aheads else None
        result["ahead_max_mb"] = max(aheads) if aheads else None
        last_progress = samples[-1]["progress"] if samples else None
        check("файл оставался недокачанным весь просмотр",
              complete_at is None,
              f"скачано к концу {(last_progress or 0) * 100:.0f}%, "
              f"подряд впереди показа: медиана "
              f"{result['ahead_median_mb']} МБ, "
              f"максимум {result['ahead_max_mb']} МБ"
              if complete_at is None else
              f"докачался целиком на {complete_at} с — прогон НЕ показателен,"
              f" нужен меньший --limit-kb")

        # --- проверка 3: подвисания картинки --------------------------
        episodes = stalls_from(samples)
        total_stall = round(sum(e["seconds"] for e in episodes), 1)
        longest = max((e["seconds"] for e in episodes), default=0)
        result["stalls"] = len(episodes)
        result["stall_seconds"] = total_stall
        result["stall_longest_s"] = longest
        result["stall_episodes"] = episodes[:40]
        say(f"подвисаний картинки {len(episodes)}, суммарно {total_stall} с, "
            f"самое долгое {longest} с")
        for e in episodes[:15]:
            say(f"    на {e['from']:.0f} с показа стояло {e['seconds']} с "
                f"(позиция {e['pos']:.0f} с, запас {e.get('ahead_mb')} МБ, "
                f"mpv paused-for-cache: {e['cache']})")
        # Порога «сколько подвисаний допустимо» не выдумываем: цифра
        # сама по себе и есть результат замера. Проверяем только то, что
        # имеет однозначный ответ — что показ вообще шёл.
        check("картинка двигалась большую часть времени",
              total_stall < watched * 0.5,
              f"стояла {total_stall} с из {watched:.0f} с")

        result["waits"] = stream.waits
        result["wait_seconds"] = round(stream.wait_seconds, 2)
        stats = service.stats()
        result["served_mb"] = round(stats.bytes / MB, 1) if stats else None
        say(f"поток: ожиданий кусков {stream.waits} "
            f"({stream.wait_seconds:.1f} с), отдано "
            f"{result['served_mb']} МБ")

        # --- проверка 4: ошибки чтения потока в логе плеера -----------
        # Только настоящие ошибки плеера: в mpv уровень сообщения стоит
        # в строке как [e]/[fatal]. Поиск слова «error» по всему логу с
        # --msg-level=all=v ловит строку с параметрами сборки mpv и даёт
        # ложную тревогу (так и вышло в первом прогоне).
        errors = []
        if os.path.isfile(log_path):
            with open(log_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "][e]" in line or "][fatal]" in line:
                        errors.append(line.strip())
        result["player_errors"] = errors[:20]
        check("в логе плеера нет ошибок чтения потока",
              not errors, f"строк с ошибками {len(errors)}"
              + (f"; первая: {errors[0][:120]}" if errors else ""))
    finally:
        if mpv is not None:
            mpv.close()
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        service.shutdown(timeout=2.0)
        engine.shutdown(timeout=5.0)
        path = os.path.join(OUT, f"result-{time.strftime('%H%M%S')}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        say(f"сырые данные: {path}")

    print()
    print(f"ИТОГ: PASS {len(PASS)}, FAIL {len(FAIL)}")
    for name in FAIL:
        print("  FAIL:", name)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
