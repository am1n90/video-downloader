"""Этап 0.2/2: скорость скачивания настоящего роя с входящими соединениями
включёнными и выключенными на уровне самого клиента (не брандмауэра
Windows) — вариант A, подтверждён владельцем 16.09.2026 вместо создания
новых правил брандмауэра.

enable_incoming_tcp / enable_incoming_utp = false -> libtorrent сам не
принимает входящие соединения, функционально то же самое, что 0 входящих
из-за блокировки брандмауэром (0.1: 16.7 МБ/с в среднем при Block), но
без системных изменений и без прав администратора. Честное парное
сравнение: тот же процесс, та же раздача, то же время — переключаем
только настройку между прогонами.
"""
import argparse
import os
import shutil
import statistics
import time

import libtorrent as lt

from common import MB, PROTO_TMP, Report, alert_mask, drain_alerts, memory_mb
from probe_metadata import PUBLIC_TRACKERS, magnet

SOURCES = {
    "ubuntu": None,  # заполняется по .torrent, см. main()
    "sintel": "08ada5a7a6183aae1e09d831df6748d566095a10",
    "bbb": "dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c",
}


def make_session(incoming):
    settings = {
        "listen_interfaces": "0.0.0.0:0,[::]:0",
        "enable_dht": True, "enable_lsd": True,
        "enable_upnp": incoming, "enable_natpmp": incoming,
        "enable_incoming_tcp": incoming, "enable_incoming_utp": incoming,
        "alert_mask": alert_mask(),
        "user_agent": "vd-proto/0.1 libtorrent/" + lt.__version__,
    }
    params = lt.session_params()
    params.settings = settings
    return lt.session(params)


def _is_incoming(peer):
    try:
        return not (int(peer.flags) & int(lt.peer_info.local_connection))
    except TypeError:
        return not bool(peer.flags & lt.peer_info.local_connection)


def run(report, uri, incoming, seconds, timeout, save_path):
    # Свежая папка на каждый прогон: иначе клиент находит уже скачанный
    # файл с прошлого прогона (по имени/размеру), have_piece все true,
    # rate остаётся 0.0 МБ/с — не сеть, а состояние на диске
    # (найдено 16.09.2026: 4 прогона подряд в общей папке).
    ses = make_session(incoming)
    atp = lt.parse_magnet_uri(uri)
    atp.save_path = save_path
    os.makedirs(atp.save_path, exist_ok=True)
    h = ses.add_torrent(atp)

    t0 = time.monotonic()
    while not h.status().has_metadata:
        drain_alerts(ses, report)
        if time.monotonic() - t0 > timeout:
            report.record(event="metadata_timeout", incoming=incoming)
            ses.remove_torrent(h)
            return None
        time.sleep(0.1)
    meta_s = round(time.monotonic() - t0, 2)

    t1 = time.monotonic()
    rates, in_counts, peer_counts = [], [], []
    while time.monotonic() - t1 < seconds:
        drain_alerts(ses, report)
        st = h.status()
        peers = h.get_peer_info()
        incoming_n = sum(1 for p in peers if _is_incoming(p))
        rates.append(st.download_payload_rate / MB)
        in_counts.append(incoming_n)
        peer_counts.append(len(peers))
        if int(time.monotonic() - t1) % 10 == 0:
            report.log(f"incoming={incoming} {st.download_payload_rate / MB:.1f} "
                       f"MB/s peers={len(peers)} in={incoming_n}")
        time.sleep(1)
    st = h.status()
    res = {"event": "run", "incoming_enabled": incoming, "metadata_s": meta_s,
           "dl_avg_mb_s": round(statistics.mean(rates), 2),
           "dl_peak_mb_s": round(max(rates), 2),
           "dl_done_mb": round(st.total_payload_download / MB),
           "max_peers": max(peer_counts), "max_incoming": max(in_counts),
           "avg_incoming": round(statistics.mean(in_counts), 1),
           "private_mb": round(memory_mb()[0])}
    report.record(**res)

    t2 = time.monotonic()
    ses.remove_torrent(h, 1)
    while time.monotonic() - t2 < 10:
        if any(type(a).__name__ in ("torrent_deleted_alert",
                                    "torrent_removed_alert")
               for a in ses.pop_alerts()):
            break
        time.sleep(0.1)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="sintel", choices=list(SOURCES))
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--repeats", type=int, default=2,
                    help="пар incoming-on/off (чередуются)")
    args = ap.parse_args()

    report = Report("probe_incoming")
    info_hash = SOURCES[args.source]
    uri = magnet(info_hash, PUBLIC_TRACKERS)
    report.record(event="start", source=args.source, repeats=args.repeats,
                  seconds=args.seconds)

    results = []
    for i in range(args.repeats):
        for incoming in (True, False):
            save_path = os.path.join(
                PROTO_TMP, "swarm-incoming",
                f"{args.source}-{'on' if incoming else 'off'}-{i}-"
                f"{time.strftime('%H%M%S')}")
            res = run(report, uri, incoming, args.seconds, args.timeout,
                     save_path)
            if res:
                results.append(res)
            shutil.rmtree(save_path, ignore_errors=True)

    on = [r["dl_avg_mb_s"] for r in results if r["incoming_enabled"]]
    off = [r["dl_avg_mb_s"] for r in results if not r["incoming_enabled"]]
    report.record(event="summary",
                  on_median=round(statistics.median(on), 2) if on else None,
                  off_median=round(statistics.median(off), 2) if off else None,
                  on_runs=len(on), off_runs=len(off))


if __name__ == "__main__":
    main()
