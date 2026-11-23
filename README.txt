SEAMLESS SHORTS STUDIO & PLAYER - EASY WINDOWS USER GUIDE
===========================================================

STEP 1: EXTRACT THIS ZIP FOLDER FIRST!
--------------------------------------
Before opening anything:
1. Right-click the downloaded .zip file in Windows File Explorer.
2. Click "Extract All..." and click "Extract".
3. Open the new extracted folder (do NOT run files from inside the
   zipped preview window, or Windows cannot save files or find tools).


-----------------------------------------------------------
OPTION A: ZERO-INSTALL BROWSER PLAYER (Fastest, Offline)
-----------------------------------------------------------
Best if you already have your Shorts videos and a song on your PC
and just want to preview transitions and export sliced audio right away.

1. Double-click:  Seamless_Shorts_Player.html
   (Opens directly in Edge or Chrome - nothing to install, works offline!)
2. Drag and drop onto the page (or click the buttons):
   - Your Shorts (.mp4 / .mov / .webm), OR a whole workspace folder,
     OR a .zip archive
   - Your full-length song (.mp3 / .wav / .m4a)
   - Optional: your YouTube Studio .csv export to show real video titles
3. Preview & check every transition:
   - Click "Audition Cut (−2s)" or press [ and ] to jump 2 seconds
     before each cut and hear the music stay on-beat.
   - Drag the timeline playhead, view the song waveform, and balance
     Video Clip Audio vs. Replacement Song in the Audio Mixer.
4. Export your results in Step 3:
   - Download individual WAV song slices with 15 ms micro-fades
     ("Download WAV Song Slices (.zip)"), manifest.json, or
     "Download All as ZIP".


-----------------------------------------------------------
OPTION B: FULL PYTHON STUDIO ON YOUR WINDOWS PC
(Downloads YouTube Playlist + Syncs & Stitches Full Video)
-----------------------------------------------------------
Best if you want the app to download a YouTube Shorts playlist,
replace the audio in every Short with FFmpeg, and build a single
stitched verification video automatically.

1. Double-click:  START_HERE.bat
   - On Mac or Linux, double-click:  START_HERE.command
2. If Windows Defender SmartScreen pops up ("Windows protected your PC"):
   - Click "More info" -> "Run anyway".
   - (START_HERE.bat is a plain text batch file you can inspect in Notepad.)
3. First-time automatic setup:
   - START_HERE.bat automatically searches for Python on your PC
     (or sets up Python/FFmpeg/yt-dlp if missing) and opens Seamless
     Shorts Studio in a browser tab running right on YOUR OWN PC.
   - Leave the black Studio terminal window open while using the app;
     close it when you are finished.
4. Inside Studio:
   - Paste your YouTube Shorts playlist link.
   - Choose or drag & drop any .mp3 / .wav / .m4a song file.
   - Click "Run & Sync Shorts".
   - Watch progress and logs live, preview cuts in the 9:16 player,
     and download your synced Parts or Verification MP4!


-----------------------------------------------------------
TROUBLESHOOTING (PLAIN ENGLISH)
-----------------------------------------------------------
1. "My browser says a clip can't play or shows a black screen?"
   Some downloaded YouTube Shorts use AV1 or VP9 video formats that
   not every browser or editor decodes directly.
   - Switch the player toggle from "Clips A/B" to "Full Verify" (always H.264), OR
   - In Studio, check "Browser-Safe H.264 re-encode for individual Parts"
     before clicking "Run & Sync Shorts".

2. "YouTube asks to confirm you're not a bot?"
   If YouTube asks for sign-in confirmation during downloads on your PC:
   - In Studio, open the optional "Browser Cookies (optional)" dropdown
     and select the browser where you are signed into YouTube
     (for example: Chrome or Microsoft Edge).
   - Or if running from Windows PowerShell inside this extracted folder:
     python seamless_shorts_agent.py --cookies-from-browser chrome
     (or replace chrome with edge).

3. "I want to try a different song without re-downloading Shorts!"
   Keep "Reuse already-downloaded Shorts if present" checked in Studio,
   pick or drop your new song, and click "Run & Sync Shorts" again. It
   re-syncs all Parts in seconds.

4. "Where are my finished videos saved on my PC?"
   Inside the shorts_workspace folder next to START_HERE.bat:
   - Synced Shorts:      shorts_workspace\2_repurposed_shorts\
   - Verification video: shorts_workspace\3_verification_full_playthrough.mp4
