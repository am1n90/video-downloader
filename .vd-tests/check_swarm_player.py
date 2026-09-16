# -*- coding: utf-8 -*-
"""Просмотр с НАСТОЯЩИМ плеером из НАСТОЯЩЕГО роя (сессия 2.2).

Инструмент Б к check_swarm_stream.py: тот меряет чтение синтетическим
читателем (много прогонов, сравнение настроек), а этот один раз проходит
весь путь программы целиком — движок, StreamService, player.resolve и
живой VLC/mpv, — чтобы закрыть «просмотр 2.1 на настоящем рое не
проверялся».

Плеер запускаем без окна (dummy у VLC, null у mpv): проверяем не
картинку, а что он читает поток и не спотыкается. Меряем:
  - метаданные, первый запрос плеера к нашему серверу (это и есть окно
    «готовим плеер» из 2.2), первые байты;
  - подвисания: ожидания недостающих кусков в TorrentStream за время
    просмотра (waits / wait_seconds) — прямой счётчик «кусок не пришёл
    вовремя», в отличие от средней скорости (находки 13 и 15);
  - перемотку на 80% и время до байтов после неё;
  - ошибки чтения потока в логе плеера.

Только домашняя машина. Контент — открытые фильмы Blender.

  build-venv\\Scripts\\python.exe .vd-tests\\check_swarm_player.py
      [--source sintel|bbb] [--watch-seconds 180]
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config

config.save = lambda settings: None

import player
import torrent_engine as te
import torrent_stream as ts

MB = 1024 * 1024
OUT = os.path.join(tempfile.gettempdir(), "vd-swarm-player")
TRACKERS = ["udp://tracker.opentrackr.org:1337/announce",
            "udp://explodie.org:6969/announce"]
SOURCES = {
    "sintel": "08ada5a7a6183aae1e09d831df6748d566095a10",
    "bbb": "dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c",
}

PASS, FAIL = [], []


def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details, flush=True)


def say(text):
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def wait_until(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def launch_headless(exe, url, log_path):
    name = os.path.basename(exe).lower()
    if name.startswith("mpv"):
        cmd = [exe, "--no-config", "--vo=null", "--ao=null", "--idle=no",
               "--keep-open=no", f"--log-file={log_path}",
               "--msg-level=all=v", url]
    else:
        cmd = [exe, "-I", "dummy", "--dummy-quiet", "--vout=dummy",
               "--aout=dummy", "--no-video-title-show", "--file-logging",
               f"--logfile={log_path}", "--verbose=2", url]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="sintel", choices=sorted(SOURCES))
    ap.add_argument("--watch-seconds", type=int, default=180)
    args = ap.parse_args()

    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)
    log_path = os.path.join(OUT, "player.log")

    try:
        exe = player.resolve("")
        check("плеер найден", True, f"{player.label_for(exe)}: {exe}")
    except player.PlayerNotFound as exc:
        check("плеер найден", False, str(exc))
        return 1

    engine = te.TorrentEngine(data_dir=os.path.join(OUT, "данные"),
                              seed_after_download=False)
    engine.start()
    service = ts.StreamService(engine)
    proc = None
    try:
        t0 = time.monotonic()
        tr = "".join(f"&tr={t}" for t in TRACKERS)
        tid = engine.add_magnet(
            f"magnet:?xt=urn:btih:{SOURCES[args.source]}{tr}", OUT)
        ok = wait_until(lambda: (engine.get(tid) or None)
                        and engine.get(tid).has_metadata, 120)
        check("метаданные раздачи получены", ok,
              f"{time.monotonic() - t0:.1f} с")
        if not ok:
            return 1
        item = engine.get(tid)
        target = ts.choose_video_file(item.files)
        check("в раздаче найден видеофайл", target is not None,
              getattr(target, "path", None))
        if target is None:
            return 1

        url = service.watch(tid, target.index)
        stream = engine._streams[(tid, target.index)]
        t_launch = time.monotonic()
        proc = launch_headless(exe, url, log_path)

        # Окно «готовим плеер»: сколько прошло до первого его запроса
        ok = wait_until(lambda: (service.stats() or None)
                        and service.stats().requests > 0, 90)
        stats = service.stats()
        prepare_s = time.monotonic() - t_launch
        check("плеер обратился к нашему серверу", ok, f"{prepare_s:.1f} с")
        ok = wait_until(lambda: service.stats().bytes > 2 * MB, 90)
        check("плеер читает поток (первые мегабайты)", ok,
              f"{service.stats().bytes / MB:.1f} МБ за "
              f"{time.monotonic() - t_launch:.1f} с")

        say(f"смотрим {args.watch_seconds} с…")
        end = time.monotonic() + args.watch_seconds
        last_say = 0
        marks = []
        while time.monotonic() < end:
            time.sleep(1.0)
            st = engine.get(tid)
            now = time.monotonic() - t_launch
            marks.append((round(now), stream.waits,
                          round(stream.wait_seconds, 1)))
            if now - last_say >= 30:
                last_say = now
                say(f"    {now:.0f} с: отдано "
                    f"{service.stats().bytes / MB:.0f} МБ, скачано "
                    f"{st.progress * 100:.0f}%, пиров {st.num_peers}, "
                    f"ожиданий {stream.waits} ({stream.wait_seconds:.1f} с)")
            if proc.poll() is not None:
                say("плеер завершился сам")
                break

        waits_total, wait_seconds = stream.waits, stream.wait_seconds
        check("за просмотр не набралось долгих ожиданий кусков",
              wait_seconds < args.watch_seconds * 0.25,
              f"{waits_total} ожиданий, {wait_seconds:.1f} с за "
              f"{args.watch_seconds} с просмотра")

        # Перемотка на 80%: плеер рвёт соединение и открывает новое —
        # ровно так же, как это делает живой пользователь
        st = engine.get(tid)
        check("раздача при просмотре качалась",
              st.progress > 0, f"{st.progress * 100:.0f}%")
        import urllib.request
        start = int(target.size * 0.8)
        request = urllib.request.Request(
            url, headers={"Range": f"bytes={start}-{start + 524287}"})
        t_seek = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=60) as resp:
                got = len(resp.read(16 * 1024))
            seek_s = time.monotonic() - t_seek
            check("перемотка на 80% отдала байты", got > 0,
                  f"{seek_s:.1f} с")
        except Exception as exc:
            check("перемотка на 80% отдала байты", False,
                  f"{type(exc).__name__}")

        if os.path.isfile(log_path):
            with open(log_path, "rb") as f:
                tail = f.read()[-8000:].decode("utf-8", "replace")
            NOISE = ("d3d11va", "dxva", "direct3d", "vout", "faad", "avcodec")
            bad = [line for line in tail.splitlines()
                   if " error: " in line.lower()
                   and not any(m in line.lower() for m in NOISE)]
            check("в логе плеера нет ошибок чтения потока", not bad,
                  " | ".join(bad[:3]) if bad else
                  f"лог {os.path.getsize(log_path)} байт")
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(5)
            except Exception:
                pass
        t_close = time.monotonic()
        service.shutdown(timeout=2.0)
        engine.shutdown(timeout=5.0)
        check("закрытие во время просмотра — в бюджете",
              time.monotonic() - t_close < 7.0,
              f"{time.monotonic() - t_close:.2f} с")

    print()
    print(f"ИТОГ: {len(PASS)} PASS, {len(FAIL)} FAIL")
    say(f"лог плеера: {log_path}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
