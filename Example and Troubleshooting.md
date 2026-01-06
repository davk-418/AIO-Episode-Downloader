# Example:

  `python AIO_Downloader_V3.py`

  **Enter download directory:**
  
  `C:\Users\YourName\Downloads`

  **Paste cURL command (copied from browser DevTools):**
  
  `curl "https://example.com/audio/file.m4a" ^
  -H "User-Agent: ..." ^
  -H "Authorization: Bearer abc123"`
  
  (*Press Enter twice after pasting.)*

  **Choose cover art option:**
  
  `Embed cover art? (y/n):` y

  **Decide whether to keep original MP3:**
  
  `Keep original MP3 after embedding cover? (y/n):` n

  **Paste cover image URL:**
  
  `https://example.com/cover.jpg`

  **Wait for download and conversion. Progress bars will show status.**

  **Check your Downloads folder for file_cover.mp3.**

---

# Troubleshooting:

  `Download failed (token expired)`: 
  
  **Recopy a fresh cURL from DevTools and try again.**
  
  
  `File too small (HTML stub)`:
  
  **Ensure your cURL includes all headers and cookies. Recopy from DevTools.**
  

  `fmpeg/ffprobe not found`:
  
  **Add ffmpeg\bin to PATH and reopen Command Prompt.**
  
  
  Cover image won’t download: 

  Use a direct image URL ending in .jpg or .png. V3 supports .webp and .heic by converting them.
