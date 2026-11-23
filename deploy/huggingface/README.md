---
title: Seamless Shorts Studio
emoji: 🎬
colorFrom: cyan
colorTo: blue
sdk: docker
app_port: 8766
pinned: false
short_description: Sync one continuous song across vertical Shorts with cut-point auditioning.
---

# Seamless Shorts Studio — Hugging Face Space

This Space runs the full **Seamless Shorts Studio & Player** Docker container (`seamless_shorts_agent.py --ui`) with FFmpeg, Node.js, yt-dlp, and a built-in 120 BPM demo track (`sample_song.mp3`).

## Setup Steps

1. Create a new Space on Hugging Face choosing **Docker** as the SDK.
2. Upload or push the project root files (`Dockerfile`, `.dockerignore`, `seamless_shorts_agent.py`, `START_HERE.bat`, `START_HERE.command`, `README.txt`, and `web/`) plus this `README.md` (place this `README.md` at the Space repository root).
3. Hugging Face automatically reads the frontmatter above (`sdk: docker`, `app_port: 8766`) and exposes the Studio web UI after the `/api/health` check passes.

## Notes

- Because this runs inside a cloud container, **YouTube may ask datacenter IPs to sign in** (`Sign in to confirm you're not a bot`). If playlist downloading is blocked in the cloud, either:
  - Drag and drop already-downloaded Shorts + your song directly into the browser interface (client-side mode), or
  - Run `START_HERE.bat` (Windows) / `START_HERE.command` (Mac/Linux) on your own computer.
- Never upload private browser cookies to a public Space.
