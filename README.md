# Seamless Shorts Studio & Player

Browser-only player and local Python Studio for **Flow Juggle** YouTube Shorts — slice one full-length song cleanly across ordered Shorts clips with micro-fades, cut-point auditioning, waveform timeline, and synced export.

## Quick Start

- **Zero-Install Browser Player:** Open `web/Seamless_Shorts_Player.html` (or deploy `web/` statically via `.github/workflows/pages.yml`), drop your Shorts + song, preview transitions with **Audition Cut (-2s)**, and export parts or faded WAV slices.
- **Windows PC Studio:** Double-click `START_HERE.bat` (auto-detects Python, `ffmpeg`, and `yt-dlp`).
- **macOS / Linux Studio:** Run `./START_HERE.command` or `python3 seamless_shorts_agent.py --ui`.
- **Cloud / Docker Hosting:** See `deploy/HOSTING.md` and `deploy/GITHUB_PAGES.md`.

See `README.txt` for step-by-step plain-language instructions.
