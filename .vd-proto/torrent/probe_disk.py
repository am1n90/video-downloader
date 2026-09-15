"""Этап 0, замер 2: дисковый движок libtorrent на локальном сиде (127.0.0.1).

Режимы:
  full    — полное скачивание без ограничения скорости: память (Private /
            Working Set), sparse-файлы, чтение недокачанного файла другим
            процессом во время записи, сверка с источником, освобождение
            файлов после закрытия сессии
  resume  — kill на ~50% -> перезапуск с fastresume -> перезапуск без
            fastresume на готовых файлах (полная перепроверка)
  select  — file_priorities: скачать только один файл раздачи
  locked  — целевой файл занят чужим дескриптором (share=0): ошибка и
            восстановление после освобождения
Сид запускается отдельно: local_seed.py (тот же .torrent, --seed-port).
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import gc
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import time

import libtorrent as lt

from common import (MB, PROTO_TMP, Report, alert_message, drain_alerts,
                    files_on_disk,
                    make_session, memory_mb, sha1_hex, sys_memory_mb)

DEFAULT_TORRENT = os.path.join(PROTO_TMP, "torrents", "local-test.torrent")
DEFAULT_SEED_ROOT = os.path.join(PROTO_TMP, "seed")

READER = r'''
import hashlib, json, sys
spec = json.loads(sys.argv[1])
h = hashlib.sha1()
for path, off, size in spec:
    with open(path, "rb") as f:
        f.seek(off)
        data = f.read(size)
    if len(data) != size:
        print("SHORT", len(data), size)
        sys.exit(2)
    h.update(data)
print(h.hexdigest())
'''

DONE_STATES = ("seeding", "finished")


def add_leech(ses, ti, save, seed_port, resume=None, priorities=None):
    atp = lt.read_resume_data(resume) if resume else lt.add_torrent_params()
    atp.ti = ti
    atp.save_path = save
    if priorities is not None:
        atp.file_priorities = priorities
    h = ses.add_torrent(atp)
    h.connect_peer(("127.0.0.1", seed_port))
    return h


def status_error(st):
    errc = getattr(st, "errc", None)
    msg = errc.message() if errc is not None and errc.value() else ""
    return msg, getattr(st, "error_file", None)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 * MB), b""):
            h.update(chunk)
    return h.hexdigest()


def compare_with_source(ti, save, seed_root, only=None):
    out = []
    fs = ti.files()
    for i in range(ti.num_files()):
        rel = fs.file_path(i)
        if only is not None and i not in only:
            continue
        got = os.path.join(save, rel)
        src = os.path.join(seed_root, rel)
        same = os.path.isfile(got) and sha256_file(got) == sha256_file(src)
        out.append({"file": rel, "identical": same})
    return out


def read_piece_externally(ti, save, piece):
    fs = ti.files()
    spec = [(os.path.join(save, fs.file_path(s.file_index)), s.offset, s.size)
            for s in ti.map_block(piece, 0, ti.piece_size(piece))]
    r = subprocess.run([sys.executable, "-c", READER, json.dumps(spec)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    ok = r.returncode == 0 and r.stdout.strip() == sha1_hex(
        ti.hash_for_piece(piece))
    return ok, (r.stdout.strip() + " " + r.stderr.strip())[-300:]


def check_random_piece(h, ti, save):
    """Кусок с have_piece -> читаем ДРУГИМ процессом и сверяем SHA-1."""
    candidates = random.sample(range(ti.num_pieces()),
                               min(300, ti.num_pieces()))
    have = [p for p in candidates if h.have_piece(p)]
    if not have:
        return None
    piece = random.choice(have)
    t = time.monotonic()
    ok, detail = read_piece_externally(ti, save, piece)
    res = {"piece": piece, "ok": ok, "read_s": round(time.monotonic() - t, 3)}
    if not ok:
        res["detail"] = detail
        time.sleep(2)
        res["ok_after_2s"], _ = read_piece_externally(ti, save, piece)
    return res


def close_session_timed(holder):
    t = time.monotonic()
    holder.clear()
    gc.collect()
    return round(time.monotonic() - t, 2)


def reset_dir(path):
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)


# ---------------------------------------------------------------- full

def mode_full(args, ti, report):
    base = os.path.join(PROTO_TMP, "leech full")
    save = os.path.join(base, "Папка с пробелами и кириллицей")
    reset_dir(base)
    os.makedirs(save)
    ses = make_session("127.0.0.1:0", local_only=True)
    h = add_leech(ses, ti, save, args.seed_port)

    t0 = time.monotonic()
    last_sample = last_check = last_connect = 0.0
    peak_private = peak_ws = 0.0
    min_avail = None
    checks = []
    while True:
        drain_alerts(ses, report)
        st = h.status()
        now = time.monotonic() - t0
        if st.num_peers == 0 and now - last_connect > 2:
            h.connect_peer(("127.0.0.1", args.seed_port))
            last_connect = now
        if now - last_sample >= 1:
            priv, ws, _ = memory_mb()
            avail, load = sys_memory_mb()
            peak_private = max(peak_private, priv)
            peak_ws = max(peak_ws, ws)
            min_avail = avail if min_avail is None else min(min_avail, avail)
            rate = st.download_payload_rate / MB
            report.log(f"{st.state} {st.progress * 100:5.1f}% {rate:6.1f} "
                       f"MB/s private={priv:.0f} ws={ws:.0f} "
                       f"avail={avail:.0f} load={load}%")
            report.record(quiet=True, event="sample",
                          progress=round(st.progress, 4),
                          rate_mb_s=round(rate, 1), private_mb=round(priv),
                          ws_mb=round(ws), avail_mb=round(avail), load=load)
            last_sample = now
        if now - last_check >= 5 and st.progress < 1:
            c = check_random_piece(h, ti, save)
            if c:
                c["progress"] = round(st.progress, 3)
                checks.append(c)
                report.record(event="concurrent_read", **c)
            report.record(event="sparse", progress=round(st.progress, 3),
                          files=files_on_disk(save))
            last_check = now
        if st.progress >= 1 and str(st.state) in DONE_STATES:
            break
        if now > args.timeout:
            report.record(event="timeout", progress=st.progress)
            break
        time.sleep(0.2)

    total_s = time.monotonic() - t0
    report.record(event="full_done", seconds=round(total_s, 1),
                  avg_mb_s=round(ti.total_size() / MB / total_s, 1),
                  peak_private_mb=round(peak_private),
                  peak_ws_mb=round(peak_ws),
                  min_avail_mb=round(min_avail or 0),
                  reads_ok=sum(1 for c in checks if c["ok"]),
                  reads_total=len(checks),
                  alerts=report.alert_counts())
    report.record(event="files_after_download", files=files_on_disk(save))

    # Освобождает ли libtorrent файлы: пока сессия жива и после закрытия
    fs = ti.files()
    target = os.path.join(save, fs.file_path(0))
    report.record(event="rename_while_session", ok=_try_rename(target))
    holder = [ses, h]
    del ses, h
    report.record(event="session_closed", close_s=close_session_timed(holder))
    report.record(event="rename_after_close", ok=_try_rename(target))
    report.record(event="compare_with_source",
                  files=compare_with_source(ti, save, args.seed_root))


def _try_rename(path):
    try:
        os.rename(path, path + ".renamed")
        os.rename(path + ".renamed", path)
        return True
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


# -------------------------------------------------------------- resume

def emit(**data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


def child(args):
    ti = lt.torrent_info(args.torrent)
    ses = make_session("127.0.0.1:0", local_only=True)
    resume = None
    if args.use_resume and os.path.isfile(args.resume_file):
        with open(args.resume_file, "rb") as f:
            resume = f.read()
    h = add_leech(ses, ti, args.save, args.seed_port, resume=resume)
    if args.limit:
        h.set_download_limit(args.limit)
    flags = getattr(lt.save_resume_flags_t, "save_info_dict", 0)

    t0 = time.monotonic()
    saves = 0
    last_req = last_print = last_connect = 0.0
    while True:
        for a in ses.pop_alerts():
            name = type(a).__name__
            if isinstance(a, lt.save_resume_data_alert):
                tmp = args.resume_file + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(lt.write_resume_data_buf(a.params))
                os.replace(tmp, args.resume_file)
                saves += 1
            elif any(w in name for w in ("error", "failed", "rejected")):
                emit(alert=name, message=alert_message(a))
        st = h.status()
        now = time.monotonic() - t0
        if st.num_peers == 0 and now - last_connect > 2 and \
                str(st.state) == "downloading":
            h.connect_peer(("127.0.0.1", args.seed_port))
            last_connect = now
        if now - last_req >= 2:
            h.save_resume_data(flags)
            last_req = now
        if now - last_print >= 0.5:
            emit(t=round(now, 2), state=str(st.state),
                 progress=round(st.progress, 4), resume_saves=saves)
            last_print = now
        if st.progress >= 1 and str(st.state) in DONE_STATES:
            emit(t=round(now, 2), state=str(st.state), progress=1.0,
                 resume_saves=saves, done=True)
            break
        time.sleep(0.1)


def run_child(args, save, resume_file, use_resume, report, label,
              kill_at=None):
    cmd = [sys.executable, "-u", os.path.abspath(__file__), "child",
           "--torrent", args.torrent, "--seed-port", str(args.seed_port),
           "--save", save, "--resume-file", resume_file,
           "--limit", str(args.limit)]
    if use_resume:
        cmd.append("--use-resume")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    t0 = time.monotonic()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace",
                         env=env)
    states = {}
    first_active = None
    last = None
    killed = None
    for line in p.stdout:
        try:
            d = json.loads(line)
        except ValueError:
            report.log(f"{label}: {line.rstrip()}")
            continue
        if "alert" in d:
            report.record(event="child_alert", label=label, **d)
            continue
        last = d
        states.setdefault(d["state"], d["t"])
        if first_active is None and d["state"] in ("downloading",) + \
                DONE_STATES:
            first_active = d
        if kill_at is not None and d["progress"] >= kill_at and \
                d["resume_saves"] >= 1:
            resume_age = time.time() - os.path.getmtime(resume_file)
            p.kill()
            killed = dict(d, resume_age_s=round(resume_age, 2))
            break
        if d.get("done"):
            break
    p.wait(timeout=60)
    res = {"label": label, "wall_s": round(time.monotonic() - t0, 1),
           "states_first_seen_s": states, "first_active": first_active,
           "last": last, "killed": killed, "exit": p.returncode}
    report.record(event="child_run", **res)
    return res


def mode_resume(args, ti, report):
    base = os.path.join(PROTO_TMP, "leech resume")
    save = os.path.join(base, "Папка резюме")
    resume_file = os.path.join(base, "state.fastresume")
    reset_dir(base)
    os.makedirs(save)
    r1 = run_child(args, save, resume_file, False, report, "1-fresh-kill50",
                   kill_at=0.5)
    r2 = run_child(args, save, resume_file, True, report, "2-with-fastresume")
    r3 = run_child(args, save, resume_file, False, report,
                   "3-no-fastresume-full-files")
    report.record(
        event="resume_summary",
        progress_at_kill=(r1["killed"] or {}).get("progress"),
        resume_age_at_kill_s=(r1["killed"] or {}).get("resume_age_s"),
        progress_after_restart=(r2["first_active"] or {}).get("progress"),
        restart_to_active_s=(r2["first_active"] or {}).get("t"),
        recheck_full_files_s=(r3["first_active"] or {}).get("t"),
        recheck_progress=(r3["first_active"] or {}).get("progress"))
    report.record(event="compare_with_source",
                  files=compare_with_source(ti, save, args.seed_root))


# -------------------------------------------------------------- select

def mode_select(args, ti, report):
    base = os.path.join(PROTO_TMP, "leech select")
    save = os.path.join(base, "Выбор файлов")
    reset_dir(base)
    os.makedirs(save)
    fs = ti.files()
    chosen = [i for i in range(ti.num_files())
              if fs.file_path(i).endswith(args.select_suffix)]
    priorities = [4 if i in chosen else 0 for i in range(ti.num_files())]
    ses = make_session("127.0.0.1:0", local_only=True)
    h = add_leech(ses, ti, save, args.seed_port, priorities=priorities)
    t0 = time.monotonic()
    last_connect = 0.0
    while time.monotonic() - t0 < args.timeout:
        drain_alerts(ses, report)
        st = h.status()
        now = time.monotonic() - t0
        if st.num_peers == 0 and now - last_connect > 2:
            h.connect_peer(("127.0.0.1", args.seed_port))
            last_connect = now
        if st.total_wanted and st.total_wanted_done >= st.total_wanted and \
                str(st.state) in DONE_STATES:
            break
        time.sleep(0.2)
    report.record(event="select_done",
                  seconds=round(time.monotonic() - t0, 1),
                  chosen=[fs.file_path(i) for i in chosen],
                  wanted_mb=round(st.total_wanted / MB),
                  wanted_done_mb=round(st.total_wanted_done / MB),
                  total_done_mb=round(st.total_done / MB),
                  state=str(st.state), files=files_on_disk(save))
    holder = [ses, h]
    del ses, h
    close_session_timed(holder)
    report.record(event="files_after_close", files=files_on_disk(save))
    report.record(event="compare_with_source",
                  files=compare_with_source(ti, save, args.seed_root,
                                            only=set(chosen)))


# -------------------------------------------------------------- locked

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID,
                             wt.DWORD, wt.DWORD, wt.HANDLE]
_k32.CreateFileW.restype = wt.HANDLE
_k32.CloseHandle.argtypes = [wt.HANDLE]
INVALID_HANDLE = wt.HANDLE(-1).value


def mode_locked(args, ti, report):
    base = os.path.join(PROTO_TMP, "leech locked")
    save = os.path.join(base, "Занятый файл")
    reset_dir(base)
    fs = ti.files()
    biggest = max(range(ti.num_files()), key=fs.file_size)
    target = os.path.join(save, fs.file_path(biggest))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    open(target, "wb").close()
    handle = _k32.CreateFileW(target, 0xC0000000, 0, None, 3, 0x80, None)
    if handle in (None, INVALID_HANDLE):
        report.record(event="lock_failed", err=ctypes.get_last_error())
        return
    report.record(event="locked", file=fs.file_path(biggest))

    ses = make_session("127.0.0.1:0", local_only=True)
    report.record(event="settings", optimistic_disk_retry_s=ses.get_settings()[
        "optimistic_disk_retry"])
    h = add_leech(ses, ti, save, args.seed_port)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 15:
        drain_alerts(ses, report)
        time.sleep(0.2)
    report.record(event="status_while_locked", **_status_detail(h))

    _k32.CloseHandle(handle)
    report.record(event="unlocked")
    # Попытка 1: штатные clear_error + resume
    h.clear_error()
    h.resume()
    recovered = _wait_progress(ses, h, args, report, seconds=20)
    report.record(event="after_clear_error_resume", recovered_s=recovered,
                  **_status_detail(h))
    # Попытка 2: явно снять upload_mode (libtorrent включает его при
    # ошибке записи и сам снимает только через optimistic_disk_retry)
    if recovered is None:
        h.unset_flags(lt.torrent_flags.upload_mode)
        recovered = _wait_progress(ses, h, args, report, seconds=40)
        report.record(event="after_unset_upload_mode", recovered_s=recovered,
                      **_status_detail(h))
    holder = [ses, h]
    del ses, h
    close_session_timed(holder)


def _status_detail(h):
    st = h.status()
    msg, efile = status_error(st)
    return {"state": str(st.state), "progress": round(st.progress, 3),
            "paused": st.paused, "upload_mode": st.upload_mode,
            "auto_managed": st.auto_managed, "num_peers": st.num_peers,
            "error": msg, "error_file": efile}


def _wait_progress(ses, h, args, report, seconds):
    t1 = time.monotonic()
    last_connect = 0.0
    start = h.status().progress
    while time.monotonic() - t1 < seconds:
        drain_alerts(ses, report)
        st = h.status()
        now = time.monotonic() - t1
        if st.num_peers == 0 and now - last_connect > 2:
            h.connect_peer(("127.0.0.1", args.seed_port))
            last_connect = now
        if st.progress >= start + 0.05:
            return round(now, 2)
        time.sleep(0.2)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["full", "resume", "select", "locked",
                                     "child"])
    ap.add_argument("--torrent", default=DEFAULT_TORRENT)
    ap.add_argument("--seed-port", type=int, default=6890)
    ap.add_argument("--seed-root", default=DEFAULT_SEED_ROOT)
    ap.add_argument("--limit", type=int, default=30 * MB,
                    help="байт/с для resume-режима")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--select-suffix", default=".mp4")
    ap.add_argument("--save")
    ap.add_argument("--resume-file")
    ap.add_argument("--use-resume", action="store_true")
    args = ap.parse_args()

    if args.mode == "child":
        child(args)
        return
    report = Report("probe_disk_" + args.mode)
    ti = lt.torrent_info(args.torrent)
    report.record(event="start", mode=args.mode, lt=lt.__version__,
                  total_mb=round(ti.total_size() / MB),
                  piece_kb=ti.piece_length() // 1024, files=ti.num_files())
    {"full": mode_full, "resume": mode_resume, "select": mode_select,
     "locked": mode_locked}[args.mode](args, ti, report)


if __name__ == "__main__":
    main()
