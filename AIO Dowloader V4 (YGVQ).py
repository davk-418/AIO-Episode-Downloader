import subprocess
import os
import re
import shutil
import sys
import tempfile
import time

from colorama import init as colorama_init, Fore, Style
from tqdm import tqdm

# ——— Init ——————————————————————————————————————————————————————————————
colorama_init(autoreset=True)

# ——— Banner & Message Functions ———————————————————————————————————————
def print_banner():
    banner = r"""
           _____ ____    _____  
     /\   |_   _/ __ \  |  __ \ 
    /  \    | || |  | | | |  | |
   / /\ \   | || |  | | | |  | |
  / ____ \ _| || |__| | | |__| |
 /_/    \_\_____\____/  |_____/ 
    """
    print(Fore.MAGENTA + banner)
    print(Fore.MAGENTA + Style.BRIGHT + " Made by NotKevin, updated by YGVQ\n")

def print_error(msg):
    print(Fore.RED + "[ERROR] " + msg)

def print_success(msg):
    print(Fore.GREEN + "[SUCCESS] " + msg)

def print_info(msg):
    print(Fore.CYAN + "[INFO] " + msg)

# ——— Pre-req checks ————————————————————————————————————————————————————
def ensure_available(cmd):
    if shutil.which(cmd) is None:
        print_error(f"'{cmd}' not found on PATH. Please install it first.")
        sys.exit(1)

for tool in ("curl", "ffmpeg", "ffprobe"):
    ensure_available(tool)

# ——— Helpers ———————————————————————————————————————————————————————
def expand_path(path):
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))

def safe_input(prompt, valid=None, allow_quit=False):
    while True:
        resp = input(Fore.YELLOW + prompt).strip()
        if allow_quit and resp.lower() == "q":
            print_info("Exiting.")
            sys.exit(0)
        if valid is None or resp.lower() in valid:
            return resp.lower()
        print_error(f"Please enter one of {valid}{' or Q to quit' if allow_quit else ''}.")

def parse_cookie(raw):
    m = re.search(r'-b\s*"(.*?)"', raw)
    if m:
        return m.group(1)
    m2 = re.search(r'-b\s+([^\s]+)', raw)
    return m2.group(1) if m2 else None

def sanitize_filename(name):
    name = name.replace("-", "_")
    return re.sub(r'[<>:"/\\|?*]+', "_", name)

def unique_path(path):
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    i = 1
    while True:
        candidate = f"{root}_{i}{ext}"
        if not os.path.exists(candidate):
            return candidate
        i += 1

def run_with_retries(cmd, attempts=3, backoff_base=1.5, cwd=None):
    for i in range(attempts):
        try:
            subprocess.run(cmd, cwd=cwd, check=True)
            return True
        except subprocess.CalledProcessError:
            if i < attempts - 1:
                time.sleep(backoff_base ** i)
            else:
                return False

def download_with_headers(url, dest, hdrs, cookie=None):
    cmd = ["curl", "-s", "-L", "-f", url]
    for h in hdrs:
        cmd += ["-H", h]
    if cookie:
        cmd += ["-b", cookie]
    cmd += ["-o", dest]
    return run_with_retries(cmd, attempts=3)

def get_media_duration(path):
    res = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=True
    )
    val = res.stdout.decode().strip()
    try:
        return float(val)
    except ValueError:
        res2 = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "stream=duration",
             "-of", "csv=p=0", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True
        )
        val2 = res2.stdout.decode().strip()
        try:
            return float(val2) if val2 else 0.0
        except ValueError:
            return 0.0

def run_ffmpeg_with_progress(cmd, total_secs):
    full_cmd = cmd + ["-progress", "pipe:1", "-nostats"]
    p = subprocess.Popen(
        full_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1
    )
    bar = tqdm(
        total=max(1.0, total_secs),
        ncols=80,
        leave=True,
        bar_format='{percentage:3.0f}%|{bar}|'
    )
    try:
        while True:
            line = p.stdout.readline()
            if not line:
                break
            line = line.strip()
            if line.startswith("out_time_ms="):
                ms = int(line.split("=", 1)[1])
                sec = ms / 1_000_000
                bar.update(max(0.0, sec - bar.n))
            elif line.startswith("progress=") and line.endswith("end"):
                break
        p.wait()
    finally:
        bar.close()
    if p.returncode != 0:
        raise subprocess.CalledProcessError(p.returncode, full_cmd)

def convert_to_mp3(path):
    mp3_path = os.path.splitext(path)[0] + ".mp3"
    dur = get_media_duration(path)
    print_info("Converting to MP3...")
    try:
        run_ffmpeg_with_progress([
            "ffmpeg", "-hide_banner",
            "-i", path, "-vn",
            "-acodec", "libmp3lame",
            "-b:a", "320k",
            mp3_path
        ], total_secs=dur)
    except subprocess.CalledProcessError:
        print_error(f"ffmpeg failed to convert '{path}' to MP3.")
        return None
    try:
        os.remove(path)
    except OSError:
        pass
    return mp3_path

def process_and_embed_image(mp3_path, img_url, hdrs, cookie=None, keep_original=False):
    tmp_img = None
    tmp_conv = None
    out_path = os.path.splitext(mp3_path)[0] + "_cover.mp3"

    print_info("Downloading image...")
    src_ext = os.path.splitext(img_url.split("?", 1)[0])[1].lower()
    src_ext = src_ext if src_ext in (".jpg", ".jpeg", ".png", ".webp", ".heic") else ".jpg"
    tmp_img = tempfile.NamedTemporaryFile(suffix=src_ext, delete=False).name
    ok = download_with_headers(img_url, tmp_img, hdrs, cookie=cookie)
    if not ok:
        print_error("Image download failed.")
        if tmp_img and os.path.exists(tmp_img):
            os.remove(tmp_img)
        return None

    print_info("Preparing image for embedding...")
    is_png = src_ext == ".png"
    is_jpg = src_ext in (".jpg", ".jpeg")

    if not (is_png or is_jpg):
        try:
            tmp_conv = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", tmp_img, "-c:v", "png", tmp_conv
            ], check=True)
            embed_path = tmp_conv
            codec = "png"
        except subprocess.CalledProcessError as e:
            print_error(f"Image conversion failed: {e}")
            for p in (tmp_img, tmp_conv):
                if p and os.path.exists(p): os.remove(p)
            return None
    else:
        embed_path = tmp_img
        codec = "mjpeg" if is_jpg else "png"

    print_info("Embedding cover into MP3...")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", mp3_path, "-i", embed_path,
            "-map", "0:a", "-map", "1:v",
            "-c:a", "copy", "-c:v", codec,
            "-disposition:v:0", "attached_pic",
            "-id3v2_version", "3",
            "-metadata:s:v", "title=",
            out_path
        ], check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"ffmpeg failed to embed cover art: {e}")
        return None
    finally:
        for p in (tmp_img, tmp_conv):
            if p and os.path.exists(p): os.remove(p)
        if not keep_original:
            try:
                if os.path.exists(mp3_path):
                    os.remove(mp3_path)
            except OSError:
                pass

    return out_path

# ——— Core Download Flow —————————————————————————————————————————————
def run_download(base_dir, embed_cover, keep_original=False):
    print_info("\nPaste your cURL (Windows) and press Enter twice. 'q' to quit.")
    lines = []
    while True:
        line = input()
        if not line.strip():
            break
        if line.strip().lower() == "q":
            print_info("Exiting.")
            sys.exit(0)
        lines.append(line)

    raw = " ".join(lines).replace("^", "")
    m = re.search(r'(https?://[^\s"\'\\]+)', raw)
    if not m:
        print_error("No URL found.")
        return
    url = m.group(1)

    hdrs = [
        h.replace("\\", "")
        for h in re.findall(r'-H\s*"(.*?)"', raw)
        if not h.lower().startswith("range:")
    ]
    hdrs.append("Range: bytes=0-")
    cookie = parse_cookie(raw)

    fname = sanitize_filename(os.path.basename(url.split("?", 1)[0]))
    out_path = unique_path(os.path.join(base_dir, fname))

    print_info("Downloading episode...")
    cmd = ["curl", "-#", "-L", "-f", url]
    for h in hdrs:
        cmd += ["-H", h]
    if cookie:
        cmd += ["-b", cookie]
    cmd += ["-o", out_path]
    ok = run_with_retries(cmd, attempts=3, cwd=base_dir)
    if not ok:
        print_error("Download failed. Token may be expired. Paste a fresh cURL and try again.")
        return

    size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    if size < 50_000:
        print_error("File too small; probably an HTML stub.")
        try:
            os.remove(out_path)
        except OSError:
            pass
        return

    if not out_path.lower().endswith(".mp3"):
        mp3 = convert_to_mp3(out_path)
        if not mp3:
            return
        out_path = mp3

    print_success(f"MP3 ready: {out_path}")

    if embed_cover == "y":
        img_url = input(Fore.YELLOW + "Cover URL (blank to skip): ").strip()
        if img_url:
            new_path = process_and_embed_image(
                out_path, img_url, hdrs, cookie=cookie, keep_original=keep_original
            )
            if new_path:
                print_success(f"Cover embedded: {new_path}")
            else:
                print_error("Embedding cover failed.")
        else:
            print_info("Skipping cover embedding.")

# ——— Main Loop ———————————————————————————————————————————————
if __name__ == "__main__":
    print_banner()
    base_dir = expand_path(
        safe_input("Download dir (e.g. ~/Downloads): ", allow_quit=True)
    )
    if not os.path.exists(base_dir):
        os.makedirs(base_dir)

    embed_choice = safe_input(
        "Embed cover art? (y/n): ",
        valid=("y", "n"),
        allow_quit=True
    )

    keep_original_mp3 = False
    if embed_choice == "y":
        keep_choice = safe_input(
            "Keep original MP3 after embedding cover? (y/n): ",
            valid=("y", "n"),
            allow_quit=True
        )
        keep_original_mp3 = (keep_choice == "y")

    print_info("You can hit 'q' at any prompt to quit.")

    while True:
        run_download(base_dir, embed_choice, keep_original=keep_original_mp3)
        cont = input(Fore.YELLOW + "Press Enter to download another episode or 'q'+Enter to quit: ").strip().lower()
        if cont == "q":
            print_success("\nAll done! Thanks for using AIOD. Created by NotKevin, updated by YGVQ")
            break
