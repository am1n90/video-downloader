"""Этап 0: локальный сид по 127.0.0.1 — детерминированные тесты без интернета.

Создаёт .torrent (v1) из папки/файла, если его ещё нет, и раздаёт его на
127.0.0.1:<port>. Строка "READY" в выводе — сид готов принимать соединения.

  python local_seed.py --src "%TEMP%/vd-torrent-proto/seed/Тест раздача"
"""
import argparse
import os
import time

import libtorrent as lt

from common import MB, PROTO_TMP, Report, drain_alerts, make_session

DEFAULT_SRC = os.path.join(PROTO_TMP, "seed", "Тест раздача")
DEFAULT_TORRENT = os.path.join(PROTO_TMP, "torrents", "local-test.torrent")


def create_torrent(src, torrent_path, report):
    fs = lt.file_storage()
    lt.add_files(fs, src)
    ct = lt.create_torrent(fs, 0, lt.create_torrent.v1_only)
    ct.set_creator("vd-proto")
    t = time.monotonic()
    lt.set_piece_hashes(ct, os.path.dirname(os.path.abspath(src)))
    hashing_s = time.monotonic() - t
    os.makedirs(os.path.dirname(torrent_path), exist_ok=True)
    with open(torrent_path, "wb") as f:
        f.write(lt.bencode(ct.generate()))
    report.record(event="torrent_created", path=torrent_path,
                  hashing_s=round(hashing_s, 1),
                  piece_kb=ct.piece_length() // 1024,
                  pieces=ct.num_pieces(), files=fs.num_files(),
                  total_mb=round(fs.total_size() / MB))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--torrent", default=DEFAULT_TORRENT)
    ap.add_argument("--port", type=int, default=6890)
    ap.add_argument("--up-limit", type=int, default=0, help="байт/с, 0 = без")
    ap.add_argument("--seconds", type=int, default=0, help="0 = бесконечно")
    args = ap.parse_args()

    report = Report("local_seed")
    if not os.path.isfile(args.torrent):
        create_torrent(args.src, args.torrent, report)

    ses = make_session(f"127.0.0.1:{args.port}", local_only=True)
    atp = lt.add_torrent_params()
    atp.ti = lt.torrent_info(args.torrent)
    atp.save_path = os.path.dirname(os.path.abspath(args.src))
    atp.flags = atp.flags | lt.torrent_flags.seed_mode
    h = ses.add_torrent(atp)
    if args.up_limit:
        h.set_upload_limit(args.up_limit)

    t0 = time.monotonic()
    ready = False
    last = 0.0
    while not args.seconds or time.monotonic() - t0 < args.seconds:
        drain_alerts(ses, report)
        st = h.status()
        if not ready and str(st.state) == "seeding":
            ready = True
            report.log(f"READY port={ses.listen_port()} name={st.name}")
        now = time.monotonic() - t0
        if now - last >= 5:
            report.log(f"{st.state} peers={st.num_peers} "
                       f"up={st.upload_payload_rate / MB:.1f} MB/s "
                       f"uploaded={st.total_payload_upload / MB:.0f} MB")
            last = now
        time.sleep(0.2)


if __name__ == "__main__":
    main()
