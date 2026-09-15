"""Этап 0.2: работает ли libtorrent внутри exe (PyInstaller --onedir).

Сессия на 127.0.0.1, создание .torrent из временного файла (путь с
кириллицей), раздача в seed_mode до состояния seeding. Печатает PACK_OK и
выходит с кодом 0; при любой ошибке — traceback и код 1.
"""
import os
import shutil
import sys
import tempfile
import time
import traceback

import libtorrent as lt


def main():
    t0 = time.monotonic()
    work = tempfile.mkdtemp(prefix="vd-pack-probe-")
    try:
        ses = lt.session({"listen_interfaces": "127.0.0.1:0",
                          "enable_dht": False, "enable_lsd": False,
                          "enable_upnp": False, "enable_natpmp": False})
        src = os.path.join(work, "проба файл.bin")
        with open(src, "wb") as f:
            f.write(os.urandom(3 * 1024 * 1024))
        fs = lt.file_storage()
        lt.add_files(fs, src)
        ct = lt.create_torrent(fs, 0, lt.create_torrent.v1_only)
        lt.set_piece_hashes(ct, work)
        torrent = os.path.join(work, "probe.torrent")
        with open(torrent, "wb") as f:
            f.write(lt.bencode(ct.generate()))
        atp = lt.add_torrent_params()
        atp.ti = lt.torrent_info(torrent)
        atp.save_path = work
        atp.flags = atp.flags | lt.torrent_flags.seed_mode
        h = ses.add_torrent(atp)
        deadline = time.monotonic() + 15
        while str(h.status().state) != "seeding":
            if time.monotonic() > deadline:
                raise TimeoutError(f"state={h.status().state}")
            time.sleep(0.1)
        print("PACK_OK", "lt", lt.__version__, "py", sys.version.split()[0],
              "frozen", getattr(sys, "frozen", False),
              "port", ses.listen_port(),
              f"{time.monotonic() - t0:.2f}s", flush=True)
        del h, ses
        return 0
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
