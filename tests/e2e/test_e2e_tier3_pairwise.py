#!/usr/bin/env python3
"""Tier 3 E2E tests: Cross-feature pairwise interactions (>= 16 distinct tests)."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests.e2e import (
    AGENT_PATH,
    BAT_PATH,
    CMD_PATH,
    README_PATH,
    ROOT_DIR,
    TOOLS_DIR,
    WEB_DIR,
    WORKSPACE_DIR,
    ZIP_PATH,
    ChromiumCDPBridge,
    clean_subproc_env,
    http_request,
    make_wav_bytes,
    start_agent_server,
    start_static_server,
)


class TestPairwiseInteractionsTier3(unittest.TestCase):
    """16 cross-feature pairwise E2E interaction tests across F1–F15."""

    cdp: ChromiumCDPBridge | None = None
    sid: str = ""
    standalone_uri: str = ""
    wav_4s_b64: str = ""
    wav_2s_b64: str = ""
    clip_b64: str = ""
    manifest_text: str = ""

    @classmethod
    def setUpClass(cls) -> None:
        cls.wav_4s_b64 = base64.b64encode(make_wav_bytes(sample_rate=8000, duration_sec=4.0, freq=440.0)).decode("ascii")
        cls.wav_2s_b64 = base64.b64encode(make_wav_bytes(sample_rate=8000, duration_sec=2.0, freq=587.0)).decode("ascii")
        clip_path = WORKSPACE_DIR / "1_source_shorts" / "05_wIA-VYebvO4.mp4"
        cls.clip_b64 = base64.b64encode(clip_path.read_bytes()).decode("ascii") if clip_path.is_file() else ""
        cls.manifest_text = (WORKSPACE_DIR / "manifest.json").read_text(encoding="utf-8")
        if shutil.which("chromium") or shutil.which("google-chrome"):
            if shutil.which("node"):
                cls.cdp = ChromiumCDPBridge()
                cls.standalone_uri = (WEB_DIR / "Seamless_Shorts_Player.html").resolve().as_uri()
                cls.sid = cls.cdp.new_tab(cls.standalone_uri, wait_ms=250)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.cdp is not None:
            cls.cdp.close()
            cls.cdp = None

    _dirty_nav: bool = False

    def _require_cdp(self, force_reload: bool = False, mark_dirty: bool = False) -> ChromiumCDPBridge:
        if self.cdp is None:
            self.skipTest("chromium or node not available")
        assert self.cdp is not None
        if force_reload or getattr(self.__class__, "_dirty_nav", False):
            self.cdp.navigate(self.sid, self.standalone_uri, wait_ms=100)
            self.__class__._dirty_nav = False
        else:
            self.cdp.eval(
                self.sid,
                """(() => {
                  try { localStorage.removeItem('seamless_shorts_theme'); } catch (_) {}
                  const cw = document.getElementById('clearWorkspaceBtn');
                  if (cw) cw.click();
                  const fade = document.getElementById('standaloneFadeMs');
                  if (fade) fade.value = '15';
                  const st = window.SeamlessApp && window.SeamlessApp.getState ? window.SeamlessApp.getState() : null;
                  if (st) { st.virtualClockMode = false; st.fadeMs = 15; }
                  if (window.SeamlessApp && window.SeamlessApp.setTheme) window.SeamlessApp.setTheme('dark');
                })()""",
            )
        self.cdp.set_viewport(self.sid, 1366, 900, False)
        if mark_dirty:
            self.__class__._dirty_nav = True
        self.cdp.clear_logs(self.sid)
        return self.cdp

    # 1. F1 + F2: Zip drop -> A/B player + -2s audition
    def test_t3_f1_f2_zip_drop_loads_ab_player_and_auditions_cut_minus_2s(self) -> None:
        cdp = self._require_cdp()
        res = cdp.eval(
            self.sid,
            f"""(async () => {{
              const z = new JSZip();
              z.file('manifest.json', {json.dumps(self.manifest_text)});
              z.file('2_repurposed_shorts/Part_01_02_19EAck3_MSg.mp4', new Uint8Array([0,0,0,1]));
              z.file('2_repurposed_shorts/Part_02_03__EF_O_bx_g4.mp4', new Uint8Array([0,0,0,2]));
              const zipBlob = await z.generateAsync({{ type: 'blob' }});
              const dt = new DataTransfer();
              dt.items.add(new File([zipBlob], 'workspace.zip', {{ type: 'application/zip' }}));
              document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
              for (let i = 0; i < 40; i++) {{
                if (window.SeamlessApp.getState().parts.length === 9) break;
                await new Promise(r => setTimeout(r, 40));
              }}
              document.querySelector('#cutMarkersTrack .cut-marker[data-idx="1"]').click();
              const t = window.SeamlessApp.getState().globalTime;
              const badge = document.getElementById('activeCutBadge').textContent;
              if (window.SeamlessApp.getState().playing) document.getElementById('playPauseBtn').click();
              return {{ t, badge, activeA: document.getElementById('videoA').classList.contains('is-active') }};
            }})()""",
        )
        self.assertAlmostEqual(res["t"], 12.049, delta=0.25)
        self.assertIn("Part 1", res["badge"])
        self.assertTrue(res["activeA"])
        self.assertEqual(cdp.get_logs(self.sid)["exceptions"], [])

    # 2. F1 + F3: CSV titles + song replacement re-plan preserves matched titles/stats
    def test_t3_f1_f3_csv_titles_and_song_replacement_replan(self) -> None:
        cdp = self._require_cdp()
        mini_manifest = {
            "fade_ms": 15,
            "parts": [
                {"part_num": 1, "filename": "Part_01_02_19EAck3_MSg.mp4", "source_filename": "02_19EAck3_MSg.mp4", "duration": 1.2, "song_start": 0.0, "song_end": 1.2, "verify_start": 0.0, "verify_end": 1.2},
                {"part_num": 2, "filename": "Part_02_03__EF_O_bx_g4.mp4", "source_filename": "03__EF_O_bx_g4.mp4", "duration": 1.5, "song_start": 1.2, "song_end": 2.7, "verify_start": 1.2, "verify_end": 2.7},
                {"part_num": 3, "filename": "Part_03_04_I8KIQyqBJ2E.mp4", "source_filename": "04_I8KIQyqBJ2E.mp4", "duration": 2.0, "song_start": 2.7, "song_end": 4.7, "verify_start": 2.7, "verify_end": 4.7},
            ],
        }
        csv_text = "\r\n".join(
            [
                "Content,Video title,Views",
                "Total,Summary,999999",
                "19EAck3_MSg,Clip One Title,5901",
                "_EF_O_bx_g4,Spider-Man Underscore Title,5685",
                "I8KIQyqBJ2E,Clip Three Title,4783",
            ]
        )
        res = cdp.eval(
            self.sid,
            f"""(async () => {{
              const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
              await window.SeamlessApp.ingestManifestAndCsv({json.dumps(mini_manifest)}, {json.dumps(csv_text)});
              // Now swap in a 2.0s song -> Part 2 must truncate to 0.8s and Part 3 must drop while Part 2 keeps Spider-Man title
              const f = new File([b64ToBytes({json.dumps(self.wav_2s_b64)})], 'short_2s.wav', {{ type: 'audio/wav' }});
              const dt = new DataTransfer();
              dt.items.add(f);
              const inp = document.getElementById('songInput');
              Object.defineProperty(inp, 'files', {{ value: dt.files, configurable: true }});
              inp.dispatchEvent(new Event('change'));
              for (let i = 0; i < 40; i++) {{
                const st = window.SeamlessApp.getState();
                if (st.songMedia && st.songMedia.name === 'short_2s.wav' && st.parts.length === 2) break;
                await new Promise(r => setTimeout(r, 40));
              }}
              const st = window.SeamlessApp.getState();
              return {{
                partsLen: st.parts.length,
                p2Dur: st.parts[1] ? st.parts[1].duration : 0,
                p2Truncated: st.parts[1] ? st.parts[1].truncated : false,
                p2Title: st.parts[1] ? st.parts[1].youtube_title : '',
                listText: document.getElementById('clipsList').innerText
              }};
            }})()""",
        )
        self.assertEqual(res["partsLen"], 2)
        self.assertAlmostEqual(res["p2Dur"], 0.8, delta=0.1)
        self.assertTrue(res["p2Truncated"])
        self.assertEqual(res["p2Title"], "Spider-Man Underscore Title")
        self.assertIn("Spider-Man Underscore Title", res["listText"])
        self.assertEqual(cdp.get_logs(self.sid)["exceptions"], [])

    # 3. F2 + F6: Timeline block click + cut nav + keyboard shortcuts sync playhead & cards
    def test_t3_f2_f6_timeline_click_and_shortcuts_sync_cut_nav_and_cards(self) -> None:
        cdp = self._require_cdp()
        res = cdp.eval(
            self.sid,
            f"""(async () => {{
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
              document.querySelector('#clipBlocksTrack .clip-block[data-idx="3"]').click();
              const activeAfterBlock = [...document.querySelectorAll('#clipsList .clip-row-card')]
                .findIndex(el => el.classList.contains('is-active'));
              document.dispatchEvent(new KeyboardEvent('keydown', {{ key: ']', bubbles: true }}));
              const activeAfterBracket = [...document.querySelectorAll('#clipsList .clip-row-card')]
                .findIndex(el => el.classList.contains('is-active'));
              const badge = document.getElementById('activeCutBadge').textContent;
              if (window.SeamlessApp.getState().playing) document.getElementById('playPauseBtn').click();
              return {{ activeAfterBlock, activeAfterBracket, badge, playheadLeft: document.getElementById('playheadLine').style.left }};
            }})()""",
        )
        self.assertEqual(res["activeAfterBlock"], 3)
        self.assertIn(res["activeAfterBracket"], (3, 4))
        self.assertTrue(res["playheadLeft"].endswith("%"))
        self.assertNotEqual(res["playheadLeft"], "0%")
        self.assertEqual(cdp.get_logs(self.sid)["exceptions"], [])

    # 4. F3 + F4: Song replacement + fade ms update -> WAV slice + manifest inside JSZip export
    def test_t3_f3_f4_song_replacement_and_fade_change_updates_wav_slices_and_zip(self) -> None:
        cdp = self._require_cdp()
        mini_manifest = {
            "fade_ms": 15,
            "parts": [
                {"part_num": 1, "filename": "Part_01.mp4", "source_filename": "01.mp4", "duration": 1.5, "song_start": 0.0, "song_end": 1.5, "verify_start": 0.0, "verify_end": 1.5},
                {"part_num": 2, "filename": "Part_02.mp4", "source_filename": "02.mp4", "duration": 2.0, "song_start": 1.5, "song_end": 3.5, "verify_start": 1.5, "verify_end": 3.5},
            ],
        }
        res = cdp.eval(
            self.sid,
            f"""(async () => {{
              const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
              await window.SeamlessApp.ingestManifestAndCsv({json.dumps(mini_manifest)}, '');
              const f = new File([b64ToBytes({json.dumps(self.wav_4s_b64)})], 'replaced_4s.wav', {{ type: 'audio/wav' }});
              const dt = new DataTransfer();
              dt.items.add(f);
              const inp = document.getElementById('songInput');
              Object.defineProperty(inp, 'files', {{ value: dt.files, configurable: true }});
              inp.dispatchEvent(new Event('change'));
              for (let i = 0; i < 40; i++) {{
                if (!document.getElementById('dlWavZipBtn').disabled) break;
                await new Promise(r => setTimeout(r, 40));
              }}
              const fadeInp = document.getElementById('standaloneFadeMs');
              fadeInp.value = '25';
              fadeInp.dispatchEvent(new Event('change'));

              let capturedZip = null;
              const origCreate = URL.createObjectURL;
              URL.createObjectURL = (blob) => {{ capturedZip = blob; return origCreate(blob); }};
              document.getElementById('dlWavZipBtn').click();
              for (let i = 0; i < 50; i++) {{
                if (capturedZip && document.getElementById('exportStatus').textContent.startsWith('Exported')) break;
                await new Promise(r => setTimeout(r, 40));
              }}
              URL.createObjectURL = origCreate;
              const ab = await capturedZip.arrayBuffer();
              const bytes = new Uint8Array(ab);
              let bin = '';
              for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
              return {{ zipB64: btoa(bin) }};
            }})()""",
        )
        zip_bytes = base64.b64decode(res["zipB64"])
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            self.assertIsNone(zf.testzip())
            man = json.loads(zf.read("manifest.json").decode("utf-8"))
            self.assertEqual(man["song_name"], "replaced_4s.wav")
            self.assertEqual(man["fade_ms"], 25)
            self.assertIn("audio_slices/Part_01_audio.wav", zf.namelist())
            self.assertIn("audio_slices/Part_02_audio.wav", zf.namelist())

    # 5. F4 + F15: Standalone file:// single-HTML exports valid RIFF WAV inside JSZip back to Python
    def test_t3_f4_f15_standalone_html_exports_valid_zip_offline(self) -> None:
        cdp = self._require_cdp()
        res = cdp.eval(
            self.sid,
            f"""(async () => {{
              const sr = 8000;
              const buf = {{ numberOfChannels: 1, sampleRate: sr, getChannelData: () => new Float32Array(sr * 2).fill(0.4) }};
              const wav = window.SeamlessCore.sliceAudioBufferToWav(buf, 0, 1.0, 15);
              const z = new JSZip();
              z.file('audio_slices/Part_01_audio.wav', wav.bytes);
              const u8 = await z.generateAsync({{ type: 'uint8array' }});
              let bin = '';
              for (let i = 0; i < u8.length; i++) bin += String.fromCharCode(u8[i]);
              return {{ zipB64: btoa(bin) }};
            }})()""",
        )
        raw = base64.b64decode(res["zipB64"])
        with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
            self.assertIsNone(zf.testzip())
            wav_bytes = zf.read("audio_slices/Part_01_audio.wav")
            self.assertEqual(wav_bytes[:4], b"RIFF")
            self.assertEqual(wav_bytes[8:12], b"WAVE")
            self.assertEqual(len(wav_bytes), 44 + 8000 * 2)

    # 6. F5 + F6: Theme toggle redraws waveform canvas and persists across reload with timeline
    def test_t3_f5_f6_theme_toggle_redraws_waveform_and_persists_with_loaded_timeline(self) -> None:
        cdp = self._require_cdp(mark_dirty=True)
        res = cdp.eval(
            self.sid,
            f"""(async () => {{
              const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
              const f = new File([b64ToBytes({json.dumps(self.wav_4s_b64)})], 'song.wav', {{ type: 'audio/wav' }});
              const dt = new DataTransfer();
              dt.items.add(f);
              const inp = document.getElementById('songInput');
              Object.defineProperty(inp, 'files', {{ value: dt.files, configurable: true }});
              inp.dispatchEvent(new Event('change'));
              for (let i = 0; i < 40; i++) {{
                if (window.SeamlessApp.getState().songMedia?.peaks) break;
                await new Promise(r => setTimeout(r, 40));
              }}
              const darkColor = getComputedStyle(document.documentElement).getPropertyValue('--wave-played').trim();
              document.getElementById('themeToggleBtn').click();
              const lightColor = getComputedStyle(document.documentElement).getPropertyValue('--wave-played').trim();
              const canvas = document.getElementById('waveformCanvas');
              const pix = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
              let drawn = 0;
              for (let i = 3; i < pix.length; i += 4) if (pix[i] > 0) drawn++;
              return {{ darkColor, lightColor, drawn, theme: document.documentElement.getAttribute('data-theme') }};
            }})()""",
        )
        self.assertNotEqual(res["darkColor"], res["lightColor"])
        self.assertGreater(res["drawn"], 100)
        self.assertEqual(res["theme"], "light")
        cdp.reload(self.sid, wait_ms=200)
        self.assertEqual(cdp.eval(self.sid, "document.documentElement.getAttribute('data-theme')"), "light")

    # 7. F5 + F2: Codec error notice on 9:16 stage in mobile layout + verify mode switch
    def test_t3_f5_f2_codec_error_on_9_16_stage_with_verify_fallback(self) -> None:
        cdp = self._require_cdp(mark_dirty=True)
        cdp.set_viewport(self.sid, 390, 844, True)
        res = cdp.eval(
            self.sid,
            f"""(async () => {{
              const dt = new DataTransfer();
              dt.items.add(new File([{json.dumps(self.manifest_text)}], 'manifest.json', {{ type: 'application/json' }}));
              dt.items.add(new File([new Uint8Array([0,0,0,1])], '3_verification_full_playthrough.mp4', {{ type: 'video/mp4' }}));
              document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
              for (let i = 0; i < 40; i++) {{
                if (window.SeamlessApp.getState().verifyMedia) break;
                await new Promise(r => setTimeout(r, 40));
              }}
              document.getElementById('modeSequenceBtn').click();
              const v = document.getElementById('videoA');
              v.setAttribute('src', 'blob:av1-unsupported');
              v.dispatchEvent(new Event('error'));
              const noticeShown = !document.getElementById('codecNotice').hidden;
              const overflow = document.documentElement.scrollWidth - window.innerWidth;
              document.getElementById('modeVerifyBtn').click();
              return {{
                noticeShown,
                overflow,
                viewAfterFallback: window.SeamlessApp.getState().playbackView
              }};
            }})()""",
        )
        self.assertTrue(res["noticeShown"])
        self.assertLessEqual(res["overflow"], 1)
        self.assertEqual(res["viewAfterFallback"], "verify")

    # 8. F7 + F10: Studio /api/upload-song -> /api/state -> /media/audio/* Range 206
    def test_t3_f7_f10_studio_upload_song_updates_state_and_range_streams_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _, base_url, stop = start_agent_server(cwd=Path(tmpdir), port_arg=0)
            try:
                wav = make_wav_bytes(duration_sec=1.2)
                st, hdrs, body = http_request(
                    f"{base_url}/api/upload-song",
                    method="POST",
                    headers={"X-Filename": "pair_track.wav"},
                    data=wav,
                )
                self.assertEqual(st, 200)
                self.assertEqual(hdrs.get("access-control-allow-origin"), "*")
                saved = json.loads(body.decode("utf-8"))["song_name"]

                _, _, state_raw = http_request(f"{base_url}/api/state")
                state = json.loads(state_raw.decode("utf-8"))
                self.assertEqual(state["song_name"], saved)

                st_r, hdr_r, body_r = http_request(
                    f"{base_url}/media/audio/{saved}",
                    headers={"Range": "bytes=0-63"},
                )
                self.assertEqual(st_r, 206)
                self.assertEqual(hdr_r.get("content-type"), "audio/wav")
                self.assertEqual(hdr_r.get("content-range"), f"bytes 0-63/{len(wav)}")
                self.assertEqual(body_r, wav[:64])
            finally:
                stop()

    # 9. F7 + F9: --cookies-from-browser forwarding + repeated /api/state polling PATH stability
    def test_t3_f7_f9_cookies_from_browser_cli_api_and_setup_paths_idempotency(self) -> None:
        if str(ROOT_DIR) not in sys.path:
            sys.path.insert(0, str(ROOT_DIR))
        import seamless_shorts_agent as agent

        orig_path = os.environ.get("PATH", "")
        _, base_url, stop = start_agent_server(cwd=ROOT_DIR, port_arg=0)
        try:
            # Repeatedly poll /api/state (which invokes get_ffmpeg_bin / get_ffprobe_bin / has_yt_dlp)
            for _ in range(8):
                st, _, _ = http_request(f"{base_url}/api/state")
                self.assertEqual(st, 200)
            agent.setup_paths()
            p1 = os.environ.get("PATH", "")
            agent.setup_paths()
            self.assertEqual(os.environ.get("PATH", ""), p1)
            self.assertEqual(agent.normalize_cookies_from_browser("chromium"), "chromium")
        finally:
            os.environ["PATH"] = orig_path
            stop()

    # 10. F10 + F11: Flat zip -j extracted dir boots Studio + serves /api/health + /Seamless_Shorts_Player.html
    def test_t3_f10_f11_flat_extracted_zip_serves_studio_and_standalone_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ext = Path(tmpdir)
            with zipfile.ZipFile(ZIP_PATH, "r") as zf:
                zf.extractall(ext)
            _, base_url, stop = start_agent_server(
                script_path=ext / "seamless_shorts_agent.py",
                cwd=ext,
                port_arg=0,
            )
            try:
                st_h, hdr_h, body_h = http_request(f"{base_url}/api/health")
                self.assertEqual(st_h, 200)
                self.assertTrue(json.loads(body_h.decode("utf-8"))["ok"])
                self.assertEqual(hdr_h.get("access-control-allow-origin"), "*")

                for asset in ("index.html", "styles.css", "app.js", "jszip.min.js", "Seamless_Shorts_Player.html"):
                    st_a, _, b_a = http_request(f"{base_url}/{asset}")
                    self.assertEqual(st_a, 200)
                    self.assertEqual(b_a, (ext / asset).read_bytes())
                    st_h2, _, hb = http_request(f"{base_url}/web/{asset}", method="HEAD")
                    self.assertEqual(st_h2, 200)
                    self.assertEqual(hb, b"")
            finally:
                stop()

    # 11. F1 + F13: Static Pages site without /api/health auto-falls back to Standalone & loads .zip
    def test_t3_f1_f13_static_pages_build_without_api_health_enters_standalone(self) -> None:
        if self.cdp is None:
            self.skipTest("chromium or node not available")
        with tempfile.TemporaryDirectory() as tmpdir:
            site = Path(tmpdir) / "site"
            subprocess.run(["bash", str(TOOLS_DIR / "build_pages.sh"), str(site)], cwd=str(ROOT_DIR), check=True, capture_output=True)
            _, url, stop = start_static_server(site)
            try:
                assert self.cdp is not None
                self.__class__._dirty_nav = True
                self.cdp.navigate(self.sid, f"{url}/", wait_ms=350)
                res = self.cdp.eval(
                    self.sid,
                    f"""(async () => {{
                      const mode = document.getElementById('modeBadge').dataset.mode;
                      const z = new JSZip();
                      z.file('manifest.json', {json.dumps(self.manifest_text)});
                      const blob = await z.generateAsync({{ type: 'blob' }});
                      const dt = new DataTransfer();
                      dt.items.add(new File([blob], 'static_test.zip', {{ type: 'application/zip' }}));
                      document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
                      for (let i = 0; i < 40; i++) {{
                        if (window.SeamlessApp.getState().parts.length === 9) break;
                        await new Promise(r => setTimeout(r, 40));
                      }}
                      return {{ mode, parts: window.SeamlessApp.getState().parts.length }};
                    }})()""",
                )
                self.assertEqual(res["mode"], "standalone")
                self.assertEqual(res["parts"], 9)
                self.assertEqual(self.cdp.get_logs(self.sid)["exceptions"], [])
            finally:
                stop()

    # 12. F8 + F11: Zipped launchers & README match disk sources & plain-language filenames
    def test_t3_f8_f11_zipped_launchers_match_source_and_readme_instructions(self) -> None:
        with zipfile.ZipFile(ZIP_PATH, "r") as zf:
            names = set(zf.namelist())
            readme = zf.read("README.txt").decode("utf-8")
            bat = zf.read("START_HERE.bat").decode("utf-8")
            self.assertEqual(zf.read("START_HERE.bat"), BAT_PATH.read_bytes())
            self.assertEqual(zf.read("START_HERE.command"), CMD_PATH.read_bytes())
            self.assertEqual(zf.read("README.txt"), README_PATH.read_bytes())
            for referenced in ("Seamless_Shorts_Player.html", "START_HERE.bat", "START_HERE.command", "seamless_shorts_agent.py"):
                self.assertIn(referenced, names)
                self.assertIn(referenced, readme)
            self.assertIn("Seamless_Shorts_Player.html", bat)

    # 13. F9 + F10: ffprobe-absent handler still builds Studio state & range-streams /media/source/*
    def test_t3_f9_f10_ffprobe_absent_studio_state_and_media_serving(self) -> None:
        import http.server
        import threading
        from unittest import mock

        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            self.skipTest("ffmpeg not installed")
        if str(ROOT_DIR) not in sys.path:
            sys.path.insert(0, str(ROOT_DIR))
        import seamless_shorts_agent as agent

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            src_dir = tmp / "shorts_workspace" / "1_source_shorts"
            out_dir = tmp / "shorts_workspace" / "2_repurposed_shorts"
            src_dir.mkdir(parents=True)
            out_dir.mkdir(parents=True)
            clip_name = "05_wIA-VYebvO4.mp4"
            shutil.copy2(WORKSPACE_DIR / "1_source_shorts" / clip_name, src_dir / clip_name)

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), agent.make_handler())
            port = server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(lambda: (server.shutdown(), server.server_close()))

            with mock.patch.object(agent, "WORK_DIR", tmp / "shorts_workspace"), mock.patch.object(
                agent, "SOURCE_DIR", src_dir
            ), mock.patch.object(agent, "OUTPUT_DIR", out_dir), mock.patch.object(
                agent, "VERIFY_FILE", tmp / "shorts_workspace" / "3_verification_full_playthrough.mp4"
            ), mock.patch.object(
                agent, "MANIFEST_FILE", tmp / "shorts_workspace" / "manifest.json"
            ), mock.patch.object(
                agent, "get_ffprobe_bin", return_value=None
            ), mock.patch.object(
                agent, "get_ffmpeg_bin", return_value=ffmpeg_bin
            ):
                # Also probe duration via ffmpeg -i fallback before serving /api/state
                dur = agent.get_media_duration(src_dir / clip_name)
                self.assertAlmostEqual(dur, 14.07, delta=0.2)

                st, _, body = http_request(f"http://127.0.0.1:{port}/api/state")
                self.assertEqual(st, 200)
                data = json.loads(body.decode("utf-8"))
                self.assertTrue(data["tools"]["ffmpeg"])
                self.assertFalse(data["tools"]["ffprobe"])
                self.assertEqual(data["sources"], [clip_name])

                st_m, hdr_m, b_m = http_request(
                    f"http://127.0.0.1:{port}/media/source/{clip_name}",
                    headers={"Range": "bytes=0-255"},
                )
                self.assertEqual(st_m, 206)
                self.assertEqual(hdr_m.get("content-type"), "video/mp4")
                self.assertEqual(len(b_m), 256)

    # 14. F12 + F10: Container HOST=0.0.0.0 & PORT=0 env binds cleanly with CORS & /api/health
    def test_t3_f12_f10_container_host_port_env_cors_and_health_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _, base_url, stop = start_agent_server(
                cwd=Path(tmpdir),
                port_arg=None,
                host_arg=None,
                extra_env={"HOST": "0.0.0.0", "PORT": "0"},
                strip_host_port=True,
            )
            try:
                st_o, hdr_o, _ = http_request(f"{base_url}/api/health", method="OPTIONS")
                self.assertEqual(st_o, 204)
                self.assertEqual(hdr_o.get("access-control-allow-origin"), "*")
                st_h, hdr_h, body_h = http_request(f"{base_url}/api/health")
                self.assertEqual(st_h, 200)
                self.assertEqual(hdr_h.get("access-control-allow-origin"), "*")
                self.assertEqual(json.loads(body_h.decode("utf-8"))["mode"], "studio")
            finally:
                stop()

    # 15. F2 + F3 + F4: Browser re-plan with real clip + truncated song -> WAV slice duration matches plan
    def test_t3_f2_f3_f4_full_browser_replan_truncate_and_wav_duration_sync(self) -> None:
        if not self.clip_b64:
            self.skipTest("Workspace clip missing")
        cdp = self._require_cdp()
        res = cdp.eval(
            self.sid,
            f"""(async () => {{
              const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
              const clipFile = new File([b64ToBytes({json.dumps(self.clip_b64)})], '05_wIA-VYebvO4.mp4', {{ type: 'video/mp4' }});
              const wavFile = new File([b64ToBytes({json.dumps(self.wav_4s_b64)})], 'song_4s.wav', {{ type: 'audio/wav' }});
              const dt = new DataTransfer();
              dt.items.add(clipFile);
              dt.items.add(wavFile);
              document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
              for (let i = 0; i < 60; i++) {{
                const st = window.SeamlessApp.getState();
                if (st.parts.length === 1 && st.songMedia && st.songMedia.audioBuffer) break;
                await new Promise(r => setTimeout(r, 50));
              }}
              const st = window.SeamlessApp.getState();
              const p = st.parts[0];
              const wav = window.SeamlessCore.sliceAudioBufferToWav(st.songMedia.audioBuffer, p.song_start, p.song_end, st.fadeMs);
              return {{
                truncated: p.truncated,
                planDur: p.duration,
                wavDur: wav.duration,
                wavHeader: String.fromCharCode(...wav.bytes.slice(0, 4))
              }};
            }})()""",
        )
        self.assertTrue(res["truncated"])
        self.assertAlmostEqual(res["planDur"], 4.0, delta=0.1)
        self.assertAlmostEqual(res["wavDur"], res["planDur"], delta=0.05)
        self.assertEqual(res["wavHeader"], "RIFF")

    # 16. F14 + F15: Standalone HTML hash matches across web/, build_pages.sh, and dist zip
    def test_t3_f14_f15_build_standalone_then_pages_keeps_identical_single_file_hash(self) -> None:
        disk_sha = hashlib.sha256((WEB_DIR / "Seamless_Shorts_Player.html").read_bytes()).hexdigest()
        with zipfile.ZipFile(ZIP_PATH, "r") as zf:
            zip_sha = hashlib.sha256(zf.read("Seamless_Shorts_Player.html")).hexdigest()
        self.assertEqual(zip_sha, disk_sha)
        with tempfile.TemporaryDirectory() as tmpdir:
            site = Path(tmpdir) / "site"
            subprocess.run(["bash", str(TOOLS_DIR / "build_pages.sh"), str(site)], cwd=str(ROOT_DIR), check=True, capture_output=True)
            pages_sha = hashlib.sha256((site / "Seamless_Shorts_Player.html").read_bytes()).hexdigest()
            self.assertEqual(pages_sha, disk_sha)


if __name__ == "__main__":
    unittest.main()
