#!/usr/bin/env python3
r"""
AIO Downloader V6  (Windows 10/11, run from cmd)

Two ways to work, both ending in the same pipeline:

  AUTOMATIC   Type episode numbers (891, 886-897, 167a), or "album 57" / "club 7".
              The script asks the Club's API for each signed link, using a session you
              paste ONCE (any cURL copied from a Club request in the browser). Your
              password is never asked for and nothing is saved to disk.
              Several episodes download at the same time (default 3 threads; "t 4"
              changes it). Album and Club season selections skip the bonus items.
  MANUAL      Type "p" and paste a "Copy as cURL (Windows)" for one audio request.

Each episode is converted to MP3 with tags and cover in ONE ffmpeg pass and saved in a
folder named after its album or Club season, inside your download folder:

    <download folder>\Album 57 - A Call to Something More\#0731 Title.mp3
    <download folder>\Club Season 7\#0891 Cars, Trains, and Motorcycles.mp3

    title       Cars, Trains, and Motorcycles
    album       Album 57: A Call to Something More   |   Club Season 7
    track       position among the main episodes, e.g. 6/12 (bonus items get none)
    cover       albums: the album's main cover, the same for every episode
                Club seasons: each episode's own medium cover
                (always a baseline RGB JPEG, longest side 800 px)

To cancel a running download without closing cmd: press q (batches) or Ctrl+C. Running
transfers are stopped, temporary files are removed, and you return to the prompt.

Episodes come from catalog.json (built by build_catalog.py). Anything not in the catalog
falls back to the aio-rename title rules. Nothing is deleted unless the new file was written.

Needs: Python 3, ffmpeg + ffprobe on PATH (curl.exe ships with Windows), and
       pip install colorama tqdm
"""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse, urlsplit, urlunsplit

# ——— Settings you may want to change ————————————————————————————————————
ARTIST = "Adventures in Odyssey"   # set to "" to leave the artist tag empty
MP3_BITRATE = "320k"
TARGET_LONG_SIDE = 800             # cover: longest side in pixels
UPSCALE_SMALL_COVERS = True        # False = never enlarge a cover, only shrink
PREFERRED_MAX_COVER_BYTES = 500_000
HARD_MAX_COVER_BYTES = 1_000_000
QSCALE_STEPS = [3, 5, 8, 12, 16, 24]   # ffmpeg JPEG quality, best first
MIN_AUDIO_BYTES = 50_000
API_BASE = "https://fotf.my.site.com/aio/services/apexrest/v1"
APP_ORIGIN = "https://app.adventuresinodyssey.com"
API_DELAY_SECONDS = 2.0            # pause between API lookups (one at a time, like normal use)
CONFIRM_ABOVE = 40                 # ask before a selection larger than this
ALLOWED_MEDIA_DOMAINS = ("adventuresinodyssey.com",)   # only download from the Club's own media host
DEFAULT_WORKERS = 3                # episodes downloaded at the same time ("t 4" changes it)
MAX_WORKERS = 6

# ——— Pre-req checks ————————————————————————————————————————————————————
missing_modules = []
for _mod in ("colorama", "tqdm"):
    try:
        __import__(_mod)
    except ImportError:
        missing_modules.append(_mod)
missing_tools = [t for t in ("curl", "ffmpeg", "ffprobe") if shutil.which(t) is None]
if missing_modules or missing_tools:
    if missing_modules:
        print("Missing Python packages: " + ", ".join(missing_modules))
        print("  Fix: pip install " + " ".join(missing_modules))
    if missing_tools:
        print("Not found on PATH: " + ", ".join(missing_tools))
        if "curl" in missing_tools:
            print("  curl.exe ships with Windows 10 (1803+) and 11.")
        if "ffmpeg" in missing_tools or "ffprobe" in missing_tools:
            print("  Fix: winget install Gyan.FFmpeg   (then reopen cmd)")
    sys.exit(1)

from colorama import Fore, init as colorama_init  # noqa: E402
from tqdm import tqdm  # noqa: E402

colorama_init(autoreset=True)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")   # never crash on characters cmd cannot print
    except Exception:
        pass


def app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


PRINT_LOCK = threading.Lock()
ACTIVE = set()                      # running curl/ffmpeg processes, so a cancel can stop them
ACTIVE_LOCK = threading.Lock()

try:
    import msvcrt                   # Windows only: lets "q" cancel a running batch without Enter
except ImportError:
    msvcrt = None


class Cancelled(Exception):
    pass


def _say(text):
    with PRINT_LOCK:
        print(text)


def info(msg):
    _say(msg)


def good(msg):
    _say(Fore.GREEN + msg)


def warn(msg):
    _say(Fore.YELLOW + msg)


def bad(msg):
    _say(Fore.RED + msg)


def q_pressed():
    """True if the q key was pressed (Windows console only). Other keys are ignored."""
    if msvcrt is None:
        return False
    pressed = False
    while msvcrt.kbhit():
        if msvcrt.getwch().lower() == "q":
            pressed = True
    return pressed


def terminate_active():
    with ACTIVE_LOCK:
        procs = list(ACTIVE)
    for p in procs:
        try:
            p.terminate()
        except OSError:
            pass


def run_tracked(cmd, cancel=None, quiet=False):
    """Run a command. It is registered so a cancel can stop it. Returns the return code."""
    sink = subprocess.DEVNULL if quiet else None
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=sink, stderr=sink)
    with ACTIVE_LOCK:
        ACTIVE.add(p)
    try:
        while True:
            try:
                return p.wait(timeout=0.3)
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.is_set():
                    p.terminate()
                    p.wait()
                    return -1
    except KeyboardInterrupt:
        p.terminate()
        raise
    finally:
        with ACTIVE_LOCK:
            ACTIVE.discard(p)


class ApiGate:
    """Spaces API lookups at least `delay` seconds apart, across all threads."""

    def __init__(self, delay):
        self.delay = delay
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self, cancel):
        with self.lock:
            while not cancel.is_set():
                remaining = self.last + self.delay - time.time()
                if remaining <= 0:
                    break
                time.sleep(min(remaining, 0.1))
            self.last = time.time()


# ——— Saved settings (folder and thread count only; never tokens) ——————————
def load_settings(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
    except OSError:
        pass


def resolve_download_dir(raw):
    """Turn what was typed into a folder path. Returns (path, note or None)."""
    text = os.path.expanduser(os.path.expandvars(raw.strip().strip('"').strip("'")))
    cwd = os.getcwd()
    if not text or text == ".":
        return cwd, None
    if os.path.isabs(text):
        return os.path.abspath(text), None
    norm = lambda p: os.path.normcase(os.path.normpath(p))
    if norm(cwd) == norm(text) or norm(cwd).endswith(os.sep + norm(text)):
        return cwd, "That matches the folder you are already in, so I am using that folder."
    return os.path.abspath(text), "That was a relative path, so it sits inside the folder you started the script from."


# ——— Input helpers ——————————————————————————————————————————————————————
def expand_path(path):
    path = path.strip().strip('"').strip("'")
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


def ask(prompt, valid=None):
    """Read a line. 'q' quits. If `valid` is given, only those answers (lowercase) are accepted."""
    while True:
        resp = input(prompt).strip()
        if resp.lower() == "q":
            print("Exiting.")
            sys.exit(0)
        if valid is None:
            return resp
        if resp.lower() in valid:
            return resp.lower()
        print(f"Please enter one of {sorted(valid)} or q to quit.")


# ——— cURL parsing ————————————————————————————————————————————————————————
def parse_curl(lines):
    raw = " ".join(lines).replace("^", "")
    m = re.search(r'(https?://[^\s"\'\\]+)', raw)
    if not m:
        return None
    headers = [
        h.replace("\\", "")
        for h in re.findall(r'-H\s*"(.*?)"', raw)
        if not h.lower().startswith("range:")
    ]
    cm = re.search(r'-b\s*"(.*?)"', raw) or re.search(r"-b\s+([^\s]+)", raw)
    return {"url": m.group(1), "headers": headers, "cookie": cm.group(1) if cm else None}


def read_curl():
    print("\nPaste your cURL (Windows) and press Enter twice. 'q' to quit.")
    lines = []
    while True:
        line = input()
        if not line.strip():
            break
        if line.strip().lower() == "q":
            print("Exiting.")
            sys.exit(0)
        lines.append(line)
    item = parse_curl(lines)
    if not item:
        bad("ERROR: No URL found in what you pasted.")
    return item


def url_expiry(url):
    """Return the signed link's expiry (epoch seconds) from its CloudFront Policy, or None."""
    policy = parse_qs(urlparse(url).query).get("Policy", [None])[0]
    if not policy:
        return None
    s = policy.replace("-", "+").replace("_", "=").replace("~", "/")
    s += "=" * (-len(s) % 4)
    try:
        data = json.loads(base64.b64decode(s))
        return int(data["Statement"][0]["Condition"]["DateLessThan"]["AWS:EpochTime"])
    except Exception:
        return None


# ——— Catalog ——————————————————————————————————————————————————————————
def load_catalog(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        warn(f"catalog.json not found at {path}. Falling back to title rules; covers will be asked for.")
        return None
    except (OSError, json.JSONDecodeError) as e:
        warn(f"Could not read the catalog ({e}). Falling back to title rules.")
        return None
    episodes = data.get("episodes", [])
    by_path = {e["audio_path"]: e for e in episodes}
    counts = {}
    for e in episodes:
        counts.setdefault(e["audio_path"].rsplit("/", 1)[-1].lower(), []).append(e)
    by_base = {k: v[0] for k, v in counts.items() if len(v) == 1}
    info(f"Catalog: {len(episodes)} items (generated {data.get('generated', '?')})")
    return {"by_path": by_path, "by_base": by_base, "records": episodes}


def find_record(catalog, url):
    if not catalog:
        return None
    path = unquote(urlparse(url).path).lstrip("/")
    rec = catalog["by_path"].get(path)
    if rec:
        return rec
    return catalog["by_base"].get(path.rsplit("/", 1)[-1].lower())


# ——— Names and tags ————————————————————————————————————————————————————
def safe_filename(name):
    name = name.replace(":", " -")
    name = re.sub(r'[<>"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:150].rstrip(" .")


def album_tag(collection):
    if not collection:
        return ""
    kind, number, title = collection.get("kind"), collection.get("number"), collection.get("title")
    if kind == "club_season":
        return f"Club Season {number}"
    if kind == "album":
        return f"Album {number}: {title}"
    return title or ""


def build_filename(rec):
    number, suffix, title = rec.get("number"), rec.get("suffix") or "", rec["title"]
    if number is not None:
        name = f"#{number:04d}{suffix} {title}"
    else:
        col = rec.get("collection") or {}
        label = col.get("title") or "Adventures in Odyssey"
        track = col.get("track")
        name = f"{label} {track:02d} {title}" if track else f"{label} {title}"
    return safe_filename(name) + ".mp3"


def collection_folder(rec):
    """Folder (inside the download folder) for this item: its album or Club season."""
    tag = album_tag(rec.get("collection"))
    return safe_filename(tag) if tag else ""


def is_bonus_rec(rec):
    return bool(rec.get("bonus")) or bool(rec.get("suffix")) or rec.get("media") == "video" \
        or (rec.get("title") or "").upper().startswith("BONUS")


def tags_from_record(rec):
    col = rec.get("collection") or {}
    if "main_track" in col:          # track among the main episodes; bonus items get no track number
        track = f"{col['main_track']}/{col['main_track_total']}" if col.get("main_track") else ""
    elif col.get("track"):
        track = f"{col['track']}/{col['track_total']}"
    else:
        track = ""
    return {"title": rec["title"], "album": album_tag(col), "track": track, "artist": ARTIST}


# ——— Fallback naming (ported from aio-rename) ——————————————————————————
MINOR_WORDS = {"a", "an", "the", "and", "but", "or", "nor", "for", "so", "yet",
               "as", "at", "by", "in", "of", "on", "per", "to", "via", "vs"}
ACRONYMS = {"AIO", "FBI", "NASA", "USA", "AM", "PM", "TV"}
STRIP_WORDS = {"mp3", "m4a", "flac", "aac", "mp4", "club", "wav"}
CONTRACTIONS = {
    "youre": "you're", "im": "I'm", "dont": "don't", "cant": "can't", "wont": "won't",
    "isnt": "isn't", "shouldnt": "shouldn't", "couldnt": "couldn't", "wouldnt": "wouldn't",
    "theyre": "they're", "weve": "we've", "ive": "I've", "lets": "let's", "whos": "who's",
}


def _preserve_acronyms(word):
    if word.isupper() and len(word) > 1:
        return word
    if word.upper() in ACRONYMS:
        return word.upper()
    return None


def _capitalize_word(word):
    kept = _preserve_acronyms(word)
    if kept is not None:
        return kept
    if not word:
        return word
    if "'" in word:
        first, rest = word.split("'", 1)
        return first[:1].upper() + first[1:].lower() + "'" + rest.lower()
    return word[:1].upper() + word[1:].lower()


def title_case(raw):
    words = raw.replace("_", " ").strip().split()

    def one(w, is_first, is_last):
        if "-" not in w:
            kept = _preserve_acronyms(w)
            if kept is not None:
                return kept
            if not is_first and not is_last and w.lower() in MINOR_WORDS:
                return w.lower()
            return _capitalize_word(w)
        parts = []
        for i, p in enumerate(w.split("-")):
            kept = _preserve_acronyms(p)
            if kept is not None:
                parts.append(kept)
            elif i > 0 and p.lower() in MINOR_WORDS:
                parts.append(p.lower())
            else:
                parts.append(_capitalize_word(p))
        return "-".join(parts)

    out = [one(w, i == 0, i == len(words) - 1) for i, w in enumerate(words)]
    if out:
        out[0] = _capitalize_word(out[0])
        out[-1] = _capitalize_word(out[-1])
    return " ".join(out)


def clean_slug_title(raw):
    placeholder = "BTVPLACEHOLDER"
    s = re.sub(r"(?i)(?:(?<=^)|(?<=_)|(?<=\.)|(?<=-))b_tv(?=(?:$|_|\.|-))", placeholder, raw)
    s = re.sub(r"[\(\[\{].+?[\)\]\}]", " ", s)
    s = re.sub(r"[._\-]+", " ", s)
    s = re.sub(r"\b(" + "|".join(map(re.escape, STRIP_WORDS)) + r")\b", "", s, flags=re.I)
    s = " ".join(w for w in s.split() if "kbps" not in w.lower())
    m = re.search(r"\bpart\s+\d+\s+of\s+\d+\b", s, flags=re.I)
    if m and s[:m.start()].strip() and not s[:m.start()].rstrip().endswith(","):
        s = s[:m.start()].rstrip() + ", " + s[m.start():]
    s = s.replace(placeholder, "B-TV")
    s = " ".join(CONTRACTIONS.get(w.lower(), w) for w in s.split())
    return re.sub(r"\s{2,}", " ", s).strip()


def fallback_record(url):
    """Best-effort record from the URL's file name when the catalog has no entry."""
    base = unquote(urlparse(url).path).rsplit("/", 1)[-1]
    stem = os.path.splitext(base)[0]
    m = re.match(r"^(\d{1,4})([a-z]?)[-_ .]+(.+)$", stem)
    number, suffix, slug = (int(m.group(1)), m.group(2), m.group(3)) if m else (None, "", stem)
    title = title_case(clean_slug_title(slug)) or stem
    return {"number": number, "suffix": suffix, "title": title, "media": "audio",
            "audio_path": "", "cover_small": "", "cover_medium": "", "collection": None}


# ——— Downloading ————————————————————————————————————————————————————————
def curl_get(url, dest, headers=None, cookie=None, show_progress=False, attempts=1, cancel=None):
    cmd = ["curl", "-#" if show_progress else "-s", "-L", "-f", url]
    for h in headers or []:
        cmd += ["-H", h]
    if cookie:
        cmd += ["-b", cookie]
    cmd += ["-o", str(dest)]
    for i in range(attempts):
        if run_tracked(cmd, cancel=cancel, quiet=not show_progress) == 0:
            return True
        if cancel is not None and cancel.is_set():
            return False
        if i < attempts - 1:
            time.sleep(1.5 ** i)
    return False


def probe_duration(path):
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        return float(res.stdout.decode().strip())
    except ValueError:
        return 0.0


def probe_audio_codec(path):
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name",
         "-of", "csv=p=0", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return res.stdout.decode().strip()


def download_audio(item, dest, quiet=False, cancel=None):
    """API links: the web player's headers first. Pasted links: the URL alone first."""
    url = item["url"]
    show = not quiet
    if show:
        info("Downloading episode...")
    if item.get("browser_headers"):
        if curl_get(url, dest, headers=item["headers"], show_progress=show, attempts=2, cancel=cancel):
            return True
        if cancel is not None and cancel.is_set():
            return False
        if show:
            warn("The request with browser headers failed; trying the plain link...")
        return curl_get(url, dest, show_progress=show, cancel=cancel)
    if curl_get(url, dest, show_progress=show, cancel=cancel):
        return True
    if cancel is not None and cancel.is_set():
        return False
    if show:
        warn("Plain request failed; retrying with the headers from your cURL...")
    return curl_get(url, dest, headers=item["headers"] + ["Range: bytes=0-"],
                    cookie=item["cookie"], show_progress=show, attempts=2, cancel=cancel)


# ——— Cover art ————————————————————————————————————————————————————————
def ffmpeg_cover_cmd(infile, outfile, qscale):
    if UPSCALE_SMALL_COVERS:
        w = f"if(gte(iw,ih),{TARGET_LONG_SIDE},-2)"
        h = f"if(gte(iw,ih),-2,{TARGET_LONG_SIDE})"
    else:
        w = f"if(gte(iw,ih),min({TARGET_LONG_SIDE},iw),-2)"
        h = f"if(gte(iw,ih),-2,min({TARGET_LONG_SIDE},ih))"
    vf = f"scale='{w}':'{h}':flags=lanczos,format=yuvj420p"
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(infile),
            "-map", "0:v:0", "-frames:v", "1", "-vf", vf, "-c:v", "mjpeg", "-q:v", str(qscale),
            "-map_metadata", "-1", "-update", "1", str(outfile)]


def normalize_cover(src):
    """Any image -> baseline RGB JPEG, aspect ratio kept, <= 500 KB preferred, <= 1 MB hard."""
    out = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False).name
    fallback = None
    try:
        for q in QSCALE_STEPS:
            res = subprocess.run(ffmpeg_cover_cmd(src, out, q), stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
            if res.returncode != 0:
                warn(f"Cover conversion failed: {res.stderr.strip()}")
                os.remove(out)
                return None
            size = os.path.getsize(out)
            if size <= PREFERRED_MAX_COVER_BYTES:
                return out
            if size <= HARD_MAX_COVER_BYTES and fallback is None:
                fallback = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False).name
                shutil.copyfile(out, fallback)
        if fallback:
            shutil.move(fallback, out)
            fallback = None
            return out
        warn("Cover could not be made smaller than 1 MB.")
        os.remove(out)
        return None
    finally:
        if fallback and os.path.exists(fallback):
            os.remove(fallback)


def encode_url(url):
    """Percent-encode spaces etc. in the path without touching existing %XX escapes."""
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, quote(p.path, safe="/%:@!$&'()*+,;=-._~"), p.query, p.fragment))


COVER_LOCK = threading.Lock()


def get_cover(url, cache_dir):
    """Download (no credentials) and normalize a cover; results are cached by URL.
    One lock, so a shared album cover is fetched once even when threads ask together."""
    url = encode_url(url)
    with COVER_LOCK:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".jpg")
        if cached.exists() and cached.stat().st_size > 0:
            return cached
        raw = tempfile.NamedTemporaryFile(suffix=".img", delete=False).name
        try:
            info("Downloading cover...")
            if not curl_get(url, raw, attempts=2):
                warn("Cover download failed.")
                return None
            fixed = normalize_cover(raw)
            if not fixed:
                return None
            shutil.copyfile(fixed, cached)
            os.remove(fixed)
            return cached
        finally:
            if os.path.exists(raw):
                os.remove(raw)


# ——— Convert + tag + cover in one ffmpeg pass ———————————————————————————
def build_mp3_cmd(src, cover, out, tags, copy_audio):
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src)]
    if cover:
        cmd += ["-i", str(cover)]
    cmd += ["-map", "0:a:0"]
    if cover:
        cmd += ["-map", "1:v:0"]
    cmd += ["-map_metadata", "-1"]
    cmd += ["-c:a", "copy"] if copy_audio else ["-c:a", "libmp3lame", "-b:a", MP3_BITRATE]
    if cover:
        cmd += ["-c:v", "copy", "-disposition:v:0", "attached_pic",
                "-metadata:s:v:0", "title=Cover", "-metadata:s:v:0", "comment=Cover (front)"]
    cmd += ["-id3v2_version", "3", "-write_id3v1", "0", "-fflags", "+bitexact"]   # bitexact: no encoder tag
    for key, value in tags.items():
        if value:
            cmd += ["-metadata", f"{key}={value}"]
    cmd += ["-f", "mp3", str(out)]
    return cmd


def run_ffmpeg_with_progress(cmd, total_secs, cancel=None):
    full = cmd[:-1] + ["-progress", "pipe:1", "-nostats"] + cmd[-1:]
    err = tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace")
    p = subprocess.Popen(full, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err,
                         text=True, bufsize=1)
    with ACTIVE_LOCK:
        ACTIVE.add(p)
    bar = tqdm(total=max(1.0, total_secs), ncols=80, leave=True,
               bar_format="{percentage:3.0f}%|{bar}|")
    try:
        for line in p.stdout:
            line = line.strip()
            if line.startswith("out_time_ms="):
                try:
                    bar.update(max(0.0, min(total_secs, int(line.split("=", 1)[1]) / 1_000_000) - bar.n))
                except ValueError:
                    pass
        p.wait()
    except KeyboardInterrupt:
        p.terminate()
        raise
    finally:
        bar.close()
        with ACTIVE_LOCK:
            ACTIVE.discard(p)
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    if p.returncode != 0:
        err.seek(0)
        raise RuntimeError(err.read().strip() or f"ffmpeg exited with code {p.returncode}")
    err.close()


def run_ffmpeg_quiet(cmd, cancel=None):
    """Same as above without a progress bar (used when several episodes run at once)."""
    err = tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace")
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err)
    with ACTIVE_LOCK:
        ACTIVE.add(p)
    try:
        while True:
            try:
                p.wait(timeout=0.3)
                break
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.is_set():
                    p.terminate()
                    p.wait()
                    raise Cancelled()
    finally:
        with ACTIVE_LOCK:
            ACTIVE.discard(p)
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    if p.returncode != 0:
        err.seek(0)
        raise RuntimeError(err.read().strip() or f"ffmpeg exited with code {p.returncode}")
    err.close()


# ——— Automatic lookup: session, selection, signed link ————————————————————
def is_blank_or_redacted(value):
    return not value or "redacted" in value.lower()


def build_session(got, token, pin):
    headers = {
        "Authorization": token,
        "X-Experience-Name": got.get("x-experience-name", "Adventures In Odyssey"),
        "Accept": "*/*",
        "Origin": APP_ORIGIN,
        "Referer": APP_ORIGIN + "/",
        "User-Agent": got.get("user-agent", "Mozilla/5.0"),
    }
    if got.get("x-viewer-id"):
        headers["X-VIEWER-ID"] = got["x-viewer-id"]
    if pin:
        headers["X-PIN"] = pin
    return headers


def prompt_session(reason):
    """Ask for a cURL (any request to the Club API). Returns a headers dict, or None if skipped."""
    print(reason)
    print("In the browser: DevTools > Network > right-click a request to fotf.my.site.com >")
    print("Copy > Copy as cURL (Windows). Paste it here, then press Enter twice (Enter alone to skip).")
    lines = []
    while True:
        line = input()
        if not line.strip():
            break
        if line.strip().lower() == "q":
            print("Exiting.")
            sys.exit(0)
        lines.append(line)
    if not lines:
        return None
    item = parse_curl(lines)
    if not item:
        bad("No request found in what you pasted.")
        return None
    got = {}
    for h in item["headers"]:
        if ":" in h:
            k, v = h.split(":", 1)
            got[k.strip().lower()] = v.strip()
    token, pin = got.get("authorization"), got.get("x-pin")
    if is_blank_or_redacted(token):
        warn("The Authorization value is missing, or reads 'redacted', so I need the real one:")
        print("  DevTools > Network > click the request > Headers > Request Headers > Authorization.")
        print("  Select the value (it starts with 'Bearer ') and copy it.")
        token = input("Authorization value (Enter to cancel): ").strip()
        if not token:
            return None
        if not token.lower().startswith("bearer "):
            token = "Bearer " + token
    if is_blank_or_redacted(pin):
        pin = input("Profile PIN (Enter if your profile has none): ").strip()
    if not got.get("x-viewer-id"):
        warn("No X-VIEWER-ID header found; the Club may refuse the requests.")
    good("Session ready.")
    return build_session(got, token, pin)


AUDIO_ACCEPT = "audio/webm,audio/ogg,audio/wav,audio/*;q=0.9,application/ogg;q=0.7,video/*;q=0.6,*/*;q=0.5"


def media_headers(user_agent, byte_range="bytes=0-"):
    """The headers the Club's own web player sends when it fetches an audio file."""
    return [f"User-Agent: {user_agent}", f"Accept: {AUDIO_ACCEPT}", "Accept-Language: en-US,en;q=0.9",
            f"Range: {byte_range}", f"Referer: {APP_ORIGIN}/", "Sec-Fetch-Dest: audio",
            "Sec-Fetch-Mode: no-cors", "Sec-Fetch-Site: same-site", "Accept-Encoding: identity",
            "Priority: u=4", "Pragma: no-cache", "Cache-Control: no-cache"]


LOGIN_HEADERS = {"authorization", "x-pin", "x-viewer-id"}


def api_get_content(session, content_id, full=False, signed_in=True):
    params = ("tag=true&series=true&recommendations=true&player=true&parent=true" if full
              else "player=true")
    headers = session if signed_in else {k: v for k, v in session.items() if k.lower() not in LOGIN_HEADERS}
    req = urllib.request.Request(f"{API_BASE}/content/{content_id}?{params}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")), 200
    except urllib.error.HTTPError as e:
        return None, e.code
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ConnectionError) as e:
        return None, str(e)


def is_signed(url):
    return "Signature" in parse_qs(urlparse(url).query)


def host_allowed(url):
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in ALLOWED_MEDIA_DOMAINS), host


def get_signed_url(session, rec):
    """Ask the Club for this episode's signed download link. Returns (url, problem)."""
    problem = "nourl"
    for full in (False, True):          # short request first, then the parameters the app itself sends
        data, status = api_get_content(session, rec["id"], full=full)
        if status in (401, 403):
            return None, "auth"
        if status == 429:
            return None, "rate"
        if status == 404:
            return None, "missing"
        if status != 200 or not data:
            return None, f"error: {status}"
        url = data.get("download_url")
        if not url:
            problem = "nourl"
            continue
        ok, host = host_allowed(url)
        if not ok:
            return None, f"unexpected host {host}"
        if is_signed(url):
            return url, None
        problem = "unsigned"            # the Club answered without treating us as a signed-in member
    return None, problem


def describe_lookup(data, status):
    """One safe line about an API answer (never prints tokens or signatures)."""
    if status != 200 or not data:
        return f"HTTP {status}", "-", "-", ""
    url = data.get("download_url")
    if not url:
        return "HTTP 200", "no", "-", ""
    exp = url_expiry(url)
    when = datetime.fromtimestamp(exp).strftime("%Y-%m-%d %H:%M") if exp else ""
    return "HTTP 200", "yes", "yes" if is_signed(url) else "no", when


def diagnose(session, rec):
    """Compare the API's answers for one episode. Prints nothing secret; safe to share."""
    print(f"\nDiagnostic for #{rec['number']}{rec.get('suffix', '')} \"{rec['title']}\" (id {rec['id']})")
    print(f"  {'request':34s} {'result':9s} {'link':5s} {'signed':7s} expires")
    last = None
    for label, full, signed_in in (("signed in, short request", False, True),
                                   ("signed in, app's full request", True, True),
                                   ("no login headers, full request", True, False)):
        data, status = api_get_content(session, rec["id"], full=full, signed_in=signed_in)
        result, has, signed, when = describe_lookup(data, status)
        print(f"  {label:34s} {result:9s} {has:5s} {signed:7s} {when}")
        if data and signed_in:
            last = data
        time.sleep(API_DELAY_SECONDS)
    if last:
        print("  media_format:", last.get("media_format"), "| media_variant:", last.get("media_variant"))
        print("  allowedActions:", (last.get("metadata") or {}).get("allowedActions"))
        url = last.get("download_url") or ""
        print("  download path:", "..." + urlparse(url).path[-60:] if url else "(none)")
        if url and is_signed(url) and host_allowed(url)[0]:
            print("  media host, one byte requested three ways:")
            for label, result in probe_variants(url, session.get("User-Agent", "Mozilla/5.0")):
                print(f"    {label:24s} {result}")


def probe_media(url, headers):
    """Request one byte with the given headers. Returns a safe one-line summary."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        hdrs = {k.lower(): v for k, v in e.headers.items()}
        keep = {k: hdrs[k] for k in ("server", "x-cache", "www-authenticate") if k in hdrs}
        try:
            body = e.read(200).decode("utf-8", errors="replace").strip().replace("\n", " ")
        except Exception:
            body = ""
        return f"HTTP {e.code} {keep} {('body: ' + repr(body)) if body else ''}".strip()
    except (urllib.error.URLError, TimeoutError) as e:
        return f"network error: {e}"


def probe_variants(url, user_agent):
    """Try the media link three ways to see which headers the media host cares about."""
    full = {}
    for h in media_headers(user_agent, byte_range="bytes=0-0"):
        k, v = h.split(":", 1)
        full[k.strip()] = v.strip()
    variants = [
        ("no extra headers", {"User-Agent": "curl/8.4.0", "Range": "bytes=0-0"}),
        ("browser UA + Referer", {"User-Agent": user_agent, "Referer": APP_ORIGIN + "/", "Range": "bytes=0-0"}),
        ("full player headers", full),
    ]
    return [(label, probe_media(url, hdrs)) for label, hdrs in variants]


SELECT_RANGE = re.compile(r"^#?(\d{1,4})\s*-\s*#?(\d{1,4})$")
SELECT_ONE = re.compile(r"^#?(\d{1,4})([a-z]?)$", re.I)
SELECT_GROUP = re.compile(r"^(album|club)\s*(\d{1,3})$", re.I)


def parse_selection(text, catalog):
    """'891, 886-897, 167a, album 57, club 7' -> (records in order, unknown tokens, bonus items skipped).
    Albums, Club seasons and ranges give the main episodes only; a lettered number such as 731a
    asks for that one bonus item."""
    records = catalog["records"]
    chosen, seen, unknown = [], set(), []
    skipped_bonus = 0

    def add(rec):
        if rec["audio_path"] not in seen:
            seen.add(rec["audio_path"])
            chosen.append(rec)

    for token in [t.strip() for t in text.split(",") if t.strip()]:
        m = SELECT_GROUP.match(token)
        if m:
            kind = "club_season" if m.group(1).lower() == "club" else "album"
            group = [r for r in records if r.get("collection")
                     and r["collection"]["kind"] == kind and r["collection"]["number"] == int(m.group(2))]
            group.sort(key=lambda r: r["collection"]["track"])
            skipped_bonus += sum(1 for r in group if is_bonus_rec(r))
            group = [r for r in group if not is_bonus_rec(r)]
            (unknown.append(token) if not group else [add(r) for r in group])
            continue
        m = SELECT_RANGE.match(token)
        if m:
            lo, hi = sorted((int(m.group(1)), int(m.group(2))))
            group = sorted((r for r in records if r["number"] is not None and not is_bonus_rec(r)
                            and lo <= r["number"] <= hi), key=lambda r: r["number"])
            (unknown.append(token) if not group else [add(r) for r in group])
            continue
        m = SELECT_ONE.match(token)
        if m:
            num, suf = int(m.group(1)), m.group(2).lower()
            group = [r for r in records if r["number"] == num and (r["suffix"] or "") == suf]
            (unknown.append(token) if not group else [add(r) for r in group])
            continue
        unknown.append(token)
    return chosen, unknown, skipped_bonus


def api_item(session, rec):
    """Ask the Club for a signed link. Returns (item, problem)."""
    url, problem = get_signed_url(session, rec)
    if problem:
        return None, problem
    ua = session.get("User-Agent", "Mozilla/5.0")
    return {"url": url, "headers": media_headers(ua), "cookie": None, "browser_headers": True}, None


def show_media_probe(url, session):
    info("Media host, one byte requested three ways:")
    for label, result in probe_variants(url, session.get("User-Agent", "Mozilla/5.0")):
        info(f"  {label:24s} {result}")


def describe(rec):
    return f"#{rec['number']}{rec.get('suffix', '')}" if rec.get("number") is not None else rec["title"]


def run_single(rec, session, base_dir, want_cover, cache_dir, catalog):
    """One episode on the main thread: full progress bars and prompts. Ctrl+C cancels."""
    info(f"\n{os.path.join(collection_folder(rec), build_filename(rec))}")
    try:
        item, problem = api_item(session, rec)
        if problem in ("auth", "unsigned"):
            return "auth"
        if problem == "rate":
            bad("The Club asked us to slow down (HTTP 429). Wait a while before trying again.")
            return "rate"
        if problem:
            bad(f"Could not get a link for this episode ({problem}).")
            return "failed"
        status = process_item(item, base_dir, want_cover, cache_dir, catalog, rec=rec)
        if status == "failed":
            show_media_probe(item["url"], session)
            if rec.get("number") is not None:
                info(f"For the API side as well, type:  d {rec['number']}{rec.get('suffix', '')}")
        return status
    except KeyboardInterrupt:
        terminate_active()
        warn("\nCancelled.")
        return "cancelled"


def run_parallel(recs, session, base_dir, want_cover, cache_dir, catalog, workers):
    """Several episodes at once. No prompts: existing files are skipped and a missing cover is left out."""
    total = len(recs)
    cancel, stop_new = threading.Event(), threading.Event()
    gate = ApiGate(API_DELAY_SECONDS)
    probed = [False]
    results = [None] * total

    def work(i, rec):
        label = f"[{i + 1}/{total}] {describe(rec)}"
        if cancel.is_set():
            return i, "cancelled"
        if stop_new.is_set():
            return i, "stopped"
        gate.wait(cancel)
        if cancel.is_set():
            return i, "cancelled"
        item, problem = api_item(session, rec)
        if problem in ("auth", "unsigned"):
            stop_new.set()
            return i, "auth"
        if problem == "rate":
            stop_new.set()
            bad(f"{label} The Club asked us to slow down (HTTP 429).")
            return i, "rate"
        if problem:
            bad(f"{label} could not get a link ({problem})")
            return i, "failed"
        status = process_item(item, base_dir, want_cover, cache_dir, catalog, rec=rec,
                              quiet=True, interactive=False, cancel=cancel, label=label)
        if status == "failed" and not probed[0]:
            probed[0] = True
            show_media_probe(item["url"], session)
        return i, status

    def cancel_all(reason):
        if not cancel.is_set():
            warn(f"\n{reason} Cancelling; stopping running transfers...")
        cancel.set()
        stop_new.set()
        terminate_active()

    info(f"Downloading {total} episodes ({workers} at a time). Press q to cancel.")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(work, i, rec) for i, rec in enumerate(recs)]
        pending = set(futures)
        try:
            while pending:
                done, pending = wait(pending, timeout=0.4, return_when=FIRST_COMPLETED)
                for f in done:
                    if not f.cancelled():
                        i, st = f.result()
                        results[i] = st
                if q_pressed():
                    cancel_all("q pressed.")
                    for f in list(pending):
                        f.cancel()
        except KeyboardInterrupt:
            cancel_all("Ctrl+C pressed.")
            for f in futures:
                f.cancel()
        for f in futures:                      # let running workers finish their cleanup
            try:
                if not f.cancelled():
                    i, st = f.result()
                    results[i] = st
            except KeyboardInterrupt:
                terminate_active()
    return [(rec, results[i] or "cancelled") for i, rec in enumerate(recs)]


def print_summary(results):
    counts = {}
    for _, st in results:
        counts[st] = counts.get(st, 0) + 1
    parts = [f"{counts.get('ok', 0)} downloaded"]
    if counts.get("skipped"):
        parts.append(f"{counts['skipped']} already there")
    if counts.get("failed"):
        parts.append(f"{counts['failed']} failed")
    not_run = counts.get("cancelled", 0) + counts.get("stopped", 0) + counts.get("auth", 0) + counts.get("rate", 0)
    if not_run:
        parts.append(f"{not_run} not finished")
    info("\nFinished: " + ", ".join(parts) + ".")
    failed = [describe(r) for r, st in results if st == "failed"]
    if failed:
        warn("Failed: " + ", ".join(failed))


def run_selection(selection, session_box, base_dir, want_cover, cache_dir, catalog, workers):
    """Download the selected episodes. Handles an expired session by asking for a new one."""
    if len(selection) > CONFIRM_ABOVE:
        if ask(f"{len(selection)} episodes selected. Continue? (y/n): ", valid={"y", "n"}) == "n":
            return
    final = {}                                   # audio_path -> (record, last status)
    pending = list(selection)
    while pending:
        if len(pending) == 1:
            results = [(pending[0], run_single(pending[0], session_box[0], base_dir, want_cover,
                                                cache_dir, catalog))]
        else:
            results = run_parallel(pending, session_box[0], base_dir, want_cover, cache_dir, catalog,
                                   max(1, min(workers, len(pending))))
        for rec, st in results:
            final[rec["audio_path"]] = (rec, st)
        if any(st == "rate" for _, st in results):
            if len(selection) > 1:
                print_summary(list(final.values()))
            bad("The Club asked us to slow down. Stopping; wait a while before trying again.")
            return
        if not any(st == "auth" for _, st in results):
            break
        redo = [r for r, st in results if st in ("auth", "stopped")]
        new = prompt_session("The Club did not accept the session (expired, or the token or PIN is wrong).")
        if not new:
            bad("Stopping: no valid session.")
            break
        session_box[0] = new
        pending = redo
    if len(selection) > 1:
        print_summary([final[r["audio_path"]] for r in selection if r["audio_path"] in final])


# ——— One episode, start to finish ——————————————————————————————————————
def process_item(item, base_dir, want_cover, cache_dir, catalog, rec=None,
                 quiet=False, interactive=True, cancel=None, label=""):
    """Returns "ok", "skipped", "failed" or "cancelled".
    quiet=True (used when several episodes run at once): one status line per stage, no bars,
    and no prompts (interactive=False): an existing file is skipped, a missing cover is left out."""
    kinds = {"info": info, "good": good, "warn": warn, "bad": bad}

    def say(kind, msg):
        kinds[kind](f"{label} {msg}" if (quiet and label) else msg)

    def cancelled():
        return cancel is not None and cancel.is_set()

    url = item["url"]
    expiry = url_expiry(url)
    if expiry and expiry < time.time():
        say("bad", "ERROR: This link expired on "
            + datetime.fromtimestamp(expiry).strftime("%Y-%m-%d %H:%M")
            + ". Get a fresh link.")
        return "failed"

    rec = rec or find_record(catalog, url)
    if rec:
        if not quiet:
            good(f"Found: {rec['title']}")
            if rec.get("media") == "video":
                warn("This item is a video; only its audio track will be kept.")
    else:
        rec = fallback_record(url)
        say("warn", f"Not in the catalog. Using the file name: {rec['title']}")
    dest_dir = Path(base_dir) / collection_folder(rec)
    final_path = dest_dir / build_filename(rec)
    shown = os.path.relpath(final_path, base_dir)

    if final_path.exists():
        if interactive:
            if ask("That file already exists. Overwrite? (y/n): ", valid={"y", "n"}) == "n":
                info("Skipped.")
                return "skipped"
        else:
            say("info", "already there, skipped")
            return "skipped"

    dest_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{os.getpid()}_{threading.get_ident()}"
    part = dest_dir / f".aio_dl_{tag}.part"
    tmp_out = dest_dir / f".aio_out_{tag}.mp3.part"
    try:
        if cancelled():
            return "cancelled"
        if quiet:
            say("info", "downloading")
        if not download_audio(item, part, quiet=quiet, cancel=cancel):
            if cancelled():
                return "cancelled"
            say("bad", "ERROR: download failed (the link may have expired).")
            return "failed"
        size = part.stat().st_size if part.exists() else 0
        duration = probe_duration(part)
        if size < MIN_AUDIO_BYTES or duration <= 0:
            say("bad", "ERROR: the download is not a valid audio file (probably an error page).")
            return "failed"
        if cancelled():
            return "cancelled"

        cover_file = None
        if want_cover:
            cover_url = rec.get("cover") or rec.get("cover_medium") or rec.get("cover_small")
            if not cover_url and interactive:
                cover_url = ask("Cover URL (blank to skip): ")
            if cover_url:
                cover_file = get_cover(cover_url, cache_dir)
                if not cover_file:
                    say("warn", "continuing without a cover.")

        copy_audio = probe_audio_codec(part) == "mp3"
        if quiet:
            say("info", "copying audio" if copy_audio else "converting to MP3")
        else:
            info("Copying audio..." if copy_audio else f"Converting to MP3 ({MP3_BITRATE}), tagging...")
        tags = tags_from_record(rec)

        def encode(cover):
            cmd = build_mp3_cmd(part, cover, tmp_out, tags, copy_audio)
            if quiet:
                run_ffmpeg_quiet(cmd, cancel)
            else:
                run_ffmpeg_with_progress(cmd, duration, cancel)

        try:
            encode(cover_file)
        except Cancelled:
            return "cancelled"
        except RuntimeError as e:
            say("bad", f"ERROR: ffmpeg failed: {e}")
            if not cover_file:
                return "failed"
            say("warn", "retrying without the cover...")
            try:
                encode(None)
                cover_file = None
            except Cancelled:
                return "cancelled"
            except RuntimeError as e2:
                say("bad", f"ERROR: ffmpeg failed again: {e2}")
                return "failed"

        os.replace(tmp_out, final_path)
        say("good", f"Done: {shown}" + ("" if cover_file else "  (no cover)"))
        return "ok"
    finally:
        for leftover in (part, tmp_out):
            try:
                if leftover.exists():
                    leftover.unlink()
            except OSError:
                pass


# ——— Main loop ————————————————————————————————————————————————————————
def manual_paste_flow(base_dir, want_cover, cache_dir, catalog):
    queue = []
    while True:
        item = read_curl()
        if item:
            queue.append(item)
        n = len(queue)
        if n == 0:
            return
        choice = input(
            f"{n} in the queue. Press Enter to download, or 'a' + Enter to add another: "
        ).strip().lower()
        if choice == "q":
            print("Exiting.")
            sys.exit(0)
        if choice != "a":
            break
    results = []
    try:
        for i, item in enumerate(queue, start=1):
            if len(queue) > 1:
                print(f"\n=== Episode {i} of {len(queue)} ===")
            results.append((fallback_record(item["url"]),
                            process_item(item, base_dir, want_cover, cache_dir, catalog)))
    except KeyboardInterrupt:
        terminate_active()
        warn("\nCancelled.")
        return
    if len(queue) > 1:
        print_summary(results)


def main(argv=None):
    ap = argparse.ArgumentParser(description="AIO Downloader V6")
    ap.add_argument("--catalog", default=str(app_dir() / "catalog.json"))
    ap.add_argument("--settings", default=str(app_dir() / "aio_settings.json"))
    ap.add_argument("--dir", help="download folder (skips the prompt)")
    ap.add_argument("--cover", choices=["y", "n"], help="embed cover art (skips the prompt)")
    ap.add_argument("--threads", type=int, help=f"episodes downloaded at once (1-{MAX_WORKERS})")
    args = ap.parse_args(argv)

    settings = load_settings(args.settings)
    catalog = load_catalog(args.catalog)

    remembered = settings.get("download_dir")
    if args.dir:
        raw_dir = args.dir
    elif remembered:
        raw_dir = ask(f"Download dir [{remembered}], or type a new one: ") or remembered
    else:
        raw_dir = ask("Download dir (e.g. ~/Downloads): ")
    base_dir, note = resolve_download_dir(raw_dir)
    os.makedirs(base_dir, exist_ok=True)
    info(f"Saving to: {base_dir}")
    if note:
        warn(note)

    try:
        workers = int(args.threads or settings.get("workers") or DEFAULT_WORKERS)
    except (TypeError, ValueError):
        workers = DEFAULT_WORKERS
    workers = max(1, min(MAX_WORKERS, workers))
    settings.update({"download_dir": base_dir, "workers": workers})
    save_settings(args.settings, settings)

    want_cover = (args.cover or ask("Embed cover art? (y/n): ", valid={"y", "n"})) == "y"
    cache_dir = app_dir() / "cover_cache"
    print("You can hit 'q' at any prompt to quit. Ctrl+C cancels a download in progress.")

    session_box = [None]
    can_auto = bool(catalog and any(r.get("id") for r in catalog["records"]))
    if can_auto:
        session_box[0] = prompt_session("\nPaste a session once to look episodes up automatically.")
        if session_box[0]:
            info(f"(Other commands: s = new session, t N = set thread count (now {workers}), "
                 "d 500 = diagnose an episode.)")
    elif catalog:
        warn("This catalog has no episode IDs; rebuild it with the latest build_catalog.py for automatic lookup.")

    while True:
        if session_box[0]:
            text = input("\nEpisode, range, or album/club (891, 886-897, album 57, club 7), "
                        "'p' to paste a cURL, or 'q' to quit: ").strip()
        elif can_auto:
            text = input("\nNo session yet. 's' to paste one, 'p' to paste a cURL for one episode, "
                         "or 'q' to quit: ").strip()
        else:
            text = "p"
        low = text.lower()
        if low == "q":
            print(Fore.GREEN + "\nMade by NotKevin :tomsmirk:")
            break
        if low == "p":
            manual_paste_flow(base_dir, want_cover, cache_dir, catalog)
        elif low == "s" and can_auto:
            session_box[0] = prompt_session("Paste a fresh cURL from a Club request.") or session_box[0]
        elif not session_box[0]:
            if text:
                warn("Paste a session first (type s), or 'p' to paste a cURL for one episode.")
        elif re.match(r"^t\s*\d+$", low):
            workers = max(1, min(MAX_WORKERS, int(re.sub(r"\D", "", low))))
            settings["workers"] = workers
            save_settings(args.settings, settings)
            info(f"Threads set to {workers}.")
        elif re.match(r"^d\s+\S", low):
            picked, unknown, _ = parse_selection(text[1:].strip(), catalog)
            if unknown or not picked:
                warn("Not found in the catalog: " + ", ".join(unknown or [text[1:].strip()]))
            else:
                diagnose(session_box[0], picked[0])
        elif text:
            selection, unknown, skipped_bonus = parse_selection(text, catalog)
            if unknown:
                warn("Not found in the catalog: " + ", ".join(unknown))
            if skipped_bonus:
                info(f"Skipping {skipped_bonus} bonus item(s). Type a lettered number such as 731a to get one.")
            if selection:
                run_selection(selection, session_box, base_dir, want_cover, cache_dir, catalog, workers)
        if not session_box[0] and not can_auto:
            if input("Press Enter for another or 'q'+Enter to quit: ").strip().lower() == "q":
                print(Fore.GREEN + "\nMade by NotKevin :tomsmirk:")
                break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(0)
