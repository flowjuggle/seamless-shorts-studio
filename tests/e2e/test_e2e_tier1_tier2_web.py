#!/usr/bin/env python3
"""Tier 1 (Feature Coverage) & Tier 2 (Boundary/Corner/Error) E2E tests for Web App F1–F6 & F15."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.e2e import (
    JSZIP_SHA256,
    ROOT_DIR,
    TOOLS_DIR,
    WEB_DIR,
    ChromiumCDPBridge,
    http_request,
    make_wav_bytes,
    run_node_script,
    start_agent_server,
    start_static_server,
)


def _ensure_browser_or_skip(test_case: unittest.TestCase) -> tuple[str, str]:
    chromium_bin = shutil.which("chromium") or shutil.which("google-chrome")
    node_bin = shutil.which("node")
    if not chromium_bin or not node_bin:
        test_case.skipTest("chromium or node not available for headless CDP verification")
    assert chromium_bin and node_bin
    return chromium_bin, node_bin


class TestWebFeaturesTier1AndTier2(unittest.TestCase):
    """70 opaque-box E2E tests (5 Tier 1 + 5 Tier 2 per feature) across F1–F6 and F15."""

    cdp: ChromiumCDPBridge | None = None
    sid: str = ""
    standalone_uri: str = ""
    wav_b64: str = ""
    short_wav_b64: str = ""
    clip_b64: str = ""
    manifest_text: str = ""

    @classmethod
    def setUpClass(cls) -> None:
        if not (shutil.which("chromium") or shutil.which("google-chrome")) or not shutil.which("node"):
            return
        cls.cdp = ChromiumCDPBridge()
        cls.standalone_uri = (WEB_DIR / "Seamless_Shorts_Player.html").resolve().as_uri()
        cls.sid = cls.cdp.new_tab(cls.standalone_uri, wait_ms=250)
        cls.wav_b64 = base64.b64encode(make_wav_bytes(sample_rate=8000, duration_sec=4.0, freq=440.0)).decode("ascii")
        cls.short_wav_b64 = base64.b64encode(make_wav_bytes(sample_rate=8000, duration_sec=1.5, freq=660.0)).decode("ascii")
        clip_path = ROOT_DIR / "shorts_workspace" / "1_source_shorts" / "05_wIA-VYebvO4.mp4"
        cls.clip_b64 = base64.b64encode(clip_path.read_bytes()).decode("ascii") if clip_path.is_file() else ""
        manifest_path = ROOT_DIR / "shorts_workspace" / "manifest.json"
        cls.manifest_text = manifest_path.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.cdp is not None:
            cls.cdp.close()
            cls.cdp = None

    _needs_reload: bool = False
    _viewport_dirty: bool = False

    def setUp(self) -> None:
        self._did_browser = False

    def _prep_browser(self, force_reload: bool = False, mark_dirty: bool = False) -> ChromiumCDPBridge:
        _ensure_browser_or_skip(self)
        assert self.cdp is not None
        if force_reload or getattr(self.__class__, "_needs_reload", False):
            self.cdp.navigate(self.sid, self.standalone_uri, wait_ms=100)
            self.__class__._needs_reload = False
        else:
            self.cdp.eval(
                self.sid,
                """(() => {
                  try { localStorage.removeItem('seamless_shorts_theme'); } catch (_) {}
                  const cw = document.getElementById('clearWorkspaceBtn');
                  if (cw) cw.click();
                  const fade = document.getElementById('standaloneFadeMs');
                  if (fade) fade.value = '15';
                  const spd = document.getElementById('speedSelect');
                  if (spd && spd.value !== '1') { spd.value = '1'; spd.dispatchEvent(new Event('change')); }
                  const vs = document.getElementById('videoVolSlider');
                  if (vs && vs.value !== '0') { vs.value = '0'; vs.dispatchEvent(new Event('input')); }
                  const ss = document.getElementById('songVolSlider');
                  if (ss && ss.value !== '100') { ss.value = '100'; ss.dispatchEvent(new Event('input')); }
                  const lb = document.getElementById('loopClipBtn');
                  if (lb && lb.getAttribute('aria-pressed') === 'true') lb.click();
                  const mb = document.getElementById('muteVideoBtn');
                  if (mb && mb.getAttribute('aria-pressed') === 'false') mb.click();
                  const st = window.SeamlessApp && window.SeamlessApp.getState ? window.SeamlessApp.getState() : null;
                  if (st) { st.virtualClockMode = false; st.fadeMs = 15; }
                  document.getElementById('codecNotice').hidden = true;
                  document.getElementById('shortcutsPopover').hidden = true;
                  if (window.SeamlessApp && window.SeamlessApp.setTheme) window.SeamlessApp.setTheme('dark');
                })()""",
            )
        if getattr(self.__class__, "_viewport_dirty", False):
            self.cdp.set_viewport(self.sid, 1366, 900, False)
            self.__class__._viewport_dirty = False
        if mark_dirty:
            self.__class__._needs_reload = True
        self.cdp.clear_logs(self.sid)
        self._did_browser = True
        return self.cdp

    def _assert_no_browser_errors(self) -> None:
        assert self.cdp is not None
        logs = self.cdp.get_logs(self.sid)
        self.assertEqual(logs["exceptions"], [], f"Uncaught page exceptions: {logs['exceptions']}")
        self.assertEqual(logs["consoleErrors"], [], f"Console errors: {logs['consoleErrors']}")

    def _run_node_core(self, body_js: str, args: tuple[str, ...] = ()) -> str:
        script = f"""
const assert = require('assert');
const core = require({json.dumps(str(WEB_DIR / 'app.js'))});
{body_js}
"""
        res = run_node_script(script, args=args)
        self.assertEqual(res.returncode, 0, f"Node core failure:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
        return res.stdout

    # =========================================================================
    # F1: STANDALONE WEB APP (5 Tier 1 + 5 Tier 2)
    # =========================================================================

    def test_t1_f1_standalone_initial_mode_and_dom_readiness(self) -> None:
        self._prep_browser()
        info = self.cdp.eval(
            self.sid,
            """(() => ({
              mode: document.getElementById('modeBadge').dataset.mode,
              badgeText: document.getElementById('modeBadge').textContent.trim(),
              studioHidden: document.getElementById('studioPanel').hidden,
              jszipType: typeof JSZip,
              dropzoneVisible: !document.getElementById('dropzone').hidden,
              pickers: ['pickFilesBtn','pickFolderBtn','pickZipBtn','pickSongBtn','pickCsvBtn']
                .every(id => Boolean(document.getElementById(id)))
            }))()""",
        )
        self.assertEqual(info["mode"], "standalone")
        self.assertIn("Standalone", info["badgeText"])
        self.assertTrue(info["studioHidden"])
        self.assertEqual(info["jszipType"], "function")
        self.assertTrue(info["dropzoneVisible"])
        self.assertTrue(info["pickers"])
        self._assert_no_browser_errors()

    def test_t1_f1_drop_multiple_source_clips_and_song_via_dropzone(self) -> None:
        self._prep_browser()
        if not self.clip_b64:
            self.skipTest("Workspace clip missing")
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
              const clipFile = new File([b64ToBytes({json.dumps(self.clip_b64)})], '05_wIA-VYebvO4.mp4', {{ type: 'video/mp4' }});
              const wavFile = new File([b64ToBytes({json.dumps(self.wav_b64)})], 'my_beat.wav', {{ type: 'audio/wav' }});
              const dt = new DataTransfer();
              dt.items.add(clipFile);
              dt.items.add(wavFile);
              document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
              for (let i = 0; i < 60; i++) {{
                const st = window.SeamlessApp.getState();
                if (st.parts.length === 1 && st.songMedia && st.songMedia.duration > 0) break;
                await new Promise(r => setTimeout(r, 50));
              }}
              const st = window.SeamlessApp.getState();
              return {{
                partsCount: st.parts.length,
                summary: document.getElementById('loadSummary').textContent,
                songDisplay: document.getElementById('activeSongDisplay').textContent,
                partDur: st.parts[0] ? st.parts[0].duration : 0,
                cards: document.querySelectorAll('#clipsList .clip-row-card').length
              }};
            }})()""",
        )
        self.assertEqual(res["partsCount"], 1)
        self.assertEqual(res["cards"], 1)
        self.assertIn("1 clip", res["summary"])
        self.assertIn("my_beat.wav", res["songDisplay"])
        self.assertGreater(res["partDur"], 3.0)
        self._assert_no_browser_errors()

    def test_t1_f1_drop_zip_archive_with_manifest_clips_and_csv(self) -> None:
        self._prep_browser()
        sample_csv = "\r\n".join(
            [
                "Content,Video title,Video publish time,Impressions click-through rate (%),Views,Average view duration,Average percentage viewed (%)",
                '19EAck3_MSg,"Flowmies Try Props","May 28, 2024",1.46,5901,0:00:14,103.59',
                '_EF_O_bx_g4,"Spider-Man learn 2 #flow Juggle","Jun 2, 2023",5.35,5685,0:00:21,35.41',
            ]
        )
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              const z = new JSZip();
              z.file('manifest.json', {json.dumps(self.manifest_text)});
              z.file('stats.csv', {json.dumps(sample_csv)});
              z.file('2_repurposed_shorts/Part_01_02_19EAck3_MSg.mp4', new Uint8Array([0,0,0,1]));
              const zipBlob = await z.generateAsync({{ type: 'blob' }});
              const zipFile = new File([zipBlob], 'bundle.zip', {{ type: 'application/zip' }});
              const dt = new DataTransfer();
              dt.items.add(zipFile);
              document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
              for (let i = 0; i < 40; i++) {{
                if (document.querySelectorAll('#clipsList .clip-row-card').length === 9) break;
                await new Promise(r => setTimeout(r, 40));
              }}
              const st = window.SeamlessApp.getState();
              return {{
                cards: document.querySelectorAll('#clipsList .clip-row-card').length,
                partMediaSize: st.partMediaByName.size,
                csvStatus: document.getElementById('csvStatusText').textContent,
                clearHidden: document.getElementById('clearWorkspaceBtn').hidden
              }};
            }})()""",
        )
        self.assertEqual(res["cards"], 9)
        self.assertEqual(res["partMediaSize"], 1)
        self.assertIn("2 video stats", res["csvStatus"])
        self.assertFalse(res["clearHidden"])
        self._assert_no_browser_errors()

    def test_t1_f1_folder_webkitrelativepath_and_file_picker_ingestion(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              const manifest = {self.manifest_text};
              const mFile = new File([JSON.stringify(manifest)], 'manifest.json', {{ type: 'application/json' }});
              Object.defineProperty(mFile, 'webkitRelativePath', {{ value: 'shorts_workspace/manifest.json' }});
              const vFile = new File([new Uint8Array([0,0,0,24])], '3_verification_full_playthrough.mp4', {{ type: 'video/mp4' }});
              Object.defineProperty(vFile, 'webkitRelativePath', {{ value: 'shorts_workspace/3_verification_full_playthrough.mp4' }});
              const pFile = new File([new Uint8Array([0,0,0,24])], 'Part_01_02_19EAck3_MSg.mp4', {{ type: 'video/mp4' }});
              Object.defineProperty(pFile, 'webkitRelativePath', {{ value: 'shorts_workspace/2_repurposed_shorts/Part_01_02_19EAck3_MSg.mp4' }});
              const dt = new DataTransfer();
              dt.items.add(mFile);
              dt.items.add(vFile);
              dt.items.add(pFile);
              const inp = document.getElementById('folderInput');
              Object.defineProperty(inp, 'files', {{ value: dt.files, configurable: true }});
              inp.dispatchEvent(new Event('change'));
              for (let i = 0; i < 40; i++) {{
                if (window.SeamlessApp.getState().parts.length === 9) break;
                await new Promise(r => setTimeout(r, 40));
              }}
              const st = window.SeamlessApp.getState();
              return {{
                partsLen: st.parts.length,
                hasVerify: Boolean(st.verifyMedia),
                verifyBtnDisabled: document.getElementById('modeVerifyBtn').disabled,
                partMapped: st.partMediaByName.has('Part_01_02_19EAck3_MSg.mp4')
              }};
            }})()""",
        )
        self.assertEqual(res["partsLen"], 9)
        self.assertTrue(res["hasVerify"])
        self.assertFalse(res["verifyBtnDisabled"])
        self.assertTrue(res["partMapped"])
        self._assert_no_browser_errors()

    def test_t1_f1_csv_rfc4180_matching_real_clips_and_clear_workspace(self) -> None:
        self._prep_browser()
        csv_text = "\r\n".join(
            [
                "Content,Video title,Video publish time,Impressions click-through rate (%),Views,Average view duration,Average percentage viewed (%)",
                '19EAck3_MSg,"Flowmies, ""Live"" 🤹‍♂️","May 28, 2024",1.46,5901,0:00:14,103.59',
                '_EF_O_bx_g4,"Spider-Man #flow","Jun 2, 2023",5.35,5685,0:00:21,35.41',
            ]
        )
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, {json.dumps(csv_text)});
              const beforeText = document.getElementById('clipsList').innerText;
              const beforeCount = window.SeamlessApp.getState().parts.length;
              document.getElementById('clearWorkspaceBtn').click();
              const afterState = window.SeamlessApp.getState();
              return {{
                beforeCount,
                hasQuotedTitle: beforeText.includes('Flowmies, "Live" 🤹‍♂️'),
                hasUnderscoreIdTitle: beforeText.includes('Spider-Man #flow'),
                afterCount: afterState.parts.length,
                afterCsvCount: afterState.csvCount,
                afterSummary: document.getElementById('loadSummary').textContent
              }};
            }})()""",
        )
        self.assertEqual(res["beforeCount"], 9)
        self.assertTrue(res["hasQuotedTitle"])
        self.assertTrue(res["hasUnderscoreIdTitle"])
        self.assertEqual(res["afterCount"], 0)
        self.assertEqual(res["afterCsvCount"], 0)
        self.assertIn("0 clips", res["afterSummary"])
        self._assert_no_browser_errors()

    def test_t2_f1_csv_skips_total_row_and_leading_underscore_youtube_id(self) -> None:
        out = self._run_node_core(
            """
const csv = [
  'Content,Video title,Views',
  'Total,All Videos,632683',
  '_EF_O_bx_g4,Leading Underscore Video,5685',
  'https://youtube.com/shorts/19EAck3_MSg?feature=share,Shorts URL Video,5901'
].join('\\r\\n');
const parsed = core.parseCsvRfc4180(csv);
assert.strictEqual(parsed.rows.length, 2);
assert.strictEqual(parsed.byId['Total'], undefined);
assert.strictEqual(parsed.byId['total'], undefined);
assert.strictEqual(core.extractYouTubeId('03__EF_O_bx_g4.mp4'), '_EF_O_bx_g4');
assert.strictEqual(core.extractYouTubeId('Part_02_03__EF_O_bx_g4.mp4'), '_EF_O_bx_g4');
assert.strictEqual(core.matchClipMetadata('03__EF_O_bx_g4.mp4', parsed.byId).views, 5685);
assert.strictEqual(core.matchClipMetadata('02_19EAck3_MSg.mp4', parsed.byId).title, 'Shorts URL Video');
console.log('OK');
"""
        )
        self.assertIn("OK", out)

    def test_t2_f1_corrupted_zip_drop_does_not_crash_app(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              const bad = new File([new Uint8Array([0x00, 0xff, 0x13, 0x37])], 'corrupt.zip', {{ type: 'application/zip' }});
              const dt = new DataTransfer();
              dt.items.add(bad);
              document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
              await new Promise(r => setTimeout(r, 250));
              // App must still remain usable for a subsequent valid load
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
              return {{
                partsAfterRecovery: window.SeamlessApp.getState().parts.length,
                exportStatus: document.getElementById('exportStatus').textContent
              }};
            }})()""",
        )
        self.assertEqual(res["partsAfterRecovery"], 9)
        self._assert_no_browser_errors()

    def test_t2_f1_macosx_resource_fork_zip_entries_ignored(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              const z = new JSZip();
              z.file('__MACOSX/._manifest.json', 'CORRUPT');
              z.file('__MACOSX/1_source_shorts/._02_19EAck3_MSg.mp4', 'CORRUPT');
              z.file('._hidden.mp4', 'CORRUPT');
              z.file('manifest.json', {json.dumps(self.manifest_text)});
              const blob = await z.generateAsync({{ type: 'blob' }});
              const dt = new DataTransfer();
              dt.items.add(new File([blob], 'mac_zip.zip', {{ type: 'application/zip' }}));
              document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
              for (let i = 0; i < 40; i++) {{
                if (window.SeamlessApp.getState().parts.length === 9) break;
                await new Promise(r => setTimeout(r, 40));
              }}
              const st = window.SeamlessApp.getState();
              return {{ partsLen: st.parts.length, srcCount: st.sourceClips.length }};
            }})()""",
        )
        self.assertEqual(res["partsLen"], 9)
        self.assertEqual(res["srcCount"], 0)
        self._assert_no_browser_errors()

    def test_t2_f1_malformed_json_and_empty_csv_graceful_handling(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            """(async () => {
              const dt = new DataTransfer();
              dt.items.add(new File(['{broken json!!!'], 'manifest.json', { type: 'application/json' }));
              dt.items.add(new File(['Content,Video title,Views\\r\\n'], 'empty.csv', { type: 'text/csv' }));
              document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt }));
              await new Promise(r => setTimeout(r, 200));
              const st = window.SeamlessApp.getState();
              return { partsLen: st.parts.length, csvCount: st.csvCount };
            })()""",
        )
        self.assertEqual(res["partsLen"], 0)
        self.assertEqual(res["csvCount"], 0)
        self._assert_no_browser_errors()

    def test_t2_f1_unsupported_file_extension_ignored_gracefully(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            """(async () => {
              const dt = new DataTransfer();
              dt.items.add(new File(['hello'], 'notes.txt', { type: 'text/plain' }));
              dt.items.add(new File([new Uint8Array([0x4d, 0x5a])], 'installer.exe', { type: 'application/octet-stream' }));
              dt.items.add(new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'thumb.png', { type: 'image/png' }));
              document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt }));
              await new Promise(r => setTimeout(r, 150));
              const st = window.SeamlessApp.getState();
              return { partsLen: st.parts.length, songMedia: st.songMedia, verifyMedia: st.verifyMedia };
            })()""",
        )
        self.assertEqual(res["partsLen"], 0)
        self.assertIsNone(res["songMedia"])
        self.assertIsNone(res["verifyMedia"])
        self._assert_no_browser_errors()

    # =========================================================================
    # F2: SYNCED PREVIEW + CUT NAVIGATION (5 Tier 1 + 5 Tier 2)
    # =========================================================================

    def test_t1_f2_dual_video_ab_and_verify_mode_switching(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              const mFile = new File([{json.dumps(self.manifest_text)}], 'manifest.json', {{ type: 'application/json' }});
              const vFile = new File([new Uint8Array([0,0,0,1])], '3_verification_full_playthrough.mp4', {{ type: 'video/mp4' }});
              const dt = new DataTransfer();
              dt.items.add(mFile);
              dt.items.add(vFile);
              document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
              for (let i = 0; i < 40; i++) {{
                if (window.SeamlessApp.getState().parts.length === 9 && window.SeamlessApp.getState().verifyMedia) break;
                await new Promise(r => setTimeout(r, 40));
              }}
              const initialView = window.SeamlessApp.getState().playbackView;
              document.getElementById('modeSequenceBtn').click();
              const seqView = window.SeamlessApp.getState().playbackView;
              const seqActive = document.getElementById('modeSequenceBtn').classList.contains('is-active');
              document.getElementById('modeVerifyBtn').click();
              const verView = window.SeamlessApp.getState().playbackView;
              const verActive = document.getElementById('modeVerifyBtn').classList.contains('is-active');
              return {{ initialView, seqView, seqActive, verView, verActive }};
            }})()""",
        )
        self.assertEqual(res["initialView"], "verify")
        self.assertEqual(res["seqView"], "sequence")
        self.assertTrue(res["seqActive"])
        self.assertEqual(res["verView"], "verify")
        self.assertTrue(res["verActive"])
        self._assert_no_browser_errors()

    def test_t1_f2_audition_cut_minus_2s_exact_seek(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
              document.getElementById('auditionCutBtn').click();
              const t1 = window.SeamlessApp.getState().globalTime;
              document.querySelector('#cutMarkersTrack .cut-marker[data-idx="2"]').click();
              const t2 = window.SeamlessApp.getState().globalTime;
              if (window.SeamlessApp.getState().playing) document.getElementById('playPauseBtn').click();
              return {{ t1, t2 }};
            }})()""",
        )
        # Cut #1 starts at 14.049 -> -2s = 12.049; Cut #2 starts at 74.096 -> -2s = 72.096
        self.assertAlmostEqual(res["t1"], 12.049, delta=0.25)
        self.assertAlmostEqual(res["t2"], 72.096, delta=0.25)
        self._assert_no_browser_errors()

    def test_t1_f2_prev_and_next_cut_navigation_buttons(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
              document.getElementById('nextCutBtn').click(); // -> cut 1 (12.049)
              const tCut1 = window.SeamlessApp.getState().globalTime;
              document.getElementById('nextCutBtn').click(); // -> cut 2 (72.096)
              const tCut2 = window.SeamlessApp.getState().globalTime;
              const badgeAt2 = document.getElementById('activeCutBadge').textContent;
              document.getElementById('prevCutBtn').click(); // -> back to cut 1 (12.049)
              const tBack1 = window.SeamlessApp.getState().globalTime;
              if (window.SeamlessApp.getState().playing) document.getElementById('playPauseBtn').click();
              return {{ tCut1, tCut2, badgeAt2, tBack1 }};
            }})()""",
        )
        self.assertAlmostEqual(res["tCut1"], 12.049, delta=0.25)
        self.assertAlmostEqual(res["tCut2"], 72.096, delta=0.25)
        self.assertAlmostEqual(res["tBack1"], 12.049, delta=0.25)
        self.assertIn("Part 2 / 9", res["badgeAt2"])
        self._assert_no_browser_errors()

    def test_t1_f2_loop_clip_toggle_and_playback_speed_selector(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
              const btn = document.getElementById('loopClipBtn');
              const beforePressed = btn.getAttribute('aria-pressed');
              btn.click();
              const afterPressed = btn.getAttribute('aria-pressed');
              const afterText = btn.textContent.trim();
              const speedSel = document.getElementById('speedSelect');
              speedSel.value = '1.5';
              speedSel.dispatchEvent(new Event('change'));
              return {{
                beforePressed, afterPressed, afterText,
                rate: window.SeamlessApp.getState().playbackRate,
                videoARate: document.getElementById('videoA').playbackRate,
                songRate: document.getElementById('songAudio').playbackRate
              }};
            }})()""",
        )
        self.assertEqual(res["beforePressed"], "false")
        self.assertEqual(res["afterPressed"], "true")
        self.assertIn("Current Clip", res["afterText"])
        self.assertEqual(res["rate"], 1.5)
        self.assertEqual(res["videoARate"], 1.5)
        self.assertEqual(res["songRate"], 1.5)
        self._assert_no_browser_errors()

    def test_t1_f2_sequential_plan_cumulative_offsets_and_9_16_stage(self) -> None:
        out = self._run_node_core(
            """
const plan = core.computeSequentialPlan([
  { filename: '10_last.mp4', rawDuration: 5.5, codec: 'vp9', width: 1080, height: 2400 },
  { filename: '02_first.mp4', rawDuration: 10.25, codec: 'av1', width: 1080, height: 1920 }
], 60.0, 15);
assert.strictEqual(plan.parts.length, 2);
assert.strictEqual(plan.parts[0].part_num, 1);
assert.strictEqual(plan.parts[0].source_filename, '02_first.mp4');
assert.strictEqual(plan.parts[0].song_start, 0);
assert.strictEqual(plan.parts[0].song_end, 10.25);
assert.strictEqual(plan.parts[1].part_num, 2);
assert.strictEqual(plan.parts[1].song_start, 10.25);
assert.strictEqual(plan.parts[1].song_end, 15.75);
assert.strictEqual(plan.total_duration, 15.75);
console.log('PLAN_OK');
"""
        )
        self.assertIn("PLAN_OK", out)
        self._prep_browser()
        self.__class__._viewport_dirty = True
        self.cdp.set_viewport(self.sid, 1440, 1100, False)
        stage_css = self.cdp.eval(
            self.sid,
            """(() => {
              const stageEl = document.getElementById('playerStage');
              const stage = stageEl.getBoundingClientRect();
              const stStyle = getComputedStyle(stageEl);
              const vStyle = getComputedStyle(document.getElementById('videoA'));
              return { ratio: stage.width / stage.height, aspectRatio: stStyle.aspectRatio, objectFit: vStyle.objectFit };
            })()""",
        )
        self.assertAlmostEqual(stage_css["ratio"], 9.0 / 16.0, delta=0.02)
        self.assertEqual(stage_css["aspectRatio"], "9 / 16")
        self.assertEqual(stage_css["objectFit"], "contain")

    def test_t2_f2_last_clip_truncated_when_song_ends_early(self) -> None:
        out = self._run_node_core(
            """
const plan = core.computeSequentialPlan([
  { filename: '01.mp4', rawDuration: 10.0 },
  { filename: '02.mp4', rawDuration: 12.0 },
  { filename: '03.mp4', rawDuration: 20.0 },
  { filename: '04.mp4', rawDuration: 15.0 }
], 25.0, 15);
assert.strictEqual(plan.parts.length, 3, 'Clip 4 after song end must be omitted');
assert.strictEqual(plan.parts[2].duration, 3);
assert.strictEqual(plan.parts[2].truncated, true);
assert.strictEqual(plan.parts[2].song_end, 25);
console.log('TRUNC_OK');
"""
        )
        self.assertIn("TRUNC_OK", out)

    def test_t2_f2_audition_at_t0_and_short_clip_clamps_to_zero(self) -> None:
        self._prep_browser()
        short_manifest = {
            "fade_ms": 15,
            "parts": [
                {"part_num": 1, "filename": "Part_01.mp4", "duration": 0.8, "song_start": 0.0, "song_end": 0.8, "verify_start": 0.0, "verify_end": 0.8},
                {"part_num": 2, "filename": "Part_02.mp4", "duration": 4.0, "song_start": 0.8, "song_end": 4.8, "verify_start": 0.8, "verify_end": 4.8},
            ],
        }
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              await window.SeamlessApp.ingestManifestAndCsv({json.dumps(short_manifest)}, '');
              document.querySelector('#cutMarkersTrack .cut-marker[data-idx="1"]').click();
              const t1 = window.SeamlessApp.getState().globalTime;
              document.getElementById('prevCutBtn').click();
              const t0 = window.SeamlessApp.getState().globalTime;
              if (window.SeamlessApp.getState().playing) document.getElementById('playPauseBtn').click();
              return {{ t1, t0 }};
            }})()""",
        )
        self.assertEqual(res["t1"], 0)
        self.assertEqual(res["t0"], 0)
        self._assert_no_browser_errors()

    def test_t2_f2_unplayable_video_codec_error_shows_banner_and_virtual_clock(self) -> None:
        self._prep_browser(mark_dirty=True)
        assert self.cdp is not None
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
              const v = document.getElementById('videoA');
              v.setAttribute('src', 'blob:nonexistent-codec');
              v.dispatchEvent(new Event('error'));
              const shown = !document.getElementById('codecNotice').hidden;
              const vclock = window.SeamlessApp.getState().virtualClockMode;
              document.getElementById('playPauseBtn').click();
              await new Promise(r => setTimeout(r, 150));
              const advanced = window.SeamlessApp.getState().globalTime;
              document.getElementById('playPauseBtn').click();
              document.getElementById('dismissCodecNoticeBtn').click();
              return {{ shown, vclock, advanced, dismissed: document.getElementById('codecNotice').hidden }};
            }})()""",
        )
        self.assertTrue(res["shown"])
        self.assertTrue(res["vclock"])
        self.assertGreater(res["advanced"], 0.05)
        self.assertTrue(res["dismissed"])
        self._assert_no_browser_errors()

    def test_t2_f2_zero_duration_or_empty_clip_list_safety(self) -> None:
        out = self._run_node_core(
            """
assert.strictEqual(core.computeSequentialPlan([], 60, 15).parts.length, 0);
const skipped = core.computeSequentialPlan([
  { filename: 'zero.mp4', rawDuration: 0 },
  { filename: 'neg.mp4', rawDuration: -5 },
  { filename: 'tiny.mp4', rawDuration: 0.05 }
], 60, 15);
assert.strictEqual(skipped.parts.length, 0);
console.log('EMPTY_CLIPS_OK');
"""
        )
        self.assertIn("EMPTY_CLIPS_OK", out)

    def test_t2_f2_seek_beyond_total_duration_and_negative_clamped(self) -> None:
        self._prep_browser()
        box = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
              const lane = document.getElementById('waveformLane');
              lane.scrollIntoView({{ block: 'center' }});
              const r = document.getElementById('playheadOverlay').getBoundingClientRect();
              const lr = lane.getBoundingClientRect();
              document.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'j', bubbles: true }}));
              const afterNegativeSeek = window.SeamlessApp.getState().globalTime;
              return {{
                total: window.SeamlessApp.getState().totalDuration,
                afterNegativeSeek,
                xRight: r.right - 1,
                y: lr.top + lr.height * 0.5,
                xPastRight: r.right + 120
              }};
            }})()""",
        )
        self.assertEqual(box["afterNegativeSeek"], 0)
        self.cdp.dispatch_mouse(self.sid, "mousePressed", box["xRight"], box["y"])
        self.cdp.dispatch_mouse(self.sid, "mouseMoved", box["xPastRight"], box["y"], buttons=1)
        self.cdp.dispatch_mouse(self.sid, "mouseReleased", box["xPastRight"], box["y"])
        after_max = self.cdp.eval(self.sid, "window.SeamlessApp.getState().globalTime")
        self.assertAlmostEqual(after_max, box["total"], delta=0.05)
        self._assert_no_browser_errors()

    # =========================================================================
    # F3: AUDIO REPLACEMENT + MIXER (5 Tier 1 + 5 Tier 2)
    # =========================================================================

    def test_t1_f3_load_and_replace_song_updates_active_display_and_replans(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
              const uploadSong = async (b64, name) => {{
                const f = new File([b64ToBytes(b64)], name, {{ type: 'audio/wav' }});
                const dt = new DataTransfer();
                dt.items.add(f);
                const inp = document.getElementById('songInput');
                Object.defineProperty(inp, 'files', {{ value: dt.files, configurable: true }});
                inp.dispatchEvent(new Event('change'));
                for (let i = 0; i < 40; i++) {{
                  const st = window.SeamlessApp.getState();
                  if (st.songMedia && st.songMedia.name === name && st.songMedia.duration > 0) break;
                  await new Promise(r => setTimeout(r, 40));
                }}
              }};
              await uploadSong({json.dumps(self.wav_b64)}, 'first_4s.wav');
              const firstTotal = window.SeamlessApp.getState().totalDuration;
              const firstDisp = document.getElementById('activeSongDisplay').textContent;
              await uploadSong({json.dumps(self.short_wav_b64)}, 'second_1_5s.wav');
              const st = window.SeamlessApp.getState();
              return {{
                firstTotal,
                firstDisp,
                secondTotal: st.totalDuration,
                secondDisp: document.getElementById('activeSongDisplay').textContent,
                override: st.hasBrowserSongOverride,
                partsLen: st.parts.length
              }};
            }})()""",
        )
        self.assertAlmostEqual(res["firstTotal"], 4.0, delta=0.1)
        self.assertIn("first_4s.wav", res["firstDisp"])
        self.assertAlmostEqual(res["secondTotal"], 1.5, delta=0.1)
        self.assertIn("second_1_5s.wav", res["secondDisp"])
        self.assertTrue(res["override"])
        self.assertEqual(res["partsLen"], 1)
        self._assert_no_browser_errors()

    def test_t1_f3_video_and_song_volume_sliders_update_gains_and_labels(self) -> None:
        self._prep_browser(force_reload=True)
        assert self.cdp is not None
        res = self.cdp.eval(
            self.sid,
            """(() => {
              const vs = document.getElementById('videoVolSlider');
              const ss = document.getElementById('songVolSlider');
              vs.value = '65';
              vs.dispatchEvent(new Event('input'));
              ss.value = '40';
              ss.dispatchEvent(new Event('input'));
              return {
                vVal: document.getElementById('videoVolValue').textContent,
                sVal: document.getElementById('songVolValue').textContent,
                vAudioVol: document.getElementById('videoA').volume,
                sAudioVol: document.getElementById('songAudio').volume,
                muted: window.SeamlessApp.getState().muteOriginal
              };
            })()""",
        )
        self.assertEqual(res["vVal"], "65%")
        self.assertEqual(res["sVal"], "40%")
        self.assertAlmostEqual(res["vAudioVol"], 0.65, delta=0.02)
        self.assertAlmostEqual(res["sAudioVol"], 0.40, delta=0.02)
        self.assertFalse(res["muted"])
        self._assert_no_browser_errors()

    def test_t1_f3_mute_original_audio_toggle_button_and_m_key(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            """(() => {
              const btn = document.getElementById('muteVideoBtn');
              const initPressed = btn.getAttribute('aria-pressed');
              const initMuted = document.getElementById('videoA').muted;
              btn.click();
              const afterClickPressed = btn.getAttribute('aria-pressed');
              const afterClickLabel = btn.textContent.trim();
              document.dispatchEvent(new KeyboardEvent('keydown', { key: 'm', bubbles: true }));
              const afterKeyPressed = btn.getAttribute('aria-pressed');
              return { initPressed, initMuted, afterClickPressed, afterClickLabel, afterKeyPressed };
            })()""",
        )
        self.assertEqual(res["initPressed"], "true")
        self.assertTrue(res["initMuted"])
        self.assertEqual(res["afterClickPressed"], "false")
        self.assertEqual(res["afterClickLabel"], "Orig Audio: On")
        self.assertEqual(res["afterKeyPressed"], "true")
        self._assert_no_browser_errors()

    def test_t1_f3_fade_ms_input_change_recomputes_plan(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
              const fadeInp = document.getElementById('standaloneFadeMs');
              fadeInp.value = '25';
              fadeInp.dispatchEvent(new Event('change'));
              return {{ fadeMs: window.SeamlessApp.getState().fadeMs }};
            }})()""",
        )
        self.assertEqual(res["fadeMs"], 25)
        self._assert_no_browser_errors()

    def test_t1_f3_replacement_song_overrides_verify_audio_mode(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
              const dt = new DataTransfer();
              dt.items.add(new File([{json.dumps(self.manifest_text)}], 'manifest.json', {{ type: 'application/json' }}));
              dt.items.add(new File([new Uint8Array([0,0,0,1])], '3_verification_full_playthrough.mp4', {{ type: 'video/mp4' }}));
              dt.items.add(new File([new Uint8Array([0,0,0,2])], 'Part_01_02_19EAck3_MSg.mp4', {{ type: 'video/mp4' }}));
              dt.items.add(new File([b64ToBytes({json.dumps(self.wav_b64)})], 'custom_song.wav', {{ type: 'audio/wav' }}));
              document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
              for (let i = 0; i < 40; i++) {{
                const st = window.SeamlessApp.getState();
                if (st.songMedia && st.verifyMedia && st.partMediaByName.size > 0 && st.parts.length > 0) break;
                await new Promise(r => setTimeout(r, 40));
              }}
              const st = window.SeamlessApp.getState();
              return {{
                override: st.hasBrowserSongOverride,
                playbackView: st.playbackView,
                songSrcSet: Boolean(document.getElementById('songAudio').src)
              }};
            }})()""",
        )
        self.assertTrue(res["override"])
        self.assertEqual(res["playbackView"], "sequence")
        self.assertTrue(res["songSrcSet"])
        self._assert_no_browser_errors()

    def test_t2_f3_zero_fade_ms_preserved_not_reset_to_15(self) -> None:
        out = self._run_node_core(
            """
const plan = core.computeSequentialPlan([{ filename: '01.mp4', rawDuration: 5.0 }], 10.0, 0);
assert.strictEqual(plan.fade_ms, 0, 'fade_ms=0 must NOT coerce to 15');
const sr = 10000;
const ch = new Float32Array(sr).fill(0.5);
const sliced = core.sliceAudioChannelsWithFade([ch], sr, 0.0, 0.5, 0);
assert.strictEqual(sliced[0][0], 0.5);
assert.strictEqual(sliced[0][sliced[0].length - 1], 0.5);
console.log('ZERO_FADE_OK');
"""
        )
        self.assertIn("ZERO_FADE_OK", out)
        self._prep_browser()
        fade_val = self.cdp.eval(
            self.sid,
            """(() => {
              const inp = document.getElementById('standaloneFadeMs');
              inp.value = '0';
              inp.dispatchEvent(new Event('change'));
              return window.SeamlessApp.getState().fadeMs;
            })()""",
        )
        self.assertEqual(fade_val, 0)

    def test_t2_f3_unmuting_via_slider_auto_clears_mute_flag(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            """(() => {
              const vs = document.getElementById('videoVolSlider');
              const muteBtn = document.getElementById('muteVideoBtn');
              const beforeMute = window.SeamlessApp.getState().muteOriginal;
              vs.value = '25';
              vs.dispatchEvent(new Event('input'));
              return {
                beforeMute,
                afterMute: window.SeamlessApp.getState().muteOriginal,
                pressed: muteBtn.getAttribute('aria-pressed'),
                label: muteBtn.textContent.trim()
              };
            })()""",
        )
        self.assertTrue(res["beforeMute"])
        self.assertFalse(res["afterMute"])
        self.assertEqual(res["pressed"], "false")
        self.assertEqual(res["label"], "Orig Audio: On")
        self._assert_no_browser_errors()

    def test_t2_f3_corrupt_or_undecodable_audio_blob_fallback(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            """(async () => {
              const dt = new DataTransfer();
              dt.items.add(new File([new Uint8Array([1, 2, 3, 4, 5])], 'broken.mp3', { type: 'audio/mpeg' }));
              document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt }));
              await new Promise(r => setTimeout(r, 250));
              const st = window.SeamlessApp.getState();
              return {
                songName: st.songMedia ? st.songMedia.name : '',
                peaks: st.songMedia ? st.songMedia.peaks : 'missing',
                wavBtnDisabled: document.getElementById('dlWavZipBtn').disabled
              };
            })()""",
        )
        self.assertEqual(res["songName"], "broken.mp3")
        self.assertIsNone(res["peaks"])
        self.assertTrue(res["wavBtnDisabled"])
        self._assert_no_browser_errors()

    def test_t2_f3_song_shorter_than_100ms_or_zero_duration_handled(self) -> None:
        out = self._run_node_core(
            """
const clips = [{ filename: '01.mp4', rawDuration: 10.0 }];
assert.strictEqual(core.computeSequentialPlan(clips, 0.05, 15).parts.length, 0);
assert.strictEqual(core.computeSequentialPlan(clips, 0.0, 15).parts.length, 1); // 0 treats as non-finite fallback or check finite
// Wait: finite positive 0.01 must stop cleanly
assert.strictEqual(core.computeSequentialPlan(clips, 0.01, 15).parts.length, 0);
console.log('TINY_SONG_OK');
"""
        )
        self.assertIn("TINY_SONG_OK", out)

    def test_t2_f3_negative_fade_ms_clamped_to_zero(self) -> None:
        out = self._run_node_core(
            """
const plan = core.computeSequentialPlan([{ filename: '01.mp4', rawDuration: 5.0 }], 10.0, -50);
assert.strictEqual(plan.fade_ms, 0);
const sr = 8000;
const ch = new Float32Array(sr).fill(0.4);
const sliced = core.sliceAudioChannelsWithFade([ch], sr, 0.0, 0.5, -25);
assert.ok(Math.abs(sliced[0][0] - 0.4) < 1e-6);
console.log('NEG_FADE_OK');
"""
        )
        self.assertIn("NEG_FADE_OK", out)
        self._prep_browser()
        val = self.cdp.eval(
            self.sid,
            """(() => {
              const inp = document.getElementById('standaloneFadeMs');
              inp.value = '-10';
              inp.dispatchEvent(new Event('change'));
              return window.SeamlessApp.getState().fadeMs;
            })()""",
        )
        self.assertEqual(val, 0)

    # =========================================================================
    # F4: DOWNLOAD CENTER + WAV/ZIP EXPORT (5 Tier 1 + 5 Tier 2)
    # =========================================================================

    def test_t1_f4_export_manifest_json_schema_and_values(self) -> None:
        out = self._run_node_core(
            """
const payload = core.buildManifestPayload({
  playlistUrl: 'https://www.youtube.com/playlist?list=PL123',
  songName: 'demo.mp3',
  isDemoSong: false,
  fadeMs: 15,
  parts: [{
    part_num: 1, filename: 'Part_01_a.mp4', source_filename: '01_a.mp4',
    duration: 10.5, song_start: 0, song_end: 10.5, verify_start: 0, verify_end: 10.5,
    codec: 'h264', width: 1080, height: 1920, size_bytes: 12345,
    youtube_id: 'a', youtube_title: 'Title A'
  }]
});
assert.strictEqual(payload.fade_ms, 15);
assert.strictEqual(payload.song_name, 'demo.mp3');
assert.strictEqual(payload.parts[0].youtube_id, 'a');
assert.strictEqual(payload.parts[0].youtube_title, 'Title A');
console.log('MANIFEST_SCHEMA_OK');
"""
        )
        self.assertIn("MANIFEST_SCHEMA_OK", out)

    def test_t1_f4_wav_slice_15ms_fade_riff_pcm16_format(self) -> None:
        out = self._run_node_core(
            """
const sr = 10000;
const ch = new Float32Array(sr * 2).fill(0.5);
const buf = { numberOfChannels: 1, sampleRate: sr, getChannelData: () => ch };
const res = core.sliceAudioBufferToWav(buf, 0.25, 1.25, 15);
const dv = new DataView(res.bytes.buffer);
assert.strictEqual(Buffer.from(res.bytes.slice(0, 4)).toString('ascii'), 'RIFF');
assert.strictEqual(Buffer.from(res.bytes.slice(8, 12)).toString('ascii'), 'WAVE');
assert.strictEqual(dv.getUint16(20, true), 1); // PCM
assert.strictEqual(dv.getUint16(22, true), 1); // mono
assert.strictEqual(dv.getUint32(24, true), sr);
assert.strictEqual(dv.getUint16(34, true), 16);
assert.strictEqual(res.numSamples, 10000);
assert.strictEqual(dv.getInt16(44, true), 0); // fade-in starts at 0
assert.ok(dv.getInt16(44 + 5000 * 2, true) > 16000); // mid amplitude preserved
assert.strictEqual(dv.getInt16(44 + 9999 * 2, true), 0); // fade-out ends at 0
console.log('WAV_DSP_OK');
"""
        )
        self.assertIn("WAV_DSP_OK", out)

    def test_t1_f4_wav_slices_zip_bundle_with_jszip_in_browser(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
              window.__capturedDownloads = [];
              const origCreate = URL.createObjectURL;
              URL.createObjectURL = (blob) => {{
                window.__lastBlob = blob;
                return origCreate(blob);
              }};
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
              const f = new File([b64ToBytes({json.dumps(self.wav_b64)})], 'song.wav', {{ type: 'audio/wav' }});
              const dt = new DataTransfer();
              dt.items.add(f);
              const inp = document.getElementById('songInput');
              Object.defineProperty(inp, 'files', {{ value: dt.files, configurable: true }});
              inp.dispatchEvent(new Event('change'));
              for (let i = 0; i < 40; i++) {{
                if (!document.getElementById('dlWavZipBtn').disabled) break;
                await new Promise(r => setTimeout(r, 40));
              }}
              window.__lastBlob = null;
              document.getElementById('dlWavZipBtn').click();
              for (let i = 0; i < 50; i++) {{
                if (window.__lastBlob && document.getElementById('exportStatus').textContent.startsWith('Exported')) break;
                await new Promise(r => setTimeout(r, 40));
              }}
              const z = await JSZip.loadAsync(window.__lastBlob);
              const names = Object.keys(z.files).sort();
              const wavData = await z.file('audio_slices/Part_01_audio.wav').async('uint8array');
              URL.createObjectURL = origCreate;
              return {{
                names,
                wavMagic: String.fromCharCode(...wavData.slice(0, 4)),
                status: document.getElementById('exportStatus').textContent
              }};
            }})()""",
        )
        self.assertIn("manifest.json", res["names"])
        self.assertIn("audio_slices/Part_01_audio.wav", res["names"])
        self.assertEqual(res["wavMagic"], "RIFF")
        self.assertIn("WAV slices", res["status"])
        self._assert_no_browser_errors()

    def test_t1_f4_download_all_zip_includes_clips_wav_and_verify(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
              const origCreate = URL.createObjectURL;
              const captured = [];
              URL.createObjectURL = (blob) => {{
                captured.push(blob);
                return origCreate(blob);
              }};
              const dt = new DataTransfer();
              dt.items.add(new File([{json.dumps(self.manifest_text)}], 'manifest.json', {{ type: 'application/json' }}));
              dt.items.add(new File([new Uint8Array([0,0,0,1])], '3_verification_full_playthrough.mp4', {{ type: 'video/mp4' }}));
              dt.items.add(new File([new Uint8Array([0,0,0,2])], 'Part_01_02_19EAck3_MSg.mp4', {{ type: 'video/mp4' }}));
              dt.items.add(new File([b64ToBytes({json.dumps(self.wav_b64)})], 'song.wav', {{ type: 'audio/wav' }}));
              document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
              for (let i = 0; i < 40; i++) {{
                if (!document.getElementById('dlWavZipBtn').disabled && window.SeamlessApp.getState().verifyMedia) break;
                await new Promise(r => setTimeout(r, 40));
              }}
              captured.length = 0;
              document.getElementById('dlAllZipBtn').click();
              for (let i = 0; i < 40; i++) {{
                if (captured.length > 0 && document.getElementById('exportStatus').textContent.includes('seamless_shorts_bundle.zip')) break;
                await new Promise(r => setTimeout(r, 40));
              }}
              const z = await JSZip.loadAsync(captured[0]);
              const names = Object.keys(z.files).sort();
              URL.createObjectURL = origCreate;
              return {{ names }};
            }})()""",
        )
        self.assertIn("manifest.json", res["names"])
        self.assertIn("3_verification_full_playthrough.mp4", res["names"])
        self.assertIn("audio_slices/Part_01_audio.wav", res["names"])
        self.assertIn("clips/Part_01_02_19EAck3_MSg.mp4", res["names"])
        self._assert_no_browser_errors()

    def test_t1_f4_single_part_wav_and_mp4_download_buttons_enabled(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
              const dt = new DataTransfer();
              dt.items.add(new File([{json.dumps(self.manifest_text)}], 'manifest.json', {{ type: 'application/json' }}));
              dt.items.add(new File([new Uint8Array([0,0,0,2])], 'Part_01_02_19EAck3_MSg.mp4', {{ type: 'video/mp4' }}));
              dt.items.add(new File([b64ToBytes({json.dumps(self.wav_b64)})], 'song.wav', {{ type: 'audio/wav' }}));
              document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
              for (let i = 0; i < 40; i++) {{
                const wavBtn = document.querySelector('#clipsList .clip-row-card [data-act="wav"]');
                if (wavBtn && !wavBtn.disabled) break;
                await new Promise(r => setTimeout(r, 40));
              }}
              const card = document.querySelector('#clipsList .clip-row-card');
              const wavBtn = card.querySelector('[data-act="wav"]');
              const mp4Btn = card.querySelector('[data-act="dl"]');
              wavBtn.click();
              return {{
                wavDisabled: wavBtn.disabled,
                mp4Disabled: mp4Btn.disabled,
                status: document.getElementById('exportStatus').textContent
              }};
            }})()""",
        )
        self.assertFalse(res["wavDisabled"])
        self.assertFalse(res["mp4Disabled"])
        self.assertIn("Part_01_audio.wav", res["status"])
        self._assert_no_browser_errors()

    def test_t2_f4_export_buttons_disabled_when_workspace_empty(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            """(() => ({
              verify: document.getElementById('dlVerifyBtn').disabled,
              manifest: document.getElementById('dlManifestBtn').disabled,
              wavZip: document.getElementById('dlWavZipBtn').disabled,
              allZip: document.getElementById('dlAllZipBtn').disabled
            }))()""",
        )
        self.assertTrue(all(res.values()))
        self._assert_no_browser_errors()

    def test_t2_f4_wav_fade_clamped_for_ultra_short_audio_slice(self) -> None:
        out = self._run_node_core(
            """
const sr = 8000;
const ch = new Float32Array(sr).fill(0.8);
const buf = { numberOfChannels: 1, sampleRate: sr, getChannelData: () => ch };
// 20ms slice = 160 samples, 50ms fade requested -> clamps to floor(160/3) = 53 samples
const res = core.sliceAudioBufferToWav(buf, 0.0, 0.02, 50);
assert.strictEqual(res.numSamples, 160);
assert.strictEqual(res.bytes.length, 44 + 160 * 2);
const dv = new DataView(res.bytes.buffer);
assert.strictEqual(dv.getInt16(44, true), 0);
assert.ok(dv.getInt16(44 + 80 * 2, true) > 20000);
console.log('SHORT_SLICE_FADE_OK');
"""
        )
        self.assertIn("SHORT_SLICE_FADE_OK", out)

    def test_t2_f4_wav_slice_end_past_audio_length_clamped(self) -> None:
        out = self._run_node_core(
            """
const sr = 8000;
const ch = new Float32Array(sr * 2).fill(0.5); // 2s total
const buf = { numberOfChannels: 1, sampleRate: sr, getChannelData: () => ch };
const res = core.sliceAudioBufferToWav(buf, 1.0, 999.0, 15);
assert.strictEqual(res.numSamples, 8000); // clamped to 1s remaining
console.log('OOB_SLICE_CLAMP_OK');
"""
        )
        self.assertIn("OOB_SLICE_CLAMP_OK", out)

    def test_t2_f4_wav_pcm_clipping_bounds_for_overscale_floats(self) -> None:
        out = self._run_node_core(
            """
const ch = new Float32Array([2.5, -3.5, 0.0]);
const bytes = core.encodeWavFromChannels([ch], 8000);
const dv = new DataView(bytes.buffer);
assert.strictEqual(dv.getInt16(44, true), 32767);
assert.strictEqual(dv.getInt16(46, true), -32768);
assert.strictEqual(dv.getInt16(48, true), 0);
console.log('PCM_SATURATION_OK');
"""
        )
        self.assertIn("PCM_SATURATION_OK", out)

    def test_t2_f4_manifest_download_without_audio_buffer_still_works(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
              const mDis = document.getElementById('dlManifestBtn').disabled;
              const wDis = document.getElementById('dlWavZipBtn').disabled;
              document.getElementById('dlManifestBtn').click();
              return {{ mDis, wDis, status: document.getElementById('exportStatus').textContent }};
            }})()""",
        )
        self.assertFalse(res["mDis"])
        self.assertTrue(res["wDis"])
        self.assertIn("Exported manifest.json", res["status"])
        self._assert_no_browser_errors()

    # =========================================================================
    # F5: MODERN UI + THEME ENGINE (5 Tier 1 + 5 Tier 2)
    # =========================================================================

    def test_t1_f5_dark_light_theme_toggle_updates_tokens_and_labels(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            """(async () => {
              const darkBg = getComputedStyle(document.body).backgroundColor;
              const darkTheme = document.documentElement.getAttribute('data-theme');
              document.getElementById('themeToggleBtn').click();
              await new Promise(r => setTimeout(r, 260));
              const lightTheme = document.documentElement.getAttribute('data-theme');
              const lightBg = getComputedStyle(document.body).backgroundColor;
              const pressed = document.getElementById('themeToggleBtn').getAttribute('aria-pressed');
              const label = document.getElementById('themeLabel').textContent.trim();
              return { darkBg, darkTheme, lightTheme, lightBg, pressed, label };
            })()""",
        )
        self.assertEqual(res["darkTheme"], "dark")
        self.assertEqual(res["lightTheme"], "light")
        self.assertNotEqual(res["darkBg"], res["lightBg"])
        self.assertEqual(res["pressed"], "true")
        self.assertEqual(res["label"], "Dark Mode")
        self._assert_no_browser_errors()

    def test_t1_f5_theme_persists_across_page_reload(self) -> None:
        self._prep_browser()
        self.cdp.eval(self.sid, "document.getElementById('themeToggleBtn').click()")
        self.cdp.reload(self.sid, wait_ms=200)
        theme = self.cdp.eval(self.sid, "document.documentElement.getAttribute('data-theme')")
        self.assertEqual(theme, "light")
        self._assert_no_browser_errors()

    def test_t1_f5_three_step_workflow_stepper_progression(self) -> None:
        self._prep_browser()
        mini_manifest = {
            "fade_ms": 15,
            "parts": [
                {"part_num": 1, "filename": "Part_01.mp4", "source_filename": "01.mp4", "duration": 1.0, "song_start": 0.0, "song_end": 1.0, "verify_start": 0.0, "verify_end": 1.0},
                {"part_num": 2, "filename": "Part_02.mp4", "source_filename": "02.mp4", "duration": 1.0, "song_start": 1.0, "song_end": 2.0, "verify_start": 1.0, "verify_end": 2.0},
                {"part_num": 3, "filename": "Part_03.mp4", "source_filename": "03.mp4", "duration": 1.0, "song_start": 2.0, "song_end": 3.0, "verify_start": 2.0, "verify_end": 3.0},
            ],
        }
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
              const initDone = document.getElementById('step1Indicator').classList.contains('is-done');
              await window.SeamlessApp.ingestManifestAndCsv({json.dumps(mini_manifest)}, '');
              const f = new File([b64ToBytes({json.dumps(self.wav_b64)})], 'song.wav', {{ type: 'audio/wav' }});
              const dt = new DataTransfer();
              dt.items.add(f);
              const inp = document.getElementById('songInput');
              Object.defineProperty(inp, 'files', {{ value: dt.files, configurable: true }});
              inp.dispatchEvent(new Event('change'));
              for (let i = 0; i < 40; i++) {{
                if (document.getElementById('step3Indicator').classList.contains('is-done')) break;
                await new Promise(r => setTimeout(r, 40));
              }}
              return {{
                initDone,
                s1Done: document.getElementById('step1Indicator').classList.contains('is-done'),
                s2Active: document.getElementById('step2Indicator').classList.contains('is-active'),
                s3Done: document.getElementById('step3Indicator').classList.contains('is-done'),
                s1Sub: document.getElementById('step1Sub').textContent,
                s2Sub: document.getElementById('step2Sub').textContent
              }};
            }})()""",
        )
        self.assertFalse(res["initDone"])
        self.assertTrue(res["s1Done"])
        self.assertTrue(res["s2Active"])
        self.assertTrue(res["s3Done"])
        self.assertIn("song ready", res["s1Sub"])
        self.assertIn("2 transition cuts", res["s2Sub"])
        self._assert_no_browser_errors()

    def test_t1_f5_responsive_layouts_desktop_tablet_mobile_no_overflow(self) -> None:
        self._prep_browser()
        self.__class__._viewport_dirty = True
        self.cdp.eval(self.sid, f"window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '')")
        for w, h, mob in [(1440, 900, False), (900, 800, False), (390, 844, True)]:
            self.cdp.set_viewport(self.sid, w, h, mob)
            overflow = self.cdp.eval(self.sid, "document.documentElement.scrollWidth - window.innerWidth")
            self.assertLessEqual(overflow, 1, f"Horizontal overflow at {w}x{h}: {overflow}")
        self._assert_no_browser_errors()

    def test_t1_f5_shortcuts_popover_toggle_and_accessibility_roles(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            """(() => {
              const btn = document.getElementById('shortcutsBtn');
              const pop = document.getElementById('shortcutsPopover');
              const beforeHidden = pop.hidden;
              btn.click();
              const openHidden = pop.hidden;
              const openExpanded = btn.getAttribute('aria-expanded');
              document.getElementById('closeShortcutsBtn').click();
              return {
                beforeHidden, openHidden, openExpanded,
                closedHidden: pop.hidden,
                hasToolbar: Boolean(document.querySelector('[role="toolbar"]')),
                hasProgressbar: Boolean(document.querySelector('[role="progressbar"]'))
              };
            })()""",
        )
        self.assertTrue(res["beforeHidden"])
        self.assertFalse(res["openHidden"])
        self.assertEqual(res["openExpanded"], "true")
        self.assertTrue(res["closedHidden"])
        self.assertTrue(res["hasToolbar"])
        self.assertTrue(res["hasProgressbar"])
        self._assert_no_browser_errors()

    def test_t2_f5_file_protocol_localstorage_throws_handled_safely(self) -> None:
        self._prep_browser(mark_dirty=True)
        assert self.cdp is not None
        res = self.cdp.eval(
            self.sid,
            """(() => {
              const orig = Object.getOwnPropertyDescriptor(window, 'localStorage');
              Object.defineProperty(window, 'localStorage', {
                configurable: true,
                get() { throw new DOMException('Blocked by sandbox', 'SecurityError'); }
              });
              try {
                document.getElementById('themeToggleBtn').click();
                return { theme: document.documentElement.getAttribute('data-theme') };
              } finally {
                if (orig) Object.defineProperty(window, 'localStorage', orig);
              }
            })()""",
        )
        self.assertEqual(res["theme"], "light")
        self._assert_no_browser_errors()

    def test_t2_f5_xss_escaping_in_csv_youtube_titles_and_filenames(self) -> None:
        self._prep_browser()
        xss_csv = (
            "Content,Video title,Views\r\n"
            '19EAck3_MSg,"<img src=x onerror=""window.__xssFired=1""><script>window.__xssFired=2</script>",100\r\n'
        )
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              window.__xssFired = 0;
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, {json.dumps(xss_csv)});
              const titleText = document.querySelector('#clipsList .clip-yt-title').textContent;
              const imgs = document.querySelectorAll('#clipsList img').length;
              return {{ fired: window.__xssFired, titleText, imgs }};
            }})()""",
        )
        self.assertEqual(res["fired"], 0)
        self.assertEqual(res["imgs"], 0)
        self.assertIn("<img src=x", res["titleText"])
        self._assert_no_browser_errors()

    def test_t2_f5_curated_design_guardrails_no_purple_or_overflow_text(self) -> None:
        css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("fonts.googleapis.com", html)
        self.assertNotIn("fonts.googleapis.com", css)
        self.assertIn("object-fit: contain", css)
        # No purple-on-dark accent hue (255..300 deg)
        hsl_hues = [int(m) for m in re.findall(r"hsl\(\s*(\d+)", css)]
        for hue in hsl_hues:
            self.assertFalse(255 <= hue <= 300, f"Unexpected purple hue in styles.css: {hue}")

    def test_t2_f5_unknown_stored_theme_falls_back_to_dark(self) -> None:
        self._prep_browser(mark_dirty=True)
        assert self.cdp is not None
        self.cdp.eval(self.sid, "localStorage.setItem('seamless_shorts_theme', 'neon-purple-garbage');")
        self.cdp.reload(self.sid, wait_ms=200)
        theme = self.cdp.eval(self.sid, "document.documentElement.getAttribute('data-theme')")
        self.assertEqual(theme, "dark")
        self._assert_no_browser_errors()

    def test_t2_f5_very_long_title_truncates_cleanly_without_breaking_grid(self) -> None:
        self._prep_browser()
        long_title = "VeryLongJugglingTitle_" + ("X" * 280)
        csv_text = f"Content,Video title,Views\r\n19EAck3_MSg,{long_title},42\r\n"
        self.__class__._viewport_dirty = True
        self.cdp.set_viewport(self.sid, 390, 844, True)
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, {json.dumps(csv_text)});
              return {{
                overflow: document.documentElement.scrollWidth - window.innerWidth,
                cardWidth: document.querySelector('#clipsList .clip-row-card').getBoundingClientRect().width
              }};
            }})()""",
        )
        self.assertLessEqual(res["overflow"], 1)
        self.assertLessEqual(res["cardWidth"], 390)
        self._assert_no_browser_errors()

    # =========================================================================
    # F6: INTERACTIVE TIMELINE + WAVEFORM (5 Tier 1 + 5 Tier 2)
    # =========================================================================

    def test_t1_f6_timeline_renders_proportional_blocks_cuts_and_ruler(self) -> None:
        self._prep_browser()
        manifest = {
            "fade_ms": 15,
            "parts": [
                {"part_num": 1, "filename": "P1.mp4", "duration": 10.0, "song_start": 0, "song_end": 10, "verify_start": 0, "verify_end": 10},
                {"part_num": 2, "filename": "P2.mp4", "duration": 20.0, "song_start": 10, "song_end": 30, "verify_start": 10, "verify_end": 30},
                {"part_num": 3, "filename": "P3.mp4", "duration": 30.0, "song_start": 30, "song_end": 60, "verify_start": 30, "verify_end": 60},
            ],
        }
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              await window.SeamlessApp.ingestManifestAndCsv({json.dumps(manifest)}, '');
              const widths = [...document.querySelectorAll('#clipBlocksTrack .clip-block')].map(el => parseFloat(el.style.width));
              const cuts = [...document.querySelectorAll('#cutMarkersTrack .cut-marker')].map(el => el.textContent);
              const ticks = document.querySelectorAll('#timeRuler .ruler-tick').length;
              return {{ widths, cuts, ticks }};
            }})()""",
        )
        self.assertAlmostEqual(res["widths"][0], 16.67, delta=0.5)
        self.assertAlmostEqual(res["widths"][1], 33.33, delta=0.5)
        self.assertAlmostEqual(res["widths"][2], 50.0, delta=0.5)
        self.assertEqual(res["cuts"], ["C1", "C2"])
        self.assertGreaterEqual(res["ticks"], 5)
        self._assert_no_browser_errors()

    def test_t1_f6_click_clip_block_and_cut_marker_seeks_playhead(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
              document.querySelector('#clipBlocksTrack .clip-block[data-idx="2"]').click();
              const tBlock = window.SeamlessApp.getState().globalTime;
              const leftPct = parseFloat(document.getElementById('playheadLine').style.left);
              document.querySelector('#cutMarkersTrack .cut-marker[data-idx="1"]').click();
              const tCut = window.SeamlessApp.getState().globalTime;
              if (window.SeamlessApp.getState().playing) document.getElementById('playPauseBtn').click();
              return {{ tBlock, leftPct, tCut }};
            }})()""",
        )
        self.assertAlmostEqual(res["tBlock"], 74.096, delta=0.2)
        self.assertGreater(res["leftPct"], 20.0)
        self.assertAlmostEqual(res["tCut"], 12.049, delta=0.25)
        self._assert_no_browser_errors()

    def test_t1_f6_pointer_scrub_and_drag_on_timeline_shell(self) -> None:
        self._prep_browser()
        box = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
              const lane = document.getElementById('waveformLane');
              lane.scrollIntoView({{ block: 'center' }});
              const r = document.getElementById('playheadOverlay').getBoundingClientRect();
              const lr = lane.getBoundingClientRect();
              return {{
                total: window.SeamlessApp.getState().totalDuration,
                midX: r.left + r.width * 0.5,
                q3X: r.left + r.width * 0.75,
                y: lr.top + lr.height * 0.5
              }};
            }})()""",
        )
        self.cdp.dispatch_mouse(self.sid, "mousePressed", box["midX"], box["y"])
        t_mid = self.cdp.eval(self.sid, "window.SeamlessApp.getState().globalTime")
        self.cdp.dispatch_mouse(self.sid, "mouseMoved", box["q3X"], box["y"], buttons=1)
        res = self.cdp.eval(
            self.sid,
            """({
              tDrag: window.SeamlessApp.getState().globalTime,
              tooltipHidden: document.getElementById('playheadTooltip').hidden
            })""",
        )
        self.cdp.dispatch_mouse(self.sid, "mouseReleased", box["q3X"], box["y"])
        self.assertAlmostEqual(t_mid, box["total"] * 0.5, delta=1.5)
        self.assertAlmostEqual(res["tDrag"], box["total"] * 0.75, delta=1.5)
        self.assertFalse(res["tooltipHidden"])
        self._assert_no_browser_errors()

    def test_t1_f6_waveform_canvas_draws_nonempty_pixels_on_song_decode(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
              const f = new File([b64ToBytes({json.dumps(self.wav_b64)})], 'wave.wav', {{ type: 'audio/wav' }});
              const dt = new DataTransfer();
              dt.items.add(f);
              const inp = document.getElementById('songInput');
              Object.defineProperty(inp, 'files', {{ value: dt.files, configurable: true }});
              inp.dispatchEvent(new Event('change'));
              for (let i = 0; i < 40; i++) {{
                const st = window.SeamlessApp.getState();
                if (st.songMedia && st.songMedia.peaks) break;
                await new Promise(r => setTimeout(r, 40));
              }}
              const canvas = document.getElementById('waveformCanvas');
              const ctx = canvas.getContext('2d');
              const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
              let nonZeroAlpha = 0;
              for (let i = 3; i < data.length; i += 4) {{
                if (data[i] > 0) nonZeroAlpha++;
              }}
              return {{
                hintHidden: document.getElementById('waveformHint').hidden,
                peaksLen: window.SeamlessApp.getState().songMedia.peaks.length,
                nonZeroAlpha
              }};
            }})()""",
        )
        self.assertTrue(res["hintHidden"])
        self.assertGreater(res["peaksLen"], 32)
        self.assertGreater(res["nonZeroAlpha"], 100)
        self._assert_no_browser_errors()

    def test_t1_f6_keyboard_shortcuts_j_l_bracket_m_space(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
              document.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'l', bubbles: true }}));
              const tL = window.SeamlessApp.getState().globalTime;
              document.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'j', bubbles: true }}));
              const tJ = window.SeamlessApp.getState().globalTime;
              document.dispatchEvent(new KeyboardEvent('keydown', {{ key: ']', bubbles: true }}));
              const tNext = window.SeamlessApp.getState().globalTime;
              document.dispatchEvent(new KeyboardEvent('keydown', {{ key: '[', bubbles: true }}));
              const tPrev = window.SeamlessApp.getState().globalTime;
              if (window.SeamlessApp.getState().playing) document.dispatchEvent(new KeyboardEvent('keydown', {{ code: 'Space', key: ' ', bubbles: true }}));
              const playingAfterPause = window.SeamlessApp.getState().playing;
              const muteBefore = window.SeamlessApp.getState().muteOriginal;
              document.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'm', bubbles: true }}));
              const muteAfter = window.SeamlessApp.getState().muteOriginal;
              return {{ tL, tJ, tNext, tPrev, playingAfterPause, muteBefore, muteAfter }};
            }})()""",
        )
        self.assertAlmostEqual(res["tL"], 5.0, delta=0.1)
        self.assertAlmostEqual(res["tJ"], 0.0, delta=0.1)
        self.assertAlmostEqual(res["tNext"], 12.049, delta=0.25)
        self.assertFalse(res["playingAfterPause"])
        self.assertNotEqual(res["muteBefore"], res["muteAfter"])
        self._assert_no_browser_errors()

    def test_t2_f6_keyboard_shortcuts_ignored_inside_input_textarea_select(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
              const inp = document.getElementById('standaloneFadeMs');
              const sel = document.getElementById('speedSelect');
              const beforeMute = window.SeamlessApp.getState().muteOriginal;
              const beforeTime = window.SeamlessApp.getState().globalTime;
              inp.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'm', bubbles: true }}));
              inp.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'l', bubbles: true }}));
              sel.dispatchEvent(new KeyboardEvent('keydown', {{ key: ']', bubbles: true }}));
              return {{
                beforeMute,
                afterMute: window.SeamlessApp.getState().muteOriginal,
                beforeTime,
                afterTime: window.SeamlessApp.getState().globalTime
              }};
            }})()""",
        )
        self.assertEqual(res["beforeMute"], res["afterMute"])
        self.assertEqual(res["beforeTime"], res["afterTime"])
        self._assert_no_browser_errors()

    def test_t2_f6_waveform_deterministic_fallback_when_undecoded(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
              const canvas = document.getElementById('waveformCanvas');
              const ctx = canvas.getContext('2d');
              const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
              let nonZeroAlpha = 0;
              for (let i = 3; i < data.length; i += 4) {{
                if (data[i] > 0) nonZeroAlpha++;
              }}
              return {{
                hasPeaks: Boolean(window.SeamlessApp.getState().songMedia && window.SeamlessApp.getState().songMedia.peaks),
                nonZeroAlpha
              }};
            }})()""",
        )
        self.assertFalse(res["hasPeaks"])
        self.assertGreater(res["nonZeroAlpha"], 50)
        self._assert_no_browser_errors()

    def test_t2_f6_pointer_drag_outside_left_and_right_bounds_clamped(self) -> None:
        self._prep_browser()
        box = self.cdp.eval(
            self.sid,
            f"""(async () => {{
              await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
              const lane = document.getElementById('waveformLane');
              lane.scrollIntoView({{ block: 'center' }});
              const r = document.getElementById('playheadOverlay').getBoundingClientRect();
              const lr = lane.getBoundingClientRect();
              return {{
                total: window.SeamlessApp.getState().totalDuration,
                startX: r.left + r.width * 0.3,
                y: lr.top + lr.height * 0.5,
                pastLeftX: Math.max(0, r.left - 80),
                pastRightX: r.right + 120
              }};
            }})()""",
        )
        self.cdp.dispatch_mouse(self.sid, "mousePressed", box["startX"], box["y"])
        self.cdp.dispatch_mouse(self.sid, "mouseMoved", box["pastLeftX"], box["y"], buttons=1)
        left_res = self.cdp.eval(
            self.sid,
            "({ t: window.SeamlessApp.getState().globalTime, pct: document.getElementById('playheadLine').style.left })",
        )
        self.cdp.dispatch_mouse(self.sid, "mouseMoved", box["pastRightX"], box["y"], buttons=1)
        right_res = self.cdp.eval(
            self.sid,
            "({ t: window.SeamlessApp.getState().globalTime, pct: document.getElementById('playheadLine').style.left })",
        )
        self.cdp.dispatch_mouse(self.sid, "mouseReleased", box["pastRightX"], box["y"])
        self.assertEqual(left_res["t"], 0)
        self.assertEqual(left_res["pct"], "0%")
        self.assertAlmostEqual(right_res["t"], box["total"], delta=0.05)
        self.assertEqual(right_res["pct"], "100%")
        self._assert_no_browser_errors()

    def test_t2_f6_zero_duration_timeline_pointer_events_safe(self) -> None:
        self._prep_browser()
        res = self.cdp.eval(
            self.sid,
            """(() => {
              const shell = document.getElementById('timelineShell');
              shell.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: 200 }));
              shell.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: 300 }));
              shell.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
              return {
                leftStyle: document.getElementById('playheadLine').style.left,
                globalTime: window.SeamlessApp.getState().globalTime
              };
            })()""",
        )
        self.assertNotIn("NaN", res["leftStyle"])
        self.assertEqual(res["globalTime"], 0)
        self._assert_no_browser_errors()

    def test_t2_f6_compute_waveform_peaks_bounds_and_tiny_buffer(self) -> None:
        out = self._run_node_core(
            """
const tiny = { getChannelData: () => new Float32Array([0.1, -0.9, 0.2]) };
const minP = core.computeWaveformPeaks(tiny, 5);
const maxP = core.computeWaveformPeaks(tiny, 9999);
assert.strictEqual(minP.length, 32);
assert.strictEqual(maxP.length, 1200);
assert.ok(!Number.isNaN(minP[0]));
assert.ok(Math.abs(minP[1] - 0.9) < 1e-6);
console.log('PEAKS_BOUNDS_OK');
"""
        )
        self.assertIn("PEAKS_BOUNDS_OK", out)

    # =========================================================================
    # F15: SINGLE-FILE PLAYER Seamless_Shorts_Player.html (5 Tier 1 + 5 Tier 2)
    # =========================================================================

    def test_t1_f15_build_standalone_inlines_css_jszip_appjs(self) -> None:
        standalone = (WEB_DIR / "Seamless_Shorts_Player.html").read_text(encoding="utf-8")
        self.assertNotRegex(standalone, r'<link\b[^>]*href\s*=\s*["\']styles\.css["\']')
        self.assertNotRegex(standalone, r'<script\b[^>]*src\s*=\s*["\']app\.js["\']')
        self.assertNotRegex(standalone, r'<script\b[^>]*src\s*=\s*["\']jszip\.min\.js["\']')
        self.assertIn("SeamlessCore", standalone)
        self.assertIn("JSZip v3.10.1", standalone)
        self.assertEqual(
            hashlib.sha256((WEB_DIR / "jszip.min.js").read_bytes()).hexdigest(),
            JSZIP_SHA256,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_out = Path(tmpdir) / "built.html"
            res = subprocess.run(
                [sys.executable, str(TOOLS_DIR / "build_standalone.py"), "--output", str(tmp_out)],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(tmp_out.read_text(encoding="utf-8"), standalone)

    def test_t1_f15_inline_scripts_pass_node_syntax_check(self) -> None:
        standalone = (WEB_DIR / "Seamless_Shorts_Player.html").read_text(encoding="utf-8")
        scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", standalone, flags=re.DOTALL | re.IGNORECASE)
        self.assertEqual(len(scripts), 2)
        node_bin = shutil.which("node")
        self.assertIsNotNone(node_bin)
        with tempfile.TemporaryDirectory() as tmpdir:
            for idx, body in enumerate(scripts):
                p = Path(tmpdir) / f"inline_{idx}.js"
                p.write_text(body, encoding="utf-8")
                chk = subprocess.run([node_bin, "--check", str(p)], capture_output=True, text=True)
                self.assertEqual(chk.returncode, 0, chk.stderr)

    def test_t1_f15_file_uri_loads_with_zero_network_requests_and_zero_errors(self) -> None:
        cdp = self._prep_browser(force_reload=True)
        logs = cdp.get_logs(self.sid)
        self.assertEqual(logs["exceptions"], [])
        self.assertEqual(logs["consoleErrors"], [])
        self.assertEqual(logs["failedRequests"], [])
        http_reqs = [u for u in logs["networkUrls"] if u.startswith("http://") or u.startswith("https://")]
        self.assertEqual(http_reqs, [], f"Standalone file:// player made external HTTP calls: {http_reqs}")

    def test_t1_f15_standalone_html_copied_to_isolated_temp_dir_works(self) -> None:
        _ensure_browser_or_skip(self)
        assert self.cdp is not None
        with tempfile.TemporaryDirectory() as tmpdir:
            alone = Path(tmpdir) / "Seamless_Shorts_Player.html"
            shutil.copy2(WEB_DIR / "Seamless_Shorts_Player.html", alone)
            tab = self.cdp.new_tab(alone.resolve().as_uri(), wait_ms=220)
            try:
                res = self.cdp.eval(
                    tab,
                    f"""(async () => {{
                      await window.SeamlessApp.ingestManifestAndCsv({self.manifest_text}, '');
                      document.getElementById('themeToggleBtn').click();
                      return {{
                        jszip: typeof JSZip,
                        parts: window.SeamlessApp.getState().parts.length,
                        theme: document.documentElement.getAttribute('data-theme')
                      }};
                    }})()""",
                )
                logs = self.cdp.get_logs(tab)
                self.assertEqual(res["jszip"], "function")
                self.assertEqual(res["parts"], 9)
                self.assertEqual(res["theme"], "light")
                self.assertEqual(logs["exceptions"], [])
                self.assertEqual(logs["consoleErrors"], [])
            finally:
                self.cdp.close_tab(tab)

    def test_t1_f15_served_via_http_from_server_route(self) -> None:
        _, base_url, stop = start_agent_server(cwd=ROOT_DIR, port_arg=0)
        try:
            disk = (WEB_DIR / "Seamless_Shorts_Player.html").read_bytes()
            for route in ("/Seamless_Shorts_Player.html", "/web/Seamless_Shorts_Player.html"):
                st, hdrs, body = http_request(f"{base_url}{route}")
                self.assertEqual(st, 200)
                self.assertEqual(hdrs.get("content-type"), "text/html; charset=utf-8")
                self.assertEqual(body, disk)
        finally:
            stop()

    def test_t2_f15_script_and_style_closing_tag_escaping(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            wdir = Path(tmpdir) / "web"
            wdir.mkdir()
            (wdir / "index.html").write_text(
                '<!doctype html><html><head><link rel="stylesheet" href="styles.css"></head>'
                '<body><script src="jszip.min.js"></script><script src="app.js"></script></body></html>',
                encoding="utf-8",
            )
            (wdir / "styles.css").write_text('body { content: "</style></STYLE>"; }', encoding="utf-8")
            (wdir / "jszip.min.js").write_text("var JSZip = function(){};", encoding="utf-8")
            (wdir / "app.js").write_text('var tag = "</script></SCRIPT>";', encoding="utf-8")
            out_file = Path(tmpdir) / "out.html"
            res = subprocess.run(
                [sys.executable, str(TOOLS_DIR / "build_standalone.py"), "--web-dir", str(wdir), "--output", str(out_file)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            rendered = out_file.read_text(encoding="utf-8")
            scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", rendered, flags=re.DOTALL | re.IGNORECASE)
            self.assertEqual(len(scripts), 2)
            self.assertIn(r"<\/script><\/SCRIPT>", scripts[1])
            styles = re.findall(r"<style\b[^>]*>(.*?)</style>", rendered, flags=re.DOTALL | re.IGNORECASE)
            self.assertEqual(len(styles), 1)
            self.assertIn(r"<\/style><\/STYLE>", styles[0])

    def test_t2_f15_build_standalone_missing_asset_raises_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            wdir = Path(tmpdir) / "web"
            wdir.mkdir()
            (wdir / "index.html").write_text((WEB_DIR / "index.html").read_text(encoding="utf-8"), encoding="utf-8")
            (wdir / "styles.css").write_text("body{}", encoding="utf-8")
            (wdir / "app.js").write_text("", encoding="utf-8")
            # Intentionally omit jszip.min.js
            res = subprocess.run(
                [sys.executable, str(TOOLS_DIR / "build_standalone.py"), "--web-dir", str(wdir), "--output", str(Path(tmpdir) / "o.html")],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("Missing required web asset", res.stderr)

    def test_t2_f15_build_standalone_rejects_external_http_asset_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            wdir = Path(tmpdir) / "web"
            wdir.mkdir()
            html = (WEB_DIR / "index.html").read_text(encoding="utf-8").replace(
                "</head>",
                '<script src="https://cdn.example.com/evil.js"></script></head>',
            )
            (wdir / "index.html").write_text(html, encoding="utf-8")
            (wdir / "styles.css").write_text("body{}", encoding="utf-8")
            (wdir / "jszip.min.js").write_text("var JSZip=function(){};", encoding="utf-8")
            (wdir / "app.js").write_text("", encoding="utf-8")
            res = subprocess.run(
                [sys.executable, str(TOOLS_DIR / "build_standalone.py"), "--web-dir", str(wdir), "--output", str(Path(tmpdir) / "o.html")],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("external http", res.stderr.lower())

    def test_t2_f15_standalone_works_when_served_on_static_http_without_api_health(self) -> None:
        _ensure_browser_or_skip(self)
        assert self.cdp is not None
        with tempfile.TemporaryDirectory() as tmpdir:
            shutil.copy2(WEB_DIR / "Seamless_Shorts_Player.html", Path(tmpdir) / "index.html")
            _, base_url, stop = start_static_server(Path(tmpdir))
            try:
                tab = self.cdp.new_tab(f"{base_url}/", wait_ms=300)
                try:
                    mode = self.cdp.eval(tab, "document.getElementById('modeBadge').dataset.mode")
                    hidden = self.cdp.eval(tab, "document.getElementById('studioPanel').hidden")
                    logs = self.cdp.get_logs(tab)
                    self.assertEqual(mode, "standalone")
                    self.assertTrue(hidden)
                    self.assertEqual(logs["exceptions"], [])
                    self.assertEqual(logs["consoleErrors"], [])
                finally:
                    self.cdp.close_tab(tab)
            finally:
                stop()

    def test_t2_f15_no_es_module_keywords_in_standalone(self) -> None:
        standalone = (WEB_DIR / "Seamless_Shorts_Player.html").read_text(encoding="utf-8")
        self.assertNotRegex(standalone, r'<script\b[^>]*\btype\s*=\s*["\']module["\']')
        app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        self.assertNotRegex(app_js, r"(?m)^\s*(?:import|export)\b")


if __name__ == "__main__":
    unittest.main()
