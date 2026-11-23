# Hosting Seamless Shorts Studio & Player Online

You can host this project in two different ways depending on whether you want **zero server maintenance** or the **full YouTube + FFmpeg pipeline**.

---

## Comparison: Standalone Static App vs. Container Studio

| Option | What It Runs | What You Need | Best For |
|---|---|---|---|
| **Option 1: GitHub Pages (Static Web App)** | Pure browser app (`web/` -> `dist/site/`) | Free GitHub account (no server, no Docker, no Python) | Drag-and-drop Shorts + song preview, `-2s` cut auditioning, audio mixing, WAV song slices, and ZIP exports — 100% private in your browser |
| **Option 2: Container Hosting (Docker / HF Spaces / Fly.io / Render)** | Full Python Studio (`seamless_shorts_agent.py --ui`) + FFmpeg + yt-dlp + Node.js | Docker host (local PC, Hugging Face Spaces, Fly.io, or Render) | Running `Run & Sync Shorts` on a server, FFmpeg H.264 verification stitching, and built-in `/api/health` monitoring |

> **Important Tip About YouTube Downloads:**
> Cloud servers (Render, Fly.io, Hugging Face Spaces) use shared datacenter IP addresses that YouTube often rate-limits or challenges with "Sign in to confirm you're not a bot". For downloading playlists reliably, running **`START_HERE.bat`** on your own Windows PC (or **`START_HERE.command`** on Mac/Linux) — or dropping existing clips into the **Standalone Browser Player** — is usually the smoothest experience.

---

## Option 1: Static Hosting (GitHub Pages)

See [`deploy/GITHUB_PAGES.md`](./GITHUB_PAGES.md). Nothing is created or published automatically; GitHub Pages deploys only if and when you explicitly create a repository and enable Pages.

---

## Option 2: Container Hosting (Port `8766`, Healthcheck `/api/health`)

All container configs bind `HOST=0.0.0.0`, default `PORT=8766`, pre-generate `sample_song.mp3` via `--make-demo-song`, and check `/api/health`.

### Local Docker / Docker Compose

```bash
# Single container
docker build -t seamless-shorts-studio .
docker run --rm -p 8766:8766 seamless-shorts-studio

# Or with persistent workspace volume
docker compose up --build
```

Then open your Docker host URL in your browser (for example, on the machine running Docker, port `8766`).

### Hugging Face Spaces (Free Docker Space)

1. Create a Docker Space on Hugging Face.
2. Copy `deploy/huggingface/README.md` to the Space root as `README.md` (contains `sdk: docker` and `app_port: 8766`).
3. Push `Dockerfile`, `.dockerignore`, `seamless_shorts_agent.py`, `START_HERE.bat`, `START_HERE.command`, `README.txt`, and `web/`.

### Render (`deploy/render.yaml`)

1. Connect your repository in Render as a **Blueprint** using `deploy/render.yaml` (or create a Docker Web Service pointing to `./Dockerfile`).
2. Render sets `PORT` automatically; the container's `CMD` binds `0.0.0.0:${PORT:-8766}` and exposes `/api/health` for health checks.

### Fly.io (`deploy/fly.toml`)

```bash
fly launch --copy-config --config deploy/fly.toml --dockerfile Dockerfile
fly deploy -c deploy/fly.toml --dockerfile Dockerfile
```

Fly.io checks `GET /api/health` on `internal_port = 8766` and keeps machines stopped when idle (`auto_stop_machines = "stop"`).
