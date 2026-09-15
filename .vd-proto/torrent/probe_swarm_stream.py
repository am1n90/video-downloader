"""Этап 0.2: просмотр во время закачки из НАСТОЯЩЕГО роя — только домашняя
машина (решение владельца). Контент — открытые фильмы Blender (демо-раздачи
WebTorrent): магнет-ссылка -> метаданные -> Range-сервер -> mpv/VLC.

Метрики те же, что в probe_players.py (первый кадр, перемотка на 80%,
подвисания), плюс время метаданных, пиры, скорость роя и закрытие сессии.
"""
import argparse
import os
import shutil
import time

from common import MB, Report
import probe_players as pp
from probe_metadata import PUBLIC_TRACKERS, magnet
import stream_server as ss

SOURCES = {
    "sintel": "08ada5a7a6183aae1e09d831df6748d566095a10",
    "bbb": "dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="sintel,bbb")
    ap.add_argument("--player", choices=["mpv", "vlc"], default="mpv")
    ap.add_argument("--watch-seconds", type=int, default=60)
    ap.add_argument("--after-seek-seconds", type=int, default=20)
    args = ap.parse_args()

    report = Report("probe_swarm_stream")
    os.makedirs(pp.LOG_DIR, exist_ok=True)
    for n, key in enumerate(args.only.split(",")):
        save = ss.new_save_dir(f"swarm-{key}")
        streamer = ss.open_magnet(report, magnet(SOURCES[key], PUBLIC_TRACKERS),
                                  save)
        srv = None
        try:
            meta_s = streamer.wait_metadata()
            idx = streamer.choose_file(None)
            size = streamer.prepare_file(idx)
            rel = streamer.ti.files().file_path(idx)
            ext = os.path.splitext(rel)[1].lower()
            srv, token = ss.start_server(streamer, report)
            url = ss.url_for(srv, token, streamer.ti, idx)
            report.log(f"=== {key}: {rel} ({size // MB} MB), metadata {meta_s} s")
            log_path = os.path.join(pp.LOG_DIR, f"swarm-{key}-{args.player}-"
                                                f"{time.strftime('%H%M%S')}.log")
            t = time.monotonic()
            if args.player == "mpv":
                res = pp.run_mpv(url, ext == ".mkv", args.watch_seconds,
                                 args.after_seek_seconds, log_path, 100 + n)
            else:
                res = pp.run_vlc(url, ext == ".mkv", args.watch_seconds,
                                 args.after_seek_seconds, log_path)
            st = streamer.h.status()
            done = streamer.h.file_progress()[idx]
            report.record(event="scenario", source=key, player=args.player,
                          file=rel, size_mb=round(size / MB),
                          metadata_s=meta_s,
                          wall_s=round(time.monotonic() - t, 1),
                          file_done_pct=round(100 * done / size, 1),
                          rate_mb_s=round(st.download_payload_rate / MB, 2),
                          peers=st.num_peers,
                          http=dict(streamer.counters,
                                    bytes_mb=round(streamer.counters["bytes"]
                                                   / MB, 1)),
                          log=log_path, **res)
        except Exception as exc:  # сценарий упал — фиксируем и дальше
            report.record(event="scenario_failed", source=key, error=repr(exc))
        finally:
            if srv is not None:
                srv.shutdown()
            report.record(event="closed", source=key,
                          close_s=streamer.close(),
                          alerts=report.alert_counts())
            shutil.rmtree(save, ignore_errors=True)


if __name__ == "__main__":
    main()
