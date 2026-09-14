# -*- coding: utf-8 -*-
"""Research probe (no app changes): can we kill OUR orphaned ffmpeg
child at app level, without patching yt-dlp?

Question (owner, 13.09): after fragment cancel the watchdog closes the
task, but the ffmpeg process keeps writing .part. Can the app remember
ffmpeg processes spawned from OUR process (children) and kill only the
right one on cancel?

Mechanism under test:
  1. Two ffmpeg.exe children of THIS python process: one writes
     'title [clip 30s-60s].webm.part' (like FFmpegFD for a fragment),
     the other 'other video.webm.part' (like a merge/MP3/HLS of
     another task - no [clip] marker).
  2. Enumerate ffmpeg.exe children of os.getpid() WITH command lines
     via PowerShell Get-CimInstance Win32_Process (stdlib subprocess).
  3. Match by '[clip 30s-60s]' in CommandLine -> os.kill(pid, 9).
  4. Verify: marked child dead, unmarked sibling untouched.

ASCII only. Workdir: %TEMP%\\vd-orph-probe (removed on start).
"""
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FF = os.path.join(ROOT, "dist", "VideoDownloader", "ffmpeg.exe")
if not os.path.isfile(FF):
    sys.exit("no ffmpeg.exe in dist/VideoDownloader")
TMP = os.environ.get("TEMP", "/tmp")
WORK = os.path.join(TMP, "vd-orph-probe")
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK, exist_ok=True)
OUT_FRAG = os.path.join(WORK, "title [clip 30s-60s].webm")
OUT_MERGE = os.path.join(WORK, "other video.webm")

SRC = ["-f", "lavfi", "-i", "testsrc=duration=300:size=320x180"]
p_frag = subprocess.Popen([FF, "-y", *SRC, OUT_FRAG])
p_merge = subprocess.Popen([FF, "-y", *SRC, OUT_MERGE])
time.sleep(2.0)
print("spawned: frag pid=%d merge pid=%d (parent python pid=%d)"
      % (p_frag.pid, p_merge.pid, os.getpid()), flush=True)

# --- enumerate OUR ffmpeg children with command lines (CIM) ---
t0 = time.monotonic()
try:
    ps = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='ffmpeg.exe'\" | "
         "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json"],
        capture_output=True, text=True, timeout=30)
    elapsed = time.monotonic() - t0
    print("CIM query: %.1fs rc=%s" % (elapsed, ps.returncode), flush=True)
    if ps.returncode != 0:
        print("stderr:", (ps.stderr or "")[:300], flush=True)
    rows = json.loads(ps.stdout) if ps.stdout.strip() else []
except Exception as e:
    print("CIM query FAILED: %s: %s" % (type(e).__name__, e), flush=True)
    rows = []
if isinstance(rows, dict):
    rows = [rows]

mine = [r for r in rows if r.get("ParentProcessId") == os.getpid()]
print("ffmpeg children of this python: %d" % len(mine), flush=True)
for r in mine:
    cl = r.get("CommandLine") or ""
    print("  pid=%s marker=%s" % (r.get("ProcessId"),
                                  "[clip 30s-60s]" in cl), flush=True)

targets = [r["ProcessId"] for r in mine
           if "[clip 30s-60s]" in (r.get("CommandLine") or "")]
print("targets (fragment marker only):", targets, flush=True)

killed_ok = True
for pid in targets:
    try:
        os.kill(pid, 9)      # Windows: TerminateProcess
    except OSError as e:
        killed_ok = False
        print("kill(%s) failed: %s" % (pid, e), flush=True)
time.sleep(1.5)

alive = lambda p: p.poll() is None
frag_dead = not alive(p_frag)
merge_alive = alive(p_merge)
print("fragment ffmpeg dead: %s | merge ffmpeg untouched: %s"
      % (frag_dead, merge_alive), flush=True)
ok = (killed_ok and frag_dead and merge_alive
      and len(targets) == 1 and elapsed < 10)
print("PROBE " + ("PASS" if ok else "FAIL")
      + ": targeted kill of OUR ffmpeg child via CIM cmdline marker",
      flush=True)

for p in (p_frag, p_merge):
    if alive(p):
        p.kill()
shutil.rmtree(WORK, ignore_errors=True)
sys.exit(0 if ok else 1)
