import subprocess
import os
import re
import shutil
import sys
import tempfile

from colorama import init as colorama_init, Fore
from tqdm import tqdm
from mutagen.mp3 import MP3

# ——— Init ——————————————————————————————————————————————————————————————
colorama_init(autoreset=True)

# ——— Pre-req checks ————————————————————————————————————————————————
def ensure_available(cmd):
    if shutil.which(cmd) is None:
        print(f"ERROR: '{cmd}' not found on PATH. Please install it first.")
        sys.exit(1)

for tool in ("curl", "ffmpeg", "ffprobe"):
    ensure_available(tool)

# ——— Helpers ———————————————————————————————————————————————————————
def expand_path(path):
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))

def safe_input(prompt, valid=None, allow_quit=False):
    while True:
        resp = input(prompt).strip()
        if allow_quit and resp.lower() == "q":
            print("Exiting.")
            sys.exit(0)
        if valid is None or resp.lower() in valid:
            return resp.lower()
        print(f"Please enter one of {valid}{' or Q to quit' if allow_quit else ''}.")

def download_image(url, dest):
    try:
        subprocess.run(
            ["curl", "-s", "-L", "-f", "-o", dest, url],
            check=True
        )
    except subprocess.CalledProcessError:
        print("ERROR: Failed to download image.")
        return False
    return True

def get_media_duration(path):
    """Return duration in seconds via ffprobe."""
    res = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=True
    )
    return float(res.stdout.decode().strip())

def run_ffmpeg_with_progress(cmd, total_secs):
    """
    Launch ffmpeg with -progress pipe:1 -nostats appended to cmd.
    Parse out_time_ms from stdout and update a tqdm bar,
    showing only bar+percentage.
    """
    full_cmd = cmd + ["-progress", "pipe:1", "-nostats"]
    p = subprocess.Popen(
        full_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1
    )
    bar = tqdm(
        total=total_secs,
        ncols=80,
        leave=True,
        bar_format='{percentage:3.0f}%|{bar}|'
    )
    while True:
        line = p.stdout.readline()
        if not line:
            break
        line = line.strip()
        if line.startswith("out_time_ms="):
            ms = int(line.split("=", 1)[1])
            sec = ms / 1_000_000
            bar.update(sec - bar.n)
        elif line.startswith("progress=") and line.endswith("end"):
            break
    p.wait()
    bar.close()
    if p.returncode != 0:
        raise subprocess.CalledProcessError(p.returncode, full_cmd)

def convert_to_mp3(path):
    mp3_path = os.path.splitext(path)[0] + ".mp3"
    dur = get_media_duration(path)
    print("Converting to MP3:")
    try:
        run_ffmpeg_with_progress([
            "ffmpeg", "-hide_banner",
            "-i", path, "-vn",
            "-acodec", "libmp3lame",
            "-b:a", "320k",        # ← higher-quality bitrate
            mp3_path
        ], total_secs=dur)
    except subprocess.CalledProcessError:
        print(f"ERROR: ffmpeg failed to convert '{path}' to MP3.")
        return None
    os.remove(path)
    return mp3_path

def embed_cover_ffmpeg(mp3_path, img_path):
    """
    Embed cover art with a simple ffmpeg call (no tqdm bar).
    """
    out_path = os.path.splitext(mp3_path)[0] + "_cover.mp3"
    try:
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", mp3_path, "-i", img_path,
            "-map", "0", "-map", "1",
            "-c", "copy",
            "-id3v2_version", "3",
            out_path
        ], check=True)
    except subprocess.CalledProcessError as e:
        print("ERROR: ffmpeg failed to embed cover art:", e)
        return None
    os.remove(mp3_path)
    return out_path

# ——— Core Download Flow —————————————————————————————————————————————
def run_download(base_dir, embed_cover):
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

    raw = " ".join(lines).replace("^", "")
    m = re.search(r'(https?://[^\s"\'\\]+)', raw)
    if not m:
        print("ERROR: No URL found.")
        return
    url = m.group(1)

    hdrs = [
        h.replace("\\", "")
        for h in re.findall(r'-H\s*"(.*?)"', raw)
        if not h.lower().startswith("range:")
    ]
    hdrs.append("Range: bytes=0-")

    fname = os.path.basename(url.split("?", 1)[0]).replace("-", "_")
    out_path = os.path.join(base_dir, fname)

    cmd = ["curl", "-#", "-L", "-f", url]
    for h in hdrs:
        cmd += ["-H", h]
    cmd += ["-o", out_path]

    try:
        subprocess.run(cmd, cwd=base_dir, check=True)
    except subprocess.CalledProcessError:
        print("ERROR: Download failed.")
        return

    size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    if size < 50_000:
        print("ERROR: File too small; probably an HTML stub.")
        os.remove(out_path)
        return

    if not out_path.lower().endswith(".mp3"):
        mp3 = convert_to_mp3(out_path)
        if not mp3:
            return
        out_path = mp3

    print(f"✅ MP3 ready: {out_path}")

    if embed_cover == "y":
        img_url = input("Cover URL (blank to skip): ").strip()
        if img_url:
            ext = os.path.splitext(img_url.split("?", 1)[0])[1].lower()
            ext = ext if ext in (".jpg", ".jpeg", ".png") else ".jpg"
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp_img = tmp.name
            if download_image(img_url, tmp_img):
                new = embed_cover_ffmpeg(out_path, tmp_img)
                if new:
                    out_path = new
                    print(f"✅ Cover embedded: {out_path}")
                else:
                    print("ERROR: Embedding cover failed.")
            os.remove(tmp_img)

# ——— Main Loop ———————————————————————————————————————————————
if __name__ == "__main__":
    base_directory = expand_path(
        safe_input("Download dir (e.g. ~/Downloads): ", allow_quit=True)
    )
    embed_choice = safe_input(
        "Embed cover art? (y/n): ",
        valid=("y", "n"),
        allow_quit=True
    )
    print("You can hit 'q' at any prompt to quit.")

    while True:
        run_download(base_directory, embed_choice)
        cont = input("Press Enter for another or 'q'+Enter to quit: ").strip().lower()
        if cont == "q":
            print(Fore.GREEN + "\nMade by NotKevin :tomsmirk:")
            break