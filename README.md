# Description (Windows OS Specific)

Python script to download Adventures in Odyssey episodes with optional cover embedding

# Requirements

### Programs and Packages

- **Python 3.13** or higher

- **FFmpeg** (added to PATH) ([Tutorial](https://www.hostinger.com/tutorials/how-to-install-ffmpeg#How_to_install_FFmpeg_on_Windows))

- **tqdm**

- **colorama**

- **mutagen**

These can be installed by running `pip install tqdm colorama mutagen`

- [`colorama`](https://pypi.org/project/colorama/) — colored terminal output.
- [`tqdm`](https://pypi.org/project/tqdm/) — progress bars.
- [`mutagen`](https://pypi.org/project/mutagen/) — audio metadata

---
# Usage

- Open console (Command Prompt) and drag the file into it. Press Enter to run the file.
- When prompted for directory (where file will save), paste directory and press Enter (see [Directory](Directory.txt) on how to find directory)
- When prompted for cover art embedding (`Embed cover art? (y/n):`), type `y` and press Enter to embed cover art or type `n` and press Enter to skip cover art embedding
- If you chose `y` for cover art, you’ll see: `Keep original MP3 after embedding cover? (y/n):` Type `y` to keep both versions, or `n` to only keep the cover‑embedded copy.
- When prompted for cURL (see [Tutorial](tutorial.gif) to find the cURL), paste and press Enter twice. If all goes well, episode will download.
- If you chose cover art, you’ll be asked: `Cover URL (blank to skip):` Paste a direct image link (ending in .jpg or .png). These can be found by searching for the episode on Google, right clicking the image, and clicking `Copy Image Link`.
- When prompted `Press Enter for another or 'q'+Enter to quit:`, press Enter to download another episode or press `q` then Enter to quit.

See [Example and Troubleshooting](Example and Troubleshooting.md) to find an example and directions for issues. 
