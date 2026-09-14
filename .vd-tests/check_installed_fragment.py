# -*- coding: utf-8 -*-
"""Live checks for 1.0.5 fragment feature in the INSTALLED copy.

Per owner acceptance (14.09.2026):
  FRAG-CANCEL - YouTube fragment 0:30-1:00; cancel while ffmpeg.exe is
    REALLY running (CIM: child of this python + '[clip 30s-60s]' marker);
    tasklist must show ffmpeg.exe before cancel and it must DISAPPEAR
    after cancel (1.0.5 kill-orphan-ffmpeg). Task must die fast with
    ERROR 'Отменено'.
  FULL - full video download still works (not broken by 1.0.5), audio
    stream present (installed ffprobe).
  MP3-FRAG - audio-mode fragment 30-60s precise -> mp3 with audio and
    ~30s duration.

yt_dlp + ffmpeg come from the INSTALLED copy (%LOCALAPPDATA%\\Programs\\
VideoDownloader), app code (downloader.py) from the project root - the
same code that is built into the 1.0.5 exe. Frozen-like options via a
_build_options patch (as check_installed_tiktok.py did for 1.0.4).
Workdir: <home>\\AppData\\Local\\Temp\\vd-15-frag (removed on exit).
TEMP env is NOT trusted (MSYS bash exports '/tmp', which Windows
python resolves as C:\\tmp) - explicit home-based path instead.

  build-venv\\Scripts\\python.exe .vd-tests\\check_installed_fragment.py
"""
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.environ["LOCALAPPDATA"], "Programs", "VideoDownloader")
INTERNAL = os.path.join(APP, "_internal")
sys.path.insert(0, INTERNAL)
sys.path.insert(1, ROOT)

import downloader  # noqa: E402
import yt_dlp  # noqa: E402
from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor  # noqa: E402

TEMP = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Temp")
WORK = os.path.join(TEMP, "vd-15-frag")
FFPROBE = os.path.join(APP, "ffprobe.exe")
BBB = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"       # Big Buck Bunny
SHORT = "https://www.youtube.com/watch?v=BaW-jenozKc"     # 10s test video
MARKER = "[clip 30s-60s]"

PASS, FAIL = [], []


def check(name, ok, details=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), "|", name, "|", details, flush=True)


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def ffprobe_audio(path):
    out = run([FFPROBE, "-v", "error", "-show_entries",
               "stream=codec_type", "-of", "csv=p=0", path])
    return "audio" in (out.stdout or "")


def ffprobe_duration(path):
    out = run([FFPROBE, "-v", "error", "-show_entries",
               "format=duration", "-of", "csv=p=0", path])
    try:
        return float((out.stdout or "").strip())
    except ValueError:
        return None


def tasklist_ffmpeg_count():
    out = run(["tasklist", "/FI", "IMAGENAME eq ffmpeg.exe"])
    return len([l for l in (out.stdout or "").splitlines()
                if l.strip().startswith("ffmpeg.exe")])


def my_ffmpeg_children():
    """ffmpeg.exe processes whose parent is THIS python (via CIM)."""
    try:
        ps = run(["powershell", "-NoProfile", "-Command",
                  "Get-CimInstance Win32_Process -Filter \"Name='ffmpeg.exe'\" | "
                  "Select-Object ProcessId,ParentProcessId,CommandLine | "
                  "ConvertTo-Json"])
        if ps.returncode != 0 or not (ps.stdout or "").strip():
            return []
        rows = json.loads(ps.stdout)
    except Exception:
        return []
    if isinstance(rows, dict):
        rows = [rows]
    return [r for r in rows if r.get("ParentProcessId") == os.getpid()]


def wait_status(item, statuses, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if item.status in statuses:
            return True
        time.sleep(0.2)
    return False


def fresh_dir(sub):
    d = os.path.join(WORK, sub)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    return d


def check_cancel(mgr, baseline):
    d = fresh_dir("cancel")
    t0 = time.monotonic()
    # precise_cut: ffmpeg re-encodes a 4K section -> runs for minutes ->
    # guaranteed window to observe it running and cancel mid-flight
    item = mgr.add(BBB, mode="video", quality="best", output_dir=d,
                   playlist=False, time_range=(30, 60), precise_cut=True)
    reached = wait_status(item, (downloader.STATUS_DOWNLOADING,
                                 downloader.STATUS_COMPLETED,
                                 downloader.STATUS_ERROR), 240)
    if not reached or item.status != downloader.STATUS_DOWNLOADING:
        check("FRAG-CANCEL fragment reached DOWNLOADING", False,
              "status=%s err=%s" % (item.status, (item.error or "")[:150]))
        return
    check("FRAG-CANCEL fragment reached DOWNLOADING", True,
          "%.1fs" % (time.monotonic() - t0))

    saw = None
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        for r in my_ffmpeg_children():
            if MARKER in (r.get("CommandLine") or ""):
                saw = r
                break
        if saw:
            break
        if item.status in (downloader.STATUS_COMPLETED,
                          downloader.STATUS_ERROR):
            break
        time.sleep(1.0)
    if not saw:
        check("FRAG-CANCEL ffmpeg.exe really running (CIM child)", False,
              "no marked child; status=%s tasklist=%d"
              % (item.status, tasklist_ffmpeg_count()))
        if item.status == downloader.STATUS_DOWNLOADING:
            mgr.cancel(item.id)
            wait_status(item, (downloader.STATUS_ERROR,
                              downloader.STATUS_COMPLETED), 60)
        return
    check("FRAG-CANCEL ffmpeg.exe really running (CIM child)", True,
          "pid=%s" % saw.get("ProcessId"))

    n = tasklist_ffmpeg_count()
    check("FRAG-CANCEL tasklist shows ffmpeg.exe before cancel",
          n > baseline, "count=%d baseline=%d" % (n, baseline))

    t_cancel = time.monotonic()
    mgr.cancel(item.id)
    ok = wait_status(item, (downloader.STATUS_ERROR,
                            downloader.STATUS_COMPLETED), 30)
    check("FRAG-CANCEL task died fast with 'Отменено'",
          ok and item.status == downloader.STATUS_ERROR
          and item.error == "Отменено",
          "status=%s err=%s in %.1fs" % (item.status, (item.error or "")[:80],
                                         time.monotonic() - t_cancel))

    gone_at = None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if tasklist_ffmpeg_count() <= baseline and not my_ffmpeg_children():
            gone_at = time.monotonic() - t_cancel
            break
        time.sleep(0.5)
    if gone_at is None:
        # orphan: show what is still running (owner: stop, no release)
        for r in my_ffmpeg_children():
            print("  ORPHAN pid=%s cmd=%s"
                  % (r.get("ProcessId"), (r.get("CommandLine") or "")[:200]),
                  flush=True)
    check("FRAG-CANCEL ffmpeg.exe gone from tasklist after cancel",
          gone_at is not None,
          ("gone %.1fs after cancel" % gone_at) if gone_at is not None
          else "STILL RUNNING 15s after cancel (ORPHAN)")


def check_full(mgr):
    d = fresh_dir("full")
    item = mgr.add(SHORT, mode="video", quality="best", output_dir=d,
                   playlist=False)
    wait_status(item, (downloader.STATUS_COMPLETED,
                       downloader.STATUS_ERROR), 300)
    ok = (item.status == downloader.STATUS_COMPLETED
          and item.files and os.path.isfile(item.files[0]))
    used = SHORT
    if not ok:
        used = BBB
        item = mgr.add(BBB, mode="video", quality="360", output_dir=d,
                       playlist=False)
        wait_status(item, (downloader.STATUS_COMPLETED,
                           downloader.STATUS_ERROR), 600)
        ok = (item.status == downloader.STATUS_COMPLETED
              and item.files and os.path.isfile(item.files[0]))
    check("FULL full video download works", ok,
          ("%.1f MB %s (%s)" % (os.path.getsize(item.files[0]) / 1048576.0,
                                os.path.basename(item.files[0]), used)) if ok
          else "status=%s err=%s" % (item.status, (item.error or "")[:150]))
    if ok:
        check("FULL audio stream present (ffprobe)",
              ffprobe_audio(item.files[0]), os.path.basename(item.files[0]))


def check_mp3_frag(mgr):
    d = fresh_dir("mp3frag")
    item = mgr.add(BBB, mode="audio", quality="best", output_dir=d,
                   playlist=False, time_range=(30, 60), precise_cut=True)
    wait_status(item, (downloader.STATUS_COMPLETED,
                       downloader.STATUS_ERROR), 600)
    ok = (item.status == downloader.STATUS_COMPLETED
          and item.files and os.path.isfile(item.files[0]))
    check("MP3-FRAG mp3 fragment completed", ok,
          ("%.2f MB %s" % (os.path.getsize(item.files[0]) / 1048576.0,
                          os.path.basename(item.files[0]))) if ok
          else "status=%s err=%s" % (item.status, (item.error or "")[:150]))
    if ok:
        check("MP3-FRAG audio stream present", ffprobe_audio(item.files[0]),
              os.path.basename(item.files[0]))
        dur = ffprobe_duration(item.files[0])
        check("MP3-FRAG duration ~30s",
              dur is not None and 28.0 <= dur <= 33.0, "dur=%s" % dur)


def main():
    if not os.path.isfile(os.path.join(APP, "VideoDownloader.exe")):
        sys.exit("installed copy not found: %s" % APP)
    if not os.path.isfile(os.path.join(APP, "ffmpeg.exe")):
        sys.exit("installed ffmpeg.exe not found: %s" % APP)
    os.makedirs(WORK, exist_ok=True)
    print("installed copy:", APP)
    print("yt_dlp:", yt_dlp.version.__version__, "from",
          os.path.dirname(yt_dlp.__file__))
    print("app code:", os.path.join(ROOT, "downloader.py"))
    baseline = tasklist_ffmpeg_count()
    print("baseline ffmpeg.exe in tasklist:", baseline, flush=True)

    orig_build = downloader.DownloadManager._build_options

    def build_frozen(self, it):
        opts = orig_build(self, it)
        opts["ffmpeg_location"] = APP
        FFmpegPostProcessor._ffmpeg_location.set(APP)
        return opts

    downloader.DownloadManager._build_options = build_frozen
    try:
        mgr = downloader.DownloadManager(max_concurrent=1)
        mgr.start()
        check_cancel(mgr, baseline)
        check_full(mgr)
        check_mp3_frag(mgr)
    finally:
        downloader.DownloadManager._build_options = orig_build
        shutil.rmtree(WORK, ignore_errors=True)

    print()
    print("TOTAL: %d PASS, %d FAIL" % (len(PASS), len(FAIL)))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()


