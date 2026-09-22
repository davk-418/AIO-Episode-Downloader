# Overview

A Windows only command‑line downloader for Adventures in Odyssey episodes. It retrieves episodes using either automatic API lookup or manual cURL requests, then converts them into clean, tagged MP3 files with normalized cover art. It is designed for personal use and does not store any login information.

<details>
  <summary>Requirements</summary>

  ### Programs and Packages

- **Windows OS 10 or 11**
- **Python 3.13** or higher
- **FFmpeg** (added to PATH)
  - [Tutorial](https://www.hostinger.com/tutorials/how-to-install-ffmpeg#How_to_install_FFmpeg_on_Windows)
  
  OR
  - run `winget install Gyan.FFmpeg`
- **tqdm**
- **colorama**

These can be installed by running `pip install tqdm colorama`
</details>

---

# Usage

1. Download [AIO_Downloader_V6.py](AIO_Downloader_V6.py) and [catalog.09-22.json](catalog.09-22.json) and place both files in the same directory.

2. Open Command Prompt and navigate to the directory:
```text
cd path\to\your\folder
```
3. Run the downloader:
```text
python AIO_Downloader_V6.py
```

4. When prompted for a download directory:
- Press **Enter** to use the current folder, or  
- Type a full or relative path to choose a different location.

5. Choose whether to embed cover art (`y` or `n`).

6. Provide a cURL request depending on the mode you want to use:

<details>
  <summary><strong>Automatic Mode</strong></summary>

  1. Sign in at https://app.adventuresinodyssey.com/  
  2. Open **DevTools → Network**  
     - Reload the page if no requests appear  
     - Search for `fotf` to filter Club API calls  
  3. Right‑click any request to `fotf.my.site.com`  
     Choose **Copy → Copy as cURL (Windows)**  
  4. Paste the cURL into Command Prompt and press **Enter twice**  
  5. Enter your selection:  
     - Single episode: `891`  
     - Range: `886-897`  
     - Bonus: `167a`  
     - Album: `album 57`  
     - Club season: `club 7`  
     - Mixed: `891, 300-305, album 30`  
</details>

<details>
  <summary><strong>Manual Mode</strong></summary>

  1. Type `p` at the prompt  
  2. Paste a single **Copy as cURL (Windows)** audio request  
  3. Press **Enter twice** to finish  
  4. The downloader will process and download that episode
</details>



The downloader will fetch signed links, download multiple episodes in parallel, convert them, tag them, and save them into album/season‑named folders.

To cancel an active batch, press **q**. Temporary files are cleaned and the prompt returns.

---

`AIO_Downloader_V6.py` and `catalog.09-22.json` are both required: the downloader file downloads the episodes and converts them, and the catalog file records each episodes URL, cover image, and other relevant metadata

The script will automatically generate an `aio_settings.json` file in the working directory; this stores only your download folder and thread count, and you can change these by editing the file.

A `cover_cache` folder will also be created to store normalized JPEG versions of album and episode covers, allowing the downloader to reuse them without re-downloading or reprocessing the same images.


