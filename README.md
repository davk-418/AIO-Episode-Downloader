# Overview

The AIO Downloader is a Python script to download episodes from Adventures in Odyssey. Current version is Windows OS only

<details>
  <summary>Requirements</summary>

  ### Programs and Packages

- **Windows OS 10 or 11**
- **Python 3.13** or higher
- **FFmpeg** (added to PATH) ([Tutorial](https://www.hostinger.com/tutorials/how-to-install-ffmpeg#How_to_install_FFmpeg_on_Windows) or run `winget install Gyan.FFmpeg`)
- **tqdm**
- **colorama**

These can be installed by running `pip install tqdm colorama`
</details>

# Usage

- Download [AIO_Downloader_V6.py](AIO_Downloader_V6.py) and [catalog.09-22.json](catalog.09-22.json) from the main directory
- Move both files into the root directory of a folder
- Open Command Prompt and `cd` to the directory where the files live
- Run:
 ```text
  python "AIO_Downloader_V6.py
  ```
- When prompted, paste your download directory or press `Enter` to set to your current directory
- When prompted, type `y` or `n` and press `Enter` to choose whether or not to embed a cover image

  <details>
    <summary>Automatic Episode Lookup:</summary>

    - Open `https://app.adventuresinodyssey.com/` in your browser and sign in to your account and user profile
      
    - Follow the steps displayed in Command Prompt to find your cURL. If there are no requests in the Network tab, reload your page. To          simplify, type `fotf` in the DevTools search bar
      
    - Paste your cURL into CMD and press Enter twice
 
    - In CMD, type in the episode number(s), club season, or album you wish to download. (e.g. `season 3`, `album 30`, `3, 300-305`
 
    - The Downloader will automatically search for the episode(s) and begin download
  </details>


