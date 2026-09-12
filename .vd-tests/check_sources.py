# -*- coding: utf-8 -*-
"""Live check of download sources (TikTok / Instagram / VK).

Reusable, two subcommands:

  check_sources.py analyze [quality]
      For each URL from sources.local.txt:
        1. fetch_info() - the exact analysis the app uses (downloader.py).
        2. Full format list -> %TEMP%\\vd-src-test\\formats-<n>-<site>.txt
           (never printed to chat).
        3. Simulate the app's format choice (same options as
           DownloadManager._build_options, simulate) for video-best
           and audio mode. Reports format_id, size and watermark status.
      Prints a summary table and STOPS. No download is started.

  check_sources.py download <n|all> [n...] [range=S-E] [precise=0|1]
      (Run only after the user says ok.) Downloads the listed items
      into %TEMP%\\vd-src-test and extracts 3 frames per item with
      ffmpeg (first/middle/last third) into frames-<n>\\.
      range=S-E (seconds) downloads only that section via
      download_ranges - the same mechanism planned for 1.0.4.
      precise=0 cuts at keyframes (fast); precise=1 (default) uses
      force_keyframes_at_cuts (re-encode, exact cuts).
      VK note: its HLS formats stall on slow CDN for long videos;
      for range downloads use format url1080 (direct mp4) instead -
      see the 'vk_url_format' handling below.

No cookies, no login, no browser data is used. settings.json, the
download history and the user's Downloads folder are never touched.

Output files live in %TEMP% only; ffmpeg/ffprobe are taken from
dist\\VideoDownloader (fetched once by build_ffmpeg.ps1, cached in
build-ffmpeg-cache).

TikTok note: yt-dlp needs curl_cffi (impersonation) for TikTok;
since 1.0.4 it is pinned in requirements.txt (curl_cffi==0.16.0).

ASCII only.
"""

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import downloader  # noqa: E402
from yt_dlp import YoutubeDL  # noqa: E402
from yt_dlp.version import __version__ as YTDLP_VERSION  # noqa: E402

TEMP = os.environ.get("TEMP", os.path.expanduser("~\\AppData\\Local\\Temp"))
WORKDIR = os.path.join(TEMP, "vd-src-test")
SOURCES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "sources.local.txt")
FFMPEG_DIR = os.path.join(ROOT, "dist", "VideoDownloader")

# Subcommand args: analyze [quality] / download [n|all]
# quality: best (default), 1080, 720 ...; n: row number from the table.

SITE_MAP = (
    ("tiktok", "tiktok"),
    ("instagram", "instagram"),
    ("vk", "vk"),
)


def site_of(url):
    low = url.lower()
    for key, tag in SITE_MAP:
        if key in low:
            return tag
    return "other"


def load_sources():
    urls = []
    with open(SOURCES, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    if not urls:
        sys.exit("No URLs in sources.local.txt")
    return urls


def fmt_mb(size):
    if not size:
        return "?"
    return "%.1f" % (size / 1048576.0)


def fmt_mmss(s):
    if not s:
        return "?"
    m, sec = divmod(int(s), 60)
    return "%d:%02d" % (m, sec)


def is_watermarked(fmt):
    """Heuristic: TikTok download_addr = with watermark; explicit
    'watermark' in format_id/format_note also counts."""
    fid = (fmt.get("format_id") or "").lower()
    note = (fmt.get("format_note") or "").lower()
    if "watermark" in fid or "watermark" in note:
        return True
    if fid.startswith("download_addr"):
        return True
    return False


def write_formats_file(path, url, info, chosen, audio_str):
    with open(path, "w", encoding="utf-8") as f:
        f.write("url: %s\n" % url)
        f.write("title: %s\n" % (info.get("title") or "?"))
        f.write("uploader: %s\n" % (info.get("uploader") or "?"))
        dur = info.get("duration")
        f.write("duration: %s\n" % (fmt_mmss(dur) if dur else "?"))
        f.write("formats: %d\n\n" % len(info.get("formats") or []))
        f.write("%-4s %-26s %-6s %-10s %-8s %-8s %-24s %10s %7s\n" % (
            "id", "format_id", "ext", "res", "vcodec", "acodec", "note",
            "filesize", "tbr"))
        f.write("-" * 110 + "\n")
        for i, fmt in enumerate(info.get("formats") or []):
            fid = fmt.get("format_id") or "?"
            ext = fmt.get("ext") or "?"
            res = "%sx%s" % (fmt.get("width") or "?", fmt.get("height") or "?")
            vc = fmt.get("vcodec") or "-"
            ac = fmt.get("acodec") or "-"
            note = fmt.get("format_note") or ""
            if is_watermarked(fmt):
                note = (note + " [WATERMARKED]").strip()
            fs = fmt.get("filesize") or fmt.get("filesize_approx") or ""
            tbr = int(fmt.get("tbr") or 0) or ""
            f.write("%-4d %-26s %-6s %-10s %-8s %-8s %-24s %10s %7s\n" % (
                i, fid, ext, res, vc, ac, note[:24], fs, tbr))
        f.write("\napp choice (video):\n")
        for fmt in chosen:
            f.write("  %s | %s | %s MB | %s\n" % (
                fmt.get("format_id"), fmt.get("ext"),
                fmt_mb(fmt.get("filesize") or fmt.get("filesize_approx")),
                fmt.get("format_note") or ""))
        f.write("app choice (audio): %s\n" % audio_str)


def app_options(mode, quality):
    """Same format logic as DownloadManager._build_options, but for
    analyze (simulate=True, nothing is written to disk)."""
    if mode == "audio":
        fmt = "bestaudio/best"
    elif quality == "best":
        fmt = "bestvideo+bestaudio/best"
    else:
        fmt = ("bestvideo[height<=%s]+bestaudio/"
               "best[height<=%s]/best" % (quality, quality))
    return {
        "format": fmt,
        "quiet": True,
        "simulate": True,
        "noplaylist": True,
        "logger": downloader.get_logger("vdl.ytdlp", "yt-dlp.log"),
    }


def chosen_formats(info):
    """Formats the app would download: requested_formats (video+audio
    merge) or the single selected format."""
    if info.get("requested_formats"):
        return list(info["requested_formats"])
    return [info] if info.get("format_id") else []


def video_entry(url, mode="video", quality="best"):
    """extract_info(download=False) with app options; returns
    (info, error). Format selection happens in process_video_result
    even without download, so 'requested_formats' is exactly what
    the app would download."""
    try:
        with YoutubeDL(app_options(mode, quality)) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        return None, str(exc)
    return info, None

def find_ffmpeg():
    """ffmpeg.exe: dist\\VideoDownloader only (PATH is not used)."""
    exe = os.path.join(FFMPEG_DIR, "ffmpeg.exe")
    if os.path.isfile(exe):
        return exe
    sys.exit("ffmpeg.exe not found in dist\\VideoDownloader - run "
             "build_ffmpeg.ps1 first")


def extract_frames(n, video, ffmpeg):
    """3 frames (first/middle/last third) as JPG next to the video."""
    dur = None
    probe = os.path.join(FFMPEG_DIR, "ffprobe.exe")
    if os.path.isfile(probe):
        try:
            out = subprocess.run(
                [probe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", video],
                capture_output=True, text=True, timeout=60)
            dur = float(out.stdout.strip() or 0) or None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            dur = None
    frames_dir = os.path.join(WORKDIR, "frames-%02d" % n)
    os.makedirs(frames_dir, exist_ok=True)
    if dur:
        marks = [dur / 6.0, dur / 2.0, dur * 5.0 / 6.0]
    else:
        marks = [1.0, 2.0, 3.0]
    made = []
    for i, t in enumerate(marks):
        jpg = os.path.join(frames_dir, "frame-%02d-%d.jpg" % (n, i + 1))
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-ss", "%.3f" % t,
               "-i", video, "-frames:v", "1", "-q:v", "2", jpg]
        try:
            subprocess.run(cmd, capture_output=True, timeout=120)
            if os.path.isfile(jpg) and os.path.getsize(jpg) > 0:
                made.append(jpg)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return made


def probe_duration(path):
    """Duration of a media file via ffprobe (None if unavailable)."""
    probe = os.path.join(FFMPEG_DIR, "ffprobe.exe")
    if not os.path.isfile(probe):
        return None
    try:
        out = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=60)
        return float(out.stdout.strip() or 0) or None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def cmd_analyze(quality):
    os.makedirs(WORKDIR, exist_ok=True)
    urls = load_sources()
    print("check_sources.py analyze | yt-dlp %s | quality=%s" % (
        YTDLP_VERSION, quality))
    print("workdir: %s\n" % WORKDIR)
    rows = []
    for n, url in enumerate(urls, 1):
        site = site_of(url)
        print("[%d/%d] %s: analyzing ..." % (n, len(urls), site), flush=True)
        # 1) fetch_info: what the app shows in the GUI
        app_err = None
        try:
            fi = downloader.fetch_info(url)
        except Exception as exc:
            fi, app_err = None, str(exc)
        # 2) full info with app choice simulation
        info, err = video_entry(url, "video", quality)
        chosen = chosen_formats(info) if info is not None else []
        formats_count = len((info or {}).get("formats") or [])
        wm_count = sum(1 for f in (info or {}).get("formats") or []
                      if is_watermarked(f))
        # audio mode choice
        if info is not None:
            _i2, _e2 = video_entry(url, "audio")
            chosen_audio = chosen_formats(_i2) if _i2 else []
            audio_str = ", ".join(
                "%s (%s MB)" % (
                    f.get("format_id"),
                    fmt_mb(f.get("filesize") or f.get("filesize_approx")))
                for f in chosen_audio) or "?"
        else:
            audio_str = "?"
        # file with the full format list
        fname = "formats-%02d-%s.txt" % (n, site)
        fpath = os.path.join(WORKDIR, fname)
        if info is not None:
            write_formats_file(fpath, url, info, chosen or [], audio_str)
        rows.append({
            "n": n, "url": url, "site": site,
            "fetch_info": "ok" if fi else "FAIL: " + (app_err or "")[:120],
            "fetch_ok": fi is not None,
            "title": (fi or info or {}).get("title", ""),
            "duration": (fi or info or {}).get("duration"),
            "formats": formats_count,
            "wm_count": wm_count,
            "chosen": chosen or [],
            "audio_str": audio_str,
            "formats_file": fpath if info is not None else "",
        })
        if fi:
            print("    title: %s" % rows[-1]["title"][:60])
            print("    app qualities: %s | audio: %s"
                  % (fi.get("video_qualities"), fi.get("audio_available")))
        elif info is not None:
            print("    (fetch_info failed, formats analyzed anyway)")
        if err:
            print("    simulate error: %s" % err[:200])
    # summary table
    print("\n" + "=" * 78)
    print("SUMMARY (no download yet - waiting for your ok)")
    print("=" * 78)
    print("%-3s %-9s %-6s %-16s %-9s %-6s %-11s %s" % (
        "n", "site", "dur", "format_id", "size MB", "wm?", "wm fmts", "fetch"))
    for r in rows:
        if not r["chosen"]:
            print("%-3d %-9s %-6s %-16s %-9s %-6s %-11s %s" % (
                r["n"], r["site"], fmt_mmss(r["duration"]), "-", "-", "-",
                "%d/%d" % (r["wm_count"], r["formats"]), r["fetch_info"]))
            continue
        vids = [f for f in r["chosen"] if f.get("vcodec") not in (None, "none")]
        auds = [f for f in r["chosen"]
                if f.get("acodec") not in (None, "none")
                and f.get("vcodec") in (None, "none")]
        v = vids[0] if vids else r["chosen"][0]
        total = sum((f.get("filesize") or f.get("filesize_approx") or 0)
                    for f in r["chosen"])
        if not total and v.get("tbr") and r["duration"]:
            total = v["tbr"] * 125 * r["duration"]
        wm = any(is_watermarked(f) for f in r["chosen"])
        fid = "+".join(f.get("format_id") or "?" for f in r["chosen"])
        print("%-3d %-9s %-6s %-16s %-9s %-6s %-11s %s" % (
            r["n"], r["site"], fmt_mmss(r["duration"]), fid[:16],
            fmt_mb(total), "YES" if wm else "no",
            "%d/%d" % (r["wm_count"], r["formats"]), "ok"))
    print("=" * 78)
    print("audio mode choice: %s" % "; ".join(
        "%d: %s" % (r["n"], r["audio_str"]) for r in rows))
    print("Full format lists: %s\\formats-*.txt" % WORKDIR)
    print("Run download only after your ok:")
    print("  build-venv\\Scripts\\python.exe .vd-tests\\check_sources.py "
          "download <n|all> [n...] [range=S-E] [precise=0|1] [fmt=...] "
          "[mode=video|audio]")


def cmd_download(args):
    """args: list of items ('all' or numbers). Optional 'range=START-END'
    (seconds) applies to ALL listed items (for huge videos)."""
    urls = load_sources()
    range_arg = None
    precise = 1
    fmt_override = None
    mode = "video"
    items = []
    for a in args:
        if a.startswith("range="):
            try:
                s, e = a[6:].split("-")
                range_arg = (float(s), float(e))
            except ValueError:
                sys.exit("Bad range: %r (expected range=START-END in seconds)" % a)
            continue
        if a.startswith("precise="):
            # precise=0: cut at keyframes only (fast, ffmpeg -c copy);
            # precise=1 (default): force_keyframes_at_cuts, re-encode
            try:
                precise = int(a[8:])
            except ValueError:
                sys.exit("Bad precise: %r (0 or 1)" % a)
            continue
        if a.startswith("fmt="):
            # Override the format string (e.g. fmt=url1080/best[height<=1080]
            # for VK range downloads - its HLS formats stall on slow CDN).
            fmt_override = a[4:]
            continue
        if a.startswith("mode="):
            # video (default) or audio (the app's MP3 mode: bestaudio +
            # FFmpegExtractAudio mp3 192k)
            if a[5:] not in ("video", "audio"):
                sys.exit("Bad mode: %r (video or audio)" % a)
            mode = a[5:]
            continue
        if a == "all":
            items = list(range(1, len(urls) + 1))
            break
        try:
            n = int(a)
        except ValueError:
            sys.exit("Bad item number: %r" % a)
        if not 1 <= n <= len(urls):
            sys.exit("Item %d not in 1..%d" % (n, len(urls)))
        items.append(n)
    if not items:
        sys.exit("Nothing to download: give item numbers or 'all'")
    ffmpeg = find_ffmpeg()
    # dev-mode: the app sets ffmpeg_location only when sys.frozen (then it
    # is dirname(sys.executable)). In this test ffmpeg lives in
    # dist\VideoDownloader - inject ffmpeg_location the same way without
    # touching downloader.py (wrap _build_options once for the whole run;
    # the range wrapper below composes on top of it).
    ffmpeg_dir = FFMPEG_DIR
    orig_build = downloader.DownloadManager._build_options

    def build_with_ffmpeg(self, it):
        opts = orig_build(self, it)
        opts["ffmpeg_location"] = ffmpeg_dir
        return opts

    downloader.DownloadManager._build_options = build_with_ffmpeg
    mgr = downloader.DownloadManager(max_concurrent=1)
    mgr.start()
    print("workdir: %s" % WORKDIR)
    print("ffmpeg: %s" % ffmpeg)
    if range_arg:
        print("download range: %g..%gs (all listed items, precise=%d)" % (
            range_arg[0], range_arg[1], precise))
    print("")
    for n in items:
        url = urls[n - 1]
        site = site_of(url)
        if range_arg:
            print("[%02d] %s: downloading range %g-%gs ..." % (
                n, site, range_arg[0], range_arg[1]), flush=True)
        else:
            print("[%02d] %s: downloading ..." % (n, site), flush=True)
        if range_arg:
            # Same mechanism as the planned 1.0.4 fragment feature:
            # download_ranges + force_keyframes_at_cuts. Set BEFORE add()
            # so the worker thread sees it immediately.
            mgr._download_range = range_arg
        if range_arg or fmt_override:
            orig = downloader.DownloadManager._build_options

            def build_with_range(self, it):
                opts = orig(self, it)
                if getattr(self, "_download_range", None):
                    start, end = self._download_range
                    opts["download_ranges"] = (
                        lambda info, ydl, _s=start, _e=end:
                        [{"start_time": _s, "end_time": _e}])
                    if precise:
                        opts["force_keyframes_at_cuts"] = True
                if fmt_override:
                    opts["format"] = fmt_override
                return opts

            downloader.DownloadManager._build_options = build_with_range
        item = mgr.add(url, mode=mode, quality="best", output_dir=WORKDIR)
        try:
            deadline = time.time() + 900
            while time.time() < deadline:
                if item.status in (downloader.STATUS_COMPLETED,
                                   downloader.STATUS_ERROR,
                                   downloader.STATUS_PAUSED):
                    break
                time.sleep(0.2)
            if item.status == downloader.STATUS_PAUSED:
                item.request_resume()
                deadline = time.time() + 900
                while time.time() < deadline and item.status not in (
                        downloader.STATUS_COMPLETED,
                        downloader.STATUS_ERROR):
                    time.sleep(0.2)
        finally:
            if range_arg:
                mgr._download_range = None
            if range_arg or fmt_override:
                downloader.DownloadManager._build_options = orig
        if item.status != downloader.STATUS_COMPLETED:
            print("  FAIL: %s" % (item.error or item.status))
            continue
        video = item.files[0]
        size = os.path.getsize(video)
        print("  ok: %s (%.1f MB)" % (os.path.basename(video), size / 1048576.0))
        if mode == "audio":
            dur = probe_duration(video)
            print("  duration: %s" % fmt_mmss(dur))
            continue
        frames = extract_frames(n, video, ffmpeg)
        print("  frames: %s" % ", ".join(os.path.basename(p) for p in frames))
        if len(frames) < 3:
            print("  WARN: ffmpeg made %d/3 frames" % len(frames))


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("analyze", "download"):
        sys.exit("Usage: check_sources.py analyze [quality] | "
                 "download <n|all> [n...] [range=S-E] [precise=0|1] "
                 "[fmt=...] [mode=video|audio]")
    if sys.argv[1] == "analyze":
        quality = sys.argv[2] if len(sys.argv) > 2 else "best"
        cmd_analyze(quality)
    else:
        if len(sys.argv) < 3:
            sys.exit("Usage: check_sources.py download <n|all> [n...] "
                     "[range=S-E] [precise=0|1] [fmt=...]")
        cmd_download(sys.argv[2:])


if __name__ == "__main__":
    main()


