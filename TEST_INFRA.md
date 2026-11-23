# E2E Test Infrastructure: Seamless Shorts Studio & Player

## 1. Test Philosophy & Requirement-Driven Methodology
- **Opaque-Box Testing**: All E2E tests verify observable external behavior (HTTP responses, headers, byte ranges, CLI exit codes/outputs, FFmpeg media outputs, zip archives, DOM state, Web Audio/Canvas/Video browser behavior) strictly against `ORIGINAL_REQUEST.md`, `PROJECT.md`, and `README.txt`.
- **4-Tier Methodology** across all **15 features (`F1–F15`)**:
  - **Tier 1 — Feature Coverage (`>= 75` tests; `>= 5` happy-path tests per feature F1–F15)**: Category-partition equivalence classes covering normal standalone & studio flows.
  - **Tier 2 — Boundary, Corner & Error Cases (`>= 75` tests; `>= 5` tests per feature F1–F15)**: Boundary values (`0` vs `None`, `"false"` string booleans, ultra-short fades, OOB/inverted/empty HTTP ranges, `409` concurrency, traversal vectors `../`/`%2e%2e%2f`/`\`/`:`/`\x00`, corrupted/empty zips & CSVs, `Total` row & `_EF_O_bx_g4` leading-underscore IDs, `file://` `localStorage` errors, codec decode errors, missing `ffprobe`, empty `PATH`).
  - **Tier 3 — Cross-Feature Pairwise Interactions (`>= 15` tests)**: Multi-feature end-to-end interactions (e.g., zip drop -> A/B player + `-2s` audition; CSV + song replacement -> truncation + WAV slice + JSZip export; Studio upload -> `/api/state` -> `/media/audio/*` Range 206; flat `zip -j` extraction -> `/` + `/api/health` + `/Seamless_Shorts_Player.html`; static build w/o `/api/health` -> Standalone auto-fallback; missing `ffprobe` -> Studio `ffmpeg` fallback + streaming).
  - **Tier 4 — Real-World Application Scenarios (`9` tests)**:
    1. Real FFmpeg `--skip-download` pipeline in isolated `TemporaryDirectory` verifying `Part_01..`, H.264/yuv420p/1080x1920 `3_verification_full_playthrough.mp4`, `15 ms` fade, cumulative timestamps, and last-clip truncation.
    2. Real headless Chromium CDP Studio E2E on live `seamless_shorts_agent.py --ui` (9 workspace clips, cut markers, `-2s` audition, Prev/Next cut, theme toggle, 0 errors).
    3. Real headless Chromium CDP Standalone `file://` E2E (`web/Seamless_Shorts_Player.html`, `.zip` + CSV `_EF_O_bx_g4` + song swap + faded WAV slices & JSZip bundle verified back in Python).
    4. Real extracted `YT_1m_to_full_song_1x.zip` E2E (flat Studio server + `file://` extracted player in Chromium).
    5. Real static Pages build (`tools/build_pages.sh` -> Python `SimpleHTTPRequestHandler`) in headless Chromium confirming graceful Standalone fallback.
    6. Cross-origin CORS + Range streaming E2E from browser `fetch()` across two local origins (`OPTIONS`, `206`, `Expose-Headers`).
    7. Mixed-codec / mixed-resolution (`av1` + `vp9`, `720x1280` + `1080x1920` + `1080x2400`) stage containment & graceful codec error fallback in Chromium.
    8. Non-technical Windows & Cloud readiness validation (`START_HERE.bat` CRLF + discovery ladder + `README.txt` clarity + Docker/Compose/Fly/Render/HF Spaces port/health contract consistency).
    9. Real 3-clip H.264 `Clips A/B` sequential playback in headless Chromium (`Part 1 -> Part 2 -> Part 3 -> pause at completion`, asserting zero Clip 2 loop).
  - **Tier 5 — Adversarial Hardening & Regression (`11` E2E + `7` new unit tests)**:
    - `virtualClockMode` `Loop: Current Clip` & playable-clip recovery, preloaded corrupt standby `codecError` virtual-clock fallback & recovery, rapid `Play->Pause->Play` `AbortError` guard, timeline click/scrub resume while playing, `codecError` jump pausing both video slots, Studio `fade_ms: 0` `#fadeMs` preservation, adversarial ZIP duplicate basenames & 0-byte audio + manifest XSS coercion, `STUDIO_HTML` fallback XSS escaping, `VERIFY_FILE` atomic swap without mid-stitch partial exposure, `yt-dlp` arg-injection `400` & `--` separator, mixed-FPS zero-drift verification & MP3 truncation shortfall fix, `HEAD /download/all.zip` parity, `parse_int_param` float rejection, `find_free_port` range & float validation, tiny-clip skip, WCAG 2.1 AA light-theme contrast (`>= 4.5:1`), CSV exact-header & `Views` `null` & `Infinity` guard, `START_HERE.bat` `find /I "\WindowsApps\"` & `call`, and `README.txt` strict CRLF.

## 2. Feature Inventory (F1–F15)
| ID | Feature | Key Verification Focus |
|---|---|---|
| F1 | Standalone Web App | Client-side execution, file/folder/zip dropzone, CSV RFC 4180 enrichment, clear workspace |
| F2 | Synced Preview + Cut Nav | 9:16 `object-fit: contain`, A/B dual-video + Full Verify switch, `-2s` cut audition, Prev/Next cut, truncation, codec fallback |
| F3 | Audio Replacement + Mixer | Song load/swap re-planning, video/song volume sliders, mute toggle, `fade_ms` `0` vs `15` preservation |
| F4 | Download Center | Single Part MP4/WAV, `manifest.json`, 15 ms faded PCM16 WAV slices, JSZip bundle exports |
| F5 | Modern UI + Theme | Dark/Light theme toggle & `localStorage` (`file://` safe), 3-step workflow, responsive widths, XSS escaping |
| F6 | Timeline + Waveform | Proportional clip blocks, cut markers, DPR canvas waveform, pointer drag/seek, keyboard shortcuts (`Space/K/J/L/[/]/M`) |
| F7 | Studio Status & Job Control | Tool pills, `/api/state` backward + alias keys, `/api/upload-song`, `/api/run` (`400`/`409`/`0`/`"false"`/cookies), live logs |
| F8 | Windows Launcher Hardening | `START_HERE.bat` CRLF, no `python3` cmd, `WindowsApps` stub filter, Registry + standard paths, `winget` PATH refresh, `START_HERE.command` |
| F9 | Tool Detection | `setup_paths()` idempotency, Windows AppData/WinGet discovery, `yt-dlp` module/CLI, `ffmpeg -i` fallback when `ffprobe` absent |
| F10 | Server Web / Range / CORS | Dir + flat `web/` resolution, RFC 7233 `206`/`416`, `HEAD` parity, CORS + `OPTIONS 204`, traversal rejection, `HOST`/`PORT` env |
| F11 | Distribution Zip + README | Flat `YT_1m_to_full_song_1x.zip` (9 files) + symlink + `unzip -t`, plain-language Windows `README.txt` (no `python3`/VM URLs) |
| F12 | Container Hosting | `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `deploy/{render.yaml,fly.toml,huggingface/README.md,HOSTING.md}` (`8766`, `/api/health`) |
| F13 | Static Hosting | `tools/build_pages.sh` (`SITE_DIR`/arg), `.nojekyll`, `.github/workflows/pages.yml`, `deploy/GITHUB_PAGES.md` consent gate |
| F14 | Test Suite Discoverability & Isolation | `unittest` discovery (`tests/unit/` + `tests/e2e/`), stdlib-only, `ProxyHandler({})`, read-only `shorts_workspace/` protection |
| F15 | Single-File Player | `tools/build_standalone.py` -> `web/Seamless_Shorts_Player.html`, `</script` & `</style` escaping, offline `file://` zero-CDN operation |

## 3. Test Architecture & Environment Rules
- **Canonical Runner**:
  `cd /home/antigravity && python3 -m unittest discover -s tests -t . -v`
- **No external test dependencies**: Pure stdlib `unittest` only (NO `pytest`, NO `playwright`).
- **Proxy Bypass**: Because `HTTP_PROXY` / `HTTPS_PROXY` are set in the VM, ALL Python `urllib` requests to `127.0.0.1` / `localhost` MUST use `urllib.request.build_opener(urllib.request.ProxyHandler({}))`, and spawned test subprocesses strip proxy variables.
- **Browser Automation**: Headless `/usr/bin/chromium` driven over Node v24 native `WebSocket` Chrome DevTools Protocol (CDP) via a persistent JSON-RPC bridge in `tests/e2e/__init__.py` (`Input.dispatchMouseEvent` for timeline drag and `Browser.setDownloadBehavior` redirecting downloads into temp `--user-data-dir`, cleaned up on teardown).
- **Data Safety**: Never invoke live YouTube `yt-dlp` downloads; never mutate `/home/antigravity/shorts_workspace/` or `/home/antigravity/upload/`. Pipeline and upload tests copy clips into `tempfile.TemporaryDirectory` (`cwd=tmpdir`) and clean up on teardown.

## 4. Test Suite Layout (223 Total: 37 Unit + 186 E2E)
- `tests/unit/` — M1–M4 + Tier 5 unit/contract tests (`test_web_app.py`, `test_server_contract.py`, `test_launcher_windows.py`, `test_hosting.py`, `test_dist.py` — 37 tests).
- `tests/e2e/__init__.py` — Shared E2E HTTP/Proxy bypass, isolated server launcher, WAV generator, and persistent headless Chromium CDP bridge.
- `tests/e2e/test_e2e_tier1_tier2_web.py` — Tiers 1 & 2 (70 tests: 35 Tier 1 + 35 Tier 2) for browser/standalone features F1–F6 & F15.
- `tests/e2e/test_e2e_tier1_tier2_backend_ops.py` — Tiers 1 & 2 (80 tests: 40 Tier 1 + 40 Tier 2) for server/launcher/tool/dist/hosting/suite features F7–F14.
- `tests/e2e/test_e2e_tier3_pairwise.py` — Tier 3 (16 tests) cross-feature pairwise interactions.
- `tests/e2e/test_e2e_tier4_workflows.py` — Tier 4 (9 tests) real-world end-to-end scenarios.
- `tests/e2e/test_e2e_tier5_hardening.py` — Tier 5 (11 tests) adversarial hardening & regression E2E scenarios.
