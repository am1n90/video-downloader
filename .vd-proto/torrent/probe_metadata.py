"""Этап 0, замер 1: время получения метаданных по магнет-ссылке (настоящий рой).

Только домашняя машина (решение владельца). Контент — легальный: ISO
дистрибутивов и открытые фильмы Blender. Данные раздач не скачиваются
(торрент удаляется сразу после метаданных), кроме --download-seconds.

Варианты: dht_cold (только xt, чистый DHT), dht_warm (только xt, DHT-состояние
от прошлого удачного прогона), trackers (xt + трекеры, чистый DHT).
"""
import argparse
import gc
import hashlib
import os
import shutil
import statistics
import time
import urllib.parse

import libtorrent as lt

from common import (MB, PROTO_TMP, Report, drain_alerts, make_session,
                    memory_mb, save_dht_state, sha1_hex)

TORRENT_DIR = os.path.join(PROTO_TMP, "torrents")
SWARM_DIR = os.path.join(PROTO_TMP, "swarm")
PUBLIC_TRACKERS = ["udp://tracker.opentrackr.org:1337/announce",
                   "udp://explodie.org:6969/announce"]
DELETE_FILES = getattr(getattr(lt, "options_t", None), "delete_files", None) \
    or getattr(lt.session, "delete_files", 1)


def sources():
    out = []
    for fname, key in (("ubuntu-26.04.1-desktop-amd64.iso.torrent", "ubuntu"),
                       ("debian-13.7.0-amd64-netinst.iso.torrent", "debian")):
        path = os.path.join(TORRENT_DIR, fname)
        if not os.path.isfile(path):
            continue
        ti = lt.torrent_info(path)
        ih = sha1_hex(ti.info_hashes().v1)
        out.append((key, ih, [t.url for t in ti.trackers()]))
    # Открытые фильмы Blender (демо-раздачи WebTorrent)
    out.append(("bbb", "dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c",
                PUBLIC_TRACKERS))
    out.append(("sintel", "08ada5a7a6183aae1e09d831df6748d566095a10",
                PUBLIC_TRACKERS))
    return out


def magnet(ih, trackers):
    uri = "magnet:?xt=urn:btih:" + ih
    for tr in trackers:
        uri += "&tr=" + urllib.parse.quote(tr, safe="")
    return uri


def _is_incoming(peer):
    try:
        return not (int(peer.flags) & int(lt.peer_info.local_connection))
    except TypeError:
        return not bool(peer.flags & lt.peer_info.local_connection)


def swarm_download(report, ses, h, seconds):
    t0 = time.monotonic()
    rates = []
    max_in = max_peers = 0
    peak_private = 0.0
    while time.monotonic() - t0 < seconds:
        drain_alerts(ses, report)
        st = h.status()
        peers = h.get_peer_info()
        incoming = sum(1 for p in peers if _is_incoming(p))
        max_in = max(max_in, incoming)
        max_peers = max(max_peers, len(peers))
        rates.append(st.download_payload_rate / MB)
        peak_private = max(peak_private, memory_mb()[0])
        if int(time.monotonic() - t0) % 10 == 0:
            report.log(f"download {st.download_payload_rate / MB:.1f} MB/s "
                       f"peers={len(peers)} incoming={incoming}")
        time.sleep(1)
    st = h.status()
    return {"dl_seconds": seconds,
            "dl_avg_mb_s": round(statistics.mean(rates), 2),
            "dl_peak_mb_s": round(max(rates), 2),
            "dl_done_mb": round(st.total_payload_download / MB),
            "max_peers_dl": max_peers, "max_incoming": max_in,
            "listen_port": ses.listen_port(),
            "peak_private_mb": round(peak_private)}


def run_once(report, key, uri, variant, timeout, dht_state, download_seconds):
    ses = make_session("0.0.0.0:0,[::]:0", dht=True, dht_state=dht_state)
    atp = lt.parse_magnet_uri(uri)
    atp.save_path = SWARM_DIR
    t0 = time.monotonic()
    h = ses.add_torrent(atp)
    t_peer = t_meta = None
    max_peers = 0
    st = h.status()
    while time.monotonic() - t0 < timeout:
        drain_alerts(ses, report)
        st = h.status()
        elapsed = time.monotonic() - t0
        max_peers = max(max_peers, st.num_peers)
        if t_peer is None and st.num_peers > 0:
            t_peer = elapsed
        if st.has_metadata:
            t_meta = elapsed
            break
        time.sleep(0.1)

    res = {"event": "metadata", "source": key, "variant": variant,
           "ok": t_meta is not None,
           "t_first_peer": None if t_peer is None else round(t_peer, 2),
           "t_metadata": None if t_meta is None else round(t_meta, 2),
           "max_peers": max_peers}
    if t_meta is not None:
        tf = h.torrent_file()
        res.update(name=tf.name(), total_mb=round(tf.total_size() / MB),
                   files=tf.num_files())
        if download_seconds:
            res.update(swarm_download(report, ses, h, download_seconds))
    # dht_state.nodes в python-обёртке 2.1.1 не читается (нет конвертера
    # vector<udp::endpoint>) — сохраняем только буфер состояния
    new_state = save_dht_state(ses)

    t_rm = time.monotonic()
    ses.remove_torrent(h, DELETE_FILES)
    while time.monotonic() - t_rm < 10:
        if any(type(a).__name__ in ("torrent_deleted_alert",
                                    "torrent_delete_failed_alert",
                                    "torrent_removed_alert")
               for a in ses.pop_alerts()):
            break
        time.sleep(0.1)
    res["remove_s"] = round(time.monotonic() - t_rm, 2)
    holder = [ses, h]
    del ses, h
    t_close = time.monotonic()
    holder.clear()
    gc.collect()
    res["close_s"] = round(time.monotonic() - t_close, 2)
    report.record(**res)
    return res, new_state


def summarize(report, results):
    groups = {}
    for r in results:
        groups.setdefault((r["source"], r["variant"]), []).append(r)
    for (source, variant), rs in groups.items():
        metas = [r["t_metadata"] for r in rs if r["ok"]]
        peers = [r["t_first_peer"] for r in rs if r["t_first_peer"] is not None]
        report.record(
            event="summary", source=source, variant=variant,
            ok=f"{len(metas)}/{len(rs)}",
            meta_median=round(statistics.median(metas), 1) if metas else None,
            meta_max=max(metas) if metas else None,
            peer_median=round(statistics.median(peers), 1) if peers else None,
            close_max=max(r["close_s"] for r in rs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--only", default="", help="ubuntu,debian,bbb,sintel")
    ap.add_argument("--variants", default="dht_cold,dht_warm,trackers")
    ap.add_argument("--download-seconds", type=int, default=0,
                    help="после метаданных качать N секунд (1-й прогон "
                         "trackers): скорость, пиры, входящие соединения")
    ap.add_argument("--dead", action="store_true",
                    help="ещё один прогон с несуществующим info-hash")
    args = ap.parse_args()

    report = Report("probe_metadata")
    report.record(event="start", lt=lt.__version__, runs=args.runs,
                  timeout=args.timeout, variants=args.variants)
    os.makedirs(SWARM_DIR, exist_ok=True)
    only = set(filter(None, args.only.split(",")))
    variants = args.variants.split(",")
    results = []
    for key, ih, trackers in sources():
        if only and key not in only:
            continue
        warm_state = None
        for variant in variants:
            if variant == "dht_warm" and warm_state is None:
                report.log(f"{key}: dht_warm пропущен — нет удачного cold")
                continue
            for i in range(args.runs):
                uri = magnet(ih, trackers if variant == "trackers" else [])
                dl = args.download_seconds if (variant == "trackers" and
                                               i == 0) else 0
                res, state = run_once(
                    report, key, uri, variant, args.timeout,
                    warm_state if variant == "dht_warm" else None, dl)
                results.append(res)
                if variant == "dht_cold" and res["ok"] and state:
                    warm_state = state
    if args.dead:
        dead = hashlib.sha1(b"vd-proto dead magnet").hexdigest()
        res, _ = run_once(report, "dead", magnet(dead, []), "dht_cold",
                          args.timeout, None, 0)
        results.append(res)
    summarize(report, results)
    shutil.rmtree(SWARM_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
