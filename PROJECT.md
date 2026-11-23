# Project: Seamless Shorts Studio & Player (gen4 plan — revalidated 2026-11-18)

Original request (authoritative, read first): `/home/antigravity/.agents/teamwork/ORIGINAL_REQUEST.md`
Prior survey reports (still valid, code unchanged since): `/home/antigravity/.agents/teamwork/explorer_{1,2,3}/handoff.md`

## Architecture

ONE unified web app (`web/`) serves both user goals 1 and 4:

1. **Standalone / hosted mode** (Goal 1 + Goal 3): the page runs 100% client-side (opened from disk as a
   single self-contained HTML file `Seamless_Shorts_Player.html`, or hosted as static files on GitHub Pages).
   User drags in Shorts + a song (or a workspace folder / zip) and previews the synced result. Nothing uploaded.
2. **Studio mode** (Goal 2 + Goal 4): the same `web/index.html` is served by `seamless_shorts_agent.py --ui`
   at `/`. The app auto-detects the backend (`GET /api/health`) and shows the extra Studio panels
   (tool status, run pipeline, song upload, progress, logs) with range-streamed media from `/media/*`.
3. **Hosted full Studio** (Goal 3, optional): Docker image of the Python Studio for Render/Fly/HF Spaces.
   Static hosting of the standalone app on GitHub Pages is prepared but NOT published (needs user consent).
4. **Distribution**: flat zip `upload/YT_1m_to_full_song_1x.zip` (built with `rm` + `zip -j`) containing launchers,
   Python engine, README, web assets and the single-file player. Because `zip -j` flattens paths, the server
   MUST find web assets either in `<script_dir>/web/` or directly in `<script_dir>/`.

Constraints (from ORIGINAL_REQUEST): never tell the user to open a VM localhost URL; never bypass YouTube bot
checks (cookies only as optional user-side setting); no GitHub repo/Pages creation without explicit consent;
AV1/VP9 parts may not decode in all browsers (handle gracefully; verification mp4 is H.264).

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1 | Standalone web app | Zero-install client page; drag-drop / file picker for files, folder (webkitdirectory) and `.zip` (bundled JSZip) | M1 | Goal 1 |
| F2 | Synced preview + cut navigation | 9:16 player; workspace review (verification mp4 + manifest cut points, or Part sequence) and build-in-browser (source Shorts + song → sequential song offsets, clips play muted with song synced); jump to cut, audition cut at −2 s, prev/next cut | M1 | Goal 1 |
| F3 | Audio replacement + mixer | Load/replace song in browser; video-vs-song volume, mute; re-plan offsets on song change | M1 | Goal 1 |
| F4 | Download center | Download individual parts / verification video / all as zip (JSZip, incl. manifest); export plan manifest.json; per-part song slices as WAV with 15 ms fades | M1 | Goal 1 |
| F5 | Modern UI + theme | Dark/light toggle (persisted, file:// safe), responsive layout (desktop/tablet/phone), clear "super easy" 1-2-3 flow, accessible controls | M1 | Goal 4 |
| F6 | Timeline + waveform | Clip blocks track, cut markers, canvas waveform (Web Audio decode), draggable playhead/click-seek, keyboard shortcuts | M1 | Goal 4 |
| F7 | Studio status & job control | In Studio mode: tool pills, run form (playlist, song select/upload, max shorts, fade ms, skip download, browser-safe, optional cookies-from-browser), progress bar, live logs, polling `/api/state` | M1 (UI) + M2 (API) | Goal 4 |
| F8 | Windows launcher hardening | `START_HERE.bat`: `py -3`/`python`/registry/standard dirs/AppData Scripts; never `python3`; PATH refresh after winget; clear plain-language messages; keeps window open on error; CRLF | M2 | Goal 2 |
| F9 | Tool detection | ffmpeg/ffprobe/yt-dlp detection incl. winget Gyan.FFmpeg locations, `%APPDATA%\Python\Python3xx\Scripts`, local `./bin`; `python -m yt_dlp` fallback; works without ffprobe | M2 | Goal 2 |
| F10 | Server: web serving, range, CORS | Serve web app at `/`, `/web/<asset>`, `/<asset>` (dir or flat layout), RFC 7233 range/206/416, HEAD, CORS + OPTIONS, `/api/health`, `/media/audio/<name>`, path-traversal safe, HOST/PORT env | M2 | Goal 2 |
| F11 | Distribution zip + README | `tools/build_dist.sh` (rm + zip -j) incl. web assets + single-file player; plain-language README.txt for the Windows user | M4 | Goal 2 / deliverables |
| F12 | Container hosting | `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `deploy/` (Render, Fly, HF Spaces) with healthcheck | M3 | Goal 3 |
| F13 | Static hosting | GitHub Pages workflow + `tools/build_pages.sh` → `dist/site/`; consent-gated instructions | M3 | Goal 3 |
| F14 | Test suite | `tests/unit/` (per milestone) + `tests/e2e/` (opaque-box) all passing | M1–M5 + E2E track | deliverables |
| F15 | Single-file player | `tools/build_standalone.py` inlines CSS+JS+JSZip into `web/Seamless_Shorts_Player.html` that works from file:// | M1 | Goal 1 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Unified Web App (Goals 1+4) | F1–F7(UI), F15; owns `web/*`, `tools/build_standalone.py`, `tests/unit/test_web_*.py` | contracts only | DONE (g6_worker_web) |
| M2 | Python Studio server + launchers (Goal 2) | F7(API)–F10; owns `seamless_shorts_agent.py`, `START_HERE.bat`, `START_HERE.command`, `tests/unit/test_server_*.py`, `tests/unit/test_launcher_*.py` | contracts only | DONE (g6_worker_server) |
| M3 | Hosting (Goal 3) | F12, F13; owns `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `deploy/`, `.github/workflows/`, `tools/build_pages.sh`, `tests/unit/test_hosting_*.py` | contracts (M1 file names, M2 HOST/PORT/health) | DONE (g6_worker_hosting_dist) |
| M4 | Packaging & plain-language docs | F11; owns `README.txt`, `tools/build_dist.sh`, `upload/YT_1m_to_full_song_1x.zip`, `tests/unit/test_dist_*.py` | M1, M2, M3 | DONE (g6_worker_hosting_dist) |
| M5 | Final: pass 100% E2E (Tiers 1-4) + Tier 5 hardening | all | M1–M4 + TEST_READY.md | DONE (`223` tests: `222` pass, `1` docker skip; `g7_reviewer_final3` APPROVE, `g7_challenger_final3` CONFIRM, `g7_auditor_final` CLEAN) |
| E2E | E2E Testing Track | owns `tests/e2e/`, `TEST_INFRA.md`, `TEST_READY.md` | requirements only | DONE (`186/186` E2E + `37` unit passing; TEST_READY.md published) |

## Interface Contracts

### Server (`seamless_shorts_agent.py --ui`) → Web app
- Asset lookup order for names in whitelist {`index.html`,`styles.css`,`app.js`,`jszip.min.js`,`Seamless_Shorts_Player.html`}:
  `<script_dir>/web/<name>` then `<script_dir>/<name>`; if `index.html` not found, `/` falls back to embedded `STUDIO_HTML`.
- `GET /` and `/index.html` → web `index.html`. `GET /web/<name>` and `GET /<name>` → the asset with correct MIME
  (`text/html; charset=utf-8`, `text/css`, `application/javascript`). Unknown names → 404.
- `GET /api/health` → `200 {"ok": true, "app": "seamless-shorts-studio", "version": "<str>", "mode": "studio"}`.
- `GET /api/state` → preserves existing keys (`tools`, `playlist_url`, `active_song_path`, `is_demo_song`, `audio_files`, `source_count`, `parts`, `verify_exists`, `verify_mtime`, `job{running,step,progress,logs,error}`) AND includes aliases/extras (`version`, `sources`, `active_playlist`, `active_song`, `manifest`, `has_verify`, `fade_ms`, `song_name`). Web app accepts both sets.
- `POST /api/run` JSON `{playlist_url, song_path, max_downloads, fade_ms, skip_download, browser_safe_h264, cookies_from_browser?}` → 200 / 409 if running.
- `POST /api/upload-song` raw body + `X-Filename` header → 200 JSON with saved name.
- Range-streamed (GET/HEAD, `Accept-Ranges: bytes`, 206 + `Content-Range`, 416 on bad range, `?download=1` → attachment):
  `/media/verify`, `/media/part/<filename>`, `/media/source/<filename>`, `/media/audio/<filename>` (song files listed in `audio_files`).
  Names are basenames only; any traversal (`..`, `/`, `\`, absolute) → 404/400.
- `GET /download/all.zip`, `GET /download/toolkit.zip` unchanged.
- CORS on every response: `Access-Control-Allow-Origin: *`, `Access-Control-Allow-Methods: GET, POST, OPTIONS, HEAD`,
  `Access-Control-Allow-Headers: *`, `Access-Control-Expose-Headers: Content-Range, Accept-Ranges, Content-Length`; `OPTIONS` → 204.
- Env: `HOST` / `PORT` override defaults (cloud); flags `--host`, `--port`, `--no-browser` keep working. Port auto-increments
  if busy; the console prints the real URL and the browser is opened on the real bound port.

### Web app
- Files: `web/index.html` (classic `<script src="jszip.min.js">` and `<script src="app.js">`, relative `styles.css`; NO ES modules,
  NO CDN, works from file://), `web/styles.css`, `web/app.js`, `web/jszip.min.js` (genuine JSZip, MIT header kept),
  `web/Seamless_Shorts_Player.html` (generated by `python3 tools/build_standalone.py`; everything inlined).
- Mode detection: if `location.protocol` is http(s) and `fetch('api/health')` returns `{ok:true}` → Studio mode, else standalone.
- Manifest schema consumed: `parts[*].{part_num, filename, source_filename, duration, song_start, song_end, verify_start, verify_end, codec, width, height}`, root `fade_ms`, `song_name`.
- Build-in-browser plan: clips in filename order; `song_start[i] = Σ durations before i`; last clip truncated when song ends; fades `fade_ms` (default 15).

## Code Layout
```
/home/antigravity/
├── seamless_shorts_agent.py      # M2: engine, CLI, server (serves web/), embedded fallback UI
├── START_HERE.bat                # M2: Windows launcher (CRLF)
├── START_HERE.command            # M2: macOS/Linux launcher
├── README.txt                    # M4: plain-language guide
├── Dockerfile, docker-compose.yml, .dockerignore   # M3
├── deploy/                       # M3: render.yaml, fly.toml, huggingface/, GITHUB_PAGES.md, HOSTING.md
├── .github/workflows/pages.yml   # M3 (template; not pushed anywhere)
├── web/                          # M1: index.html, styles.css, app.js, jszip.min.js, Seamless_Shorts_Player.html
├── tools/                        # build_standalone.py (M1), build_pages.sh (M3), build_dist.sh (M4)
├── tests/__init__.py, tests/unit/ (milestone-owned), tests/e2e/ (E2E track-owned)
├── upload/YT_1m_to_full_song_1x.zip  (symlinked at ./YT_1m_to_full_song_1x.zip)  # M4
└── shorts_workspace/             # existing test data (do not delete clips/parts/verify/manifest)
```

## Test Conventions
- stdlib `unittest` (pytest-compatible). Canonical full-suite command: `cd /home/antigravity && python3 -m unittest discover -s tests -t . -v`
  (also runnable with `python3 -m pytest -q tests` if pytest installed). Every test dir has `__init__.py`.
- Server tests start `seamless_shorts_agent.py --ui --no-browser --port <free>` as a subprocess and MUST use
  `urllib.request.build_opener(urllib.request.ProxyHandler({}))` (HTTP_PROXY is set in the VM). Always terminate the subprocess.
- Tests must not download from YouTube, must not modify `shorts_workspace/` source clips/parts/verify, and must clean up temp files.
- Browser-behaviour tests may use a headless browser if available in the VM (node v24 present); otherwise they must skip gracefully, not fail.
