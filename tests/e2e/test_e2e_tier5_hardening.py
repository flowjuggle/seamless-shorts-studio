#!/usr/bin/env python3
"""Tier 5 E2E Adversarial Hardening Tests (Chromium CDP, Atomic Verify, Drift, Security)."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests.e2e import (
    AGENT_PATH,
    WEB_DIR,
    ChromiumCDPBridge,
    http_request,
    make_wav_bytes,
    start_agent_server,
)


def _make_h264_mp4(ffmpeg_bin: str, out_path: Path, duration: float = 1.0, fps: int = 30, size: str = "360x640", color: str = "red") -> None:
    subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={size}:r={fps}:d={duration:.3f}",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )


class TestTier5AdversarialHardening(unittest.TestCase):
    """Tier 5 regression & adversarial E2E checks across web app and Studio backend."""

    cdp: ChromiumCDPBridge | None = None

    @classmethod
    def setUpClass(cls) -> None:
        if (shutil.which("chromium") or shutil.which("google-chrome")) and shutil.which("node"):
            cls.cdp = ChromiumCDPBridge()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.cdp is not None:
            cls.cdp.close()
            cls.cdp = None

    def _require_cdp(self) -> ChromiumCDPBridge:
        if self.cdp is None:
            self.skipTest("chromium or node not available")
        assert self.cdp is not None
        return self.cdp

    def test_t5_virtual_clock_loop_clip_and_playable_recovery_in_chromium(self) -> None:
        cdp = self._require_cdp()
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            self.skipTest("ffmpeg required to generate playable H.264 clip")

        with tempfile.TemporaryDirectory() as tmpdir:
            clip_path = Path(tmpdir) / "02_playable.mp4"
            _make_h264_mp4(ffmpeg_bin, clip_path, duration=1.2, fps=30, color="green")
            clip_b64 = base64.b64encode(clip_path.read_bytes()).decode("ascii")
            wav_b64 = base64.b64encode(make_wav_bytes(sample_rate=8000, duration_sec=3.0, freq=440.0)).decode("ascii")

            sid = cdp.new_tab((WEB_DIR / "Seamless_Shorts_Player.html").resolve().as_uri(), wait_ms=250)
            try:
                res = cdp.eval(
                    sid,
                    f"""(async () => {{
                      const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
                      // 1. Virtual clock + Loop: Current Clip must stay inside Part 0 [0, 0.5]
                      await window.SeamlessApp.ingestManifestAndCsv({{
                        fade_ms: 15,
                        parts: [
                          {{ part_num: 1, filename: 'Part_01_v.mp4', source_filename: '01_v.mp4', duration: 0.5, song_start: 0, song_end: 0.5, verify_start: 0, verify_end: 0.5 }},
                          {{ part_num: 2, filename: 'Part_02_v.mp4', source_filename: '02_v.mp4', duration: 0.5, song_start: 0.5, song_end: 1.0, verify_start: 0.5, verify_end: 1.0 }}
                        ]
                      }}, '');
                      document.getElementById('loopClipBtn').click(); // Loop: Current Clip = true
                      document.getElementById('playPauseBtn').click();
                      await new Promise(r => setTimeout(r, 850));
                      const loopState = {{
                        idx: window.SeamlessApp.getState().activePartIdx,
                        t: window.SeamlessApp.getState().globalTime,
                        loopClip: window.SeamlessApp.getState().loopClip,
                        vclk: window.SeamlessApp.getState().virtualClockMode,
                        playing: window.SeamlessApp.getState().playing
                      }};
                      document.getElementById('playPauseBtn').click(); // pause
                      document.getElementById('loopClipBtn').click(); // Loop: Full Sequence = false

                      // 2. Virtual clock recovery: Part 0 unplayable/missing -> Part 1 real H.264 playable
                      document.getElementById('clearWorkspaceBtn').click();
                      const playableFile = new File([b64ToBytes({json.dumps(clip_b64)})], '02_playable.mp4', {{ type: 'video/mp4' }});
                      const songFile = new File([b64ToBytes({json.dumps(wav_b64)})], 'song.wav', {{ type: 'audio/wav' }});
                      const dt = new DataTransfer();
                      dt.items.add(playableFile);
                      dt.items.add(songFile);
                      document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
                      for (let i = 0; i < 60; i++) {{
                        if (window.SeamlessApp.getState().sourceClips.length === 1 && window.SeamlessApp.getState().songMedia?.audioBuffer) break;
                        await new Promise(r => setTimeout(r, 40));
                      }}
                      // Inject a broken Part 0 before the playable Part 1
                      const st = window.SeamlessApp.getState();
                      const playableRec = st.sourceClips[0];
                      st.parts = [
                        {{ part_num: 1, filename: 'Part_01_broken.mp4', source_filename: '01_broken.mp4', duration: 0.45, song_start: 0, song_end: 0.45, verify_start: 0, verify_end: 0.45, codecError: true }},
                        {{ part_num: 2, filename: 'Part_02_playable.mp4', source_filename: '02_playable.mp4', duration: 1.2, song_start: 0.45, song_end: 1.65, verify_start: 0.45, verify_end: 1.65, objectUrl: playableRec.objectUrl, codecError: false }}
                      ];
                      st.totalDuration = 1.65;
                      st.activePartIdx = 0;
                      st.globalTime = 0;
                      st.virtualClockMode = true;
                      document.getElementById('playPauseBtn').click();
                      await new Promise(r => setTimeout(r, 750));
                      const recState = {{
                        idx: window.SeamlessApp.getState().activePartIdx,
                        t: window.SeamlessApp.getState().globalTime,
                        vclk: window.SeamlessApp.getState().virtualClockMode,
                        playing: window.SeamlessApp.getState().playing
                      }};
                      if (window.SeamlessApp.getState().playing) document.getElementById('playPauseBtn').click();
                      return {{ loopState, recState }};
                    }})()""",
                )
                logs = cdp.get_logs(sid)
                self.assertEqual(res["loopState"]["idx"], 0)
                self.assertLess(res["loopState"]["t"], 0.55)
                self.assertTrue(res["loopState"]["loopClip"])
                self.assertTrue(res["loopState"]["vclk"])
                self.assertTrue(res["loopState"]["playing"])

                self.assertEqual(res["recState"]["idx"], 1)
                self.assertFalse(res["recState"]["vclk"], "virtualClockMode must recover when crossing into playable Part 1")
                self.assertGreaterEqual(res["recState"]["t"], 0.45)
                self.assertEqual(logs["exceptions"], [])
                self.assertEqual(logs["consoleErrors"], [])
            finally:
                cdp.close_tab(sid)

    def test_t5_studio_fade_ms_zero_preserved_in_input(self) -> None:
        cdp = self._require_cdp()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            ws = tmp / "shorts_workspace"
            ws.mkdir(parents=True)
            (ws / "manifest.json").write_text(
                json.dumps({"fade_ms": 0, "song_name": "", "parts": []}),
                encoding="utf-8",
            )
            _, base_url, stop = start_agent_server(cwd=tmp, port_arg=0)
            sid = cdp.new_tab(f"{base_url}/", wait_ms=450)
            try:
                res = cdp.eval(
                    sid,
                    """({
                      mode: document.getElementById('modeBadge').dataset.mode,
                      fadeVal: document.getElementById('fadeMs').value,
                      stateFade: window.SeamlessApp.getState().fadeMs
                    })""",
                )
                logs = cdp.get_logs(sid)
                self.assertEqual(res["mode"], "studio")
                self.assertEqual(res["fadeVal"], "0")
                self.assertEqual(res["stateFade"], 0)
                self.assertEqual(logs["exceptions"], [])
                self.assertEqual(logs["consoleErrors"], [])
            finally:
                cdp.close_tab(sid)
                stop()

    def test_t5_adversarial_zip_duplicate_basenames_0byte_audio_and_xss_coercion(self) -> None:
        cdp = self._require_cdp()
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            self.skipTest("ffmpeg required")

        with tempfile.TemporaryDirectory() as tmpdir:
            clip_p = Path(tmpdir) / "dup.mp4"
            _make_h264_mp4(ffmpeg_bin, clip_p, duration=1.0, fps=30, color="blue")
            clip_b64 = base64.b64encode(clip_p.read_bytes()).decode("ascii")
            valid_wav_b64 = base64.b64encode(make_wav_bytes(sample_rate=8000, duration_sec=3.5)).decode("ascii")

            sid = cdp.new_tab((WEB_DIR / "Seamless_Shorts_Player.html").resolve().as_uri(), wait_ms=250)
            try:
                res = cdp.eval(
                    sid,
                    f"""(async () => {{
                      const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
                      // Load valid song first
                      const dtSong = new DataTransfer();
                      dtSong.items.add(new File([b64ToBytes({json.dumps(valid_wav_b64)})], 'valid_song.wav', {{ type: 'audio/wav' }}));
                      document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dtSong }}));
                      for (let i = 0; i < 40; i++) {{
                        if (window.SeamlessApp.getState().songMedia?.audioBuffer) break;
                        await new Promise(r => setTimeout(r, 40));
                      }}

                      // Drop adversarial zip with duplicate basenames in different subdirs, traversal name, 0B video, 0B audio
                      const z = new JSZip();
                      z.file('nested/dir/dup.mp4', b64ToBytes({json.dumps(clip_b64)}));
                      z.file('other/dir/dup.mp4', b64ToBytes({json.dumps(clip_b64)}));
                      z.file('../../evil_traversal.mp4', b64ToBytes({json.dumps(clip_b64)}));
                      z.file('empty.mp4', new Uint8Array([]));
                      z.file('empty.wav', new Uint8Array([]));
                      const zipBlob = await z.generateAsync({{ type: 'blob' }});

                      const dtZip = new DataTransfer();
                      dtZip.items.add(new File([zipBlob], 'adversarial.zip', {{ type: 'application/zip' }}));
                      document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dtZip }}));
                      for (let i = 0; i < 80; i++) {{
                        if (window.SeamlessApp.getState().parts.length >= 3) break;
                        await new Promise(r => setTimeout(r, 50));
                      }}

                      const st = window.SeamlessApp.getState();
                      const partNames = st.parts.map(p => p.source_filename);
                      const songName = st.songMedia ? st.songMedia.name : '';
                      const exportStatus = document.getElementById('exportStatus').textContent;

                      // Also test manifest XSS numeric field coercion
                      window.__manifest_xss = undefined;
                      await window.SeamlessApp.ingestManifestAndCsv({{
                        fade_ms: 15,
                        parts: [{{
                          part_num: '<img src=x onerror="window.__manifest_xss=1">',
                          filename: 'Part_01_test.mp4',
                          source_filename: '01_test.mp4',
                          duration: 1.0,
                          song_start: 0,
                          song_end: 1.0,
                          verify_start: 0,
                          verify_end: 1.0,
                          width: '<img src=x onerror="window.__manifest_xss=2">',
                          height: 1920
                        }}]
                      }}, '');
                      await new Promise(r => setTimeout(r, 80));

                      return {{
                        partNames,
                        songName,
                        exportStatus,
                        manifestXssOk: window.__manifest_xss === undefined
                      }};
                    }})()""",
                )
                logs = cdp.get_logs(sid)
                self.assertEqual(len(res["partNames"]), 3, f"Duplicate basenames were collapsed: {res['partNames']}")
                self.assertEqual(len(set(res["partNames"])), 3)
                self.assertEqual(res["songName"], "valid_song.wav", "0-byte audio must not overwrite valid songMedia")
                self.assertIn("empty.wav", res["exportStatus"])
                self.assertTrue(res["manifestXssOk"])
                self.assertEqual(logs["exceptions"], [])
                self.assertEqual(logs["consoleErrors"], [])
            finally:
                cdp.close_tab(sid)

    def test_t5_studio_html_fallback_xss_escaped_in_chromium(self) -> None:
        cdp = self._require_cdp()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            shutil.copy2(AGENT_PATH, tmp / "seamless_shorts_agent.py")
            ws = tmp / "shorts_workspace"
            ws.mkdir(parents=True)
            (ws / "manifest.json").write_text(
                json.dumps(
                    {
                        "fade_ms": 15,
                        "parts": [
                            {
                                "part_num": 1,
                                "filename": 'Part_01_<img src=x onerror="window.__fb_xss=1">.mp4',
                                "source_filename": "01_test.mp4",
                                "duration": 2.5,
                                "song_start": 0.0,
                                "song_end": 2.5,
                                "verify_start": 0.0,
                                "verify_end": 2.5,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            _, base_url, stop = start_agent_server(
                script_path=tmp / "seamless_shorts_agent.py",
                cwd=tmp,
                port_arg=0,
            )
            sid = cdp.new_tab(f"{base_url}/", wait_ms=450)
            try:
                res = cdp.eval(
                    sid,
                    """({
                      cards: document.querySelectorAll('#partsGrid .part-card').length,
                      cardText: document.getElementById('partsGrid').innerText,
                      xssOk: window.__fb_xss === undefined
                    })""",
                )
                logs = cdp.get_logs(sid)
                self.assertEqual(res["cards"], 1)
                self.assertIn("Part_01_<img", res["cardText"])
                self.assertTrue(res["xssOk"])
                self.assertEqual(logs["exceptions"], [])
                self.assertEqual(logs["consoleErrors"], [])
            finally:
                cdp.close_tab(sid)
                stop()

    def test_t5_verify_file_atomic_swap_no_mid_write_partial_exposure(self) -> None:
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            self.skipTest("ffmpeg required")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            src_dir = tmp / "shorts_workspace" / "1_source_shorts"
            src_dir.mkdir(parents=True)
            _make_h264_mp4(ffmpeg_bin, src_dir / "01_a.mp4", duration=2.0, fps=30, color="red")
            _make_h264_mp4(ffmpeg_bin, src_dir / "02_b.mp4", duration=2.0, fps=30, color="blue")
            song = tmp / "song.wav"
            song.write_bytes(make_wav_bytes(sample_rate=8000, duration_sec=6.0))

            _, base_url, stop = start_agent_server(cwd=tmp, port_arg=0)
            try:
                observations: list[tuple[bool, int, int]] = []
                stop_poll = threading.Event()

                def poll_verify() -> None:
                    while not stop_poll.is_set():
                        st_s, _, raw_s = http_request(f"{base_url}/api/state")
                        if st_s == 200:
                            s = json.loads(raw_s.decode("utf-8"))
                            v_exists = bool(s.get("verify_exists"))
                            st_v, _, raw_v = http_request(f"{base_url}/media/verify")
                            observations.append((v_exists, st_v, len(raw_v)))
                            if not s["job"]["running"] and s["job"]["progress"] == 100:
                                break
                        time.sleep(0.04)

                st, _, _ = http_request(
                    f"{base_url}/api/run",
                    method="POST",
                    headers={"Content-Type": "application/json"},
                    data=json.dumps({"song_path": str(song), "skip_download": True, "fade_ms": 15}).encode("utf-8"),
                )
                self.assertEqual(st, 200)
                t = threading.Thread(target=poll_verify, daemon=True)
                t.start()
                t.join(timeout=30.0)
                stop_poll.set()

                # Never observed a 200 response with 0 bytes or partial < 1KB verify file
                for v_exists, st_v, v_len in observations:
                    if st_v == 200:
                        self.assertGreater(v_len, 1000, f"Exposed partial VERIFY_FILE mid-write: {(v_exists, st_v, v_len)}")
                    if v_exists:
                        self.assertEqual(st_v, 200)
                        self.assertGreater(v_len, 1000)
                self.assertTrue(any(st_v == 200 for _, st_v, _ in observations), "Expected final /media/verify 200")
            finally:
                stop()

    def test_t5_yt_dlp_arg_injection_blocked_over_api_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            _, base_url, stop = start_agent_server(cwd=tmp, port_arg=0)
            try:
                for bad_pl in ("--version", "--exec=id", "-U", "", "ftp://evil/list"):
                    st, hdrs, body = http_request(
                        f"{base_url}/api/run",
                        method="POST",
                        headers={"Content-Type": "application/json"},
                        data=json.dumps(
                            {"playlist_url": bad_pl, "skip_download": False, "max_downloads": 1}
                        ).encode("utf-8"),
                    )
                    self.assertEqual(st, 400, f"Expected 400 for playlist_url={bad_pl!r}, got {st}")
                    self.assertEqual(hdrs.get("access-control-allow-origin"), "*")
                    self.assertIn("error", json.loads(body.decode("utf-8")))
            finally:
                stop()

    def test_t5_mixed_fps_verify_zero_drift_and_mp3_truncation_no_shortfall(self) -> None:
        ffmpeg_bin = shutil.which("ffmpeg")
        ffprobe_bin = shutil.which("ffprobe")
        if not ffmpeg_bin or not ffprobe_bin:
            self.skipTest("ffmpeg and ffprobe required")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            src_dir = tmp / "shorts_workspace" / "1_source_shorts"
            src_dir.mkdir(parents=True)
            _make_h264_mp4(ffmpeg_bin, src_dir / "01_24fps.mp4", duration=1.750, fps=24, size="720x1280", color="red")
            _make_h264_mp4(ffmpeg_bin, src_dir / "02_30fps.mp4", duration=2.433, fps=30, size="1080x1920", color="blue")
            _make_h264_mp4(ffmpeg_bin, src_dir / "03_60fps.mp4", duration=1.950, fps=60, size="1080x2400", color="green")

            # Truncated 5.00s MP3 across 6.13s of clips -> Part 3 is truncated (~0.82s)
            song_mp3 = tmp / "song_5s.mp3"
            subprocess.run(
                [ffmpeg_bin, "-y", "-f", "lavfi", "-i", "sine=f=440:r=44100:d=5.0", "-c:a", "libmp3lame", "-b:a", "192k", str(song_mp3)],
                check=True,
                capture_output=True,
            )

            res = subprocess.run(
                [sys.executable, str(AGENT_PATH), "--cli", "--skip-download", "--fade-ms", "15", "--song", str(song_mp3)],
                cwd=str(tmp),
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

            manifest = json.loads((tmp / "shorts_workspace" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["parts"]), 3)
            planned_total = sum(p["duration"] for p in manifest["parts"])

            verify_mp4 = tmp / "shorts_workspace" / "3_verification_full_playthrough.mp4"
            v_probe = json.loads(
                subprocess.check_output(
                    [ffprobe_bin, "-v", "error", "-show_entries", "format=duration:stream=codec_type,duration", "-of", "json", str(verify_mp4)],
                    text=True,
                )
            )
            verify_dur = float(v_probe["format"]["duration"])
            self.assertAlmostEqual(verify_dur, planned_total, delta=0.05, msg=f"Verify drift too large: verify={verify_dur}, planned={planned_total}")

            # Check truncated Part 3 has no audio shortfall relative to video stream
            part3_mp4 = tmp / "shorts_workspace" / "2_repurposed_shorts" / manifest["parts"][2]["filename"]
            p3_probe = json.loads(
                subprocess.check_output(
                    [ffprobe_bin, "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(part3_mp4)],
                    text=True,
                )
            )
            a3 = next(float(s["duration"]) for s in p3_probe["streams"] if s["codec_type"] == "audio")
            planned_p3 = float(manifest["parts"][2]["duration"])
            self.assertGreaterEqual(a3, planned_p3 - 0.02, f"MP3 truncation audio shortfall: a={a3}, planned={planned_p3}")
            self.assertLess(abs(a3 - planned_p3), 0.04, f"MP3 truncation audio drift: a={a3}, planned={planned_p3}")

    def test_t5_play_pause_abort_race_does_not_latch_virtual_clock(self) -> None:
        cdp = self._require_cdp()
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            self.skipTest("ffmpeg required to generate playable H.264 clips")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            clip1 = tmp / "01_a.mp4"
            clip2 = tmp / "02_b.mp4"
            _make_h264_mp4(ffmpeg_bin, clip1, duration=2.0, fps=30, color="red")
            _make_h264_mp4(ffmpeg_bin, clip2, duration=2.0, fps=30, color="blue")
            c1_b64 = base64.b64encode(clip1.read_bytes()).decode("ascii")
            c2_b64 = base64.b64encode(clip2.read_bytes()).decode("ascii")
            wav_b64 = base64.b64encode(make_wav_bytes(sample_rate=8000, duration_sec=5.0)).decode("ascii")

            sid = cdp.new_tab((WEB_DIR / "Seamless_Shorts_Player.html").resolve().as_uri(), wait_ms=250)
            try:
                res = cdp.eval(
                    sid,
                    f"""(async () => {{
                      const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
                      const dt = new DataTransfer();
                      dt.items.add(new File([b64ToBytes({json.dumps(c1_b64)})], '01_a.mp4', {{ type: 'video/mp4' }}));
                      dt.items.add(new File([b64ToBytes({json.dumps(c2_b64)})], '02_b.mp4', {{ type: 'video/mp4' }}));
                      dt.items.add(new File([b64ToBytes({json.dumps(wav_b64)})], 'song.wav', {{ type: 'audio/wav' }}));
                      document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
                      for (let i = 0; i < 60; i++) {{
                        const st = window.SeamlessApp.getState();
                        const va = document.getElementById('videoA');
                        if (st.parts.length === 2 && st.songMedia?.audioBuffer && va.readyState >= 1) break;
                        await new Promise(r => setTimeout(r, 40));
                      }}

                      const btn = document.getElementById('playPauseBtn');
                      // Rapid Play -> Pause -> Play race to trigger AbortError on first v.play()
                      btn.click();
                      btn.click();
                      await new Promise(r => setTimeout(r, 60));
                      const afterPauseRace = {{
                        playing: window.SeamlessApp.getState().playing,
                        vclk: window.SeamlessApp.getState().virtualClockMode
                      }};

                      btn.click();
                      await new Promise(r => setTimeout(r, 320));
                      const st = window.SeamlessApp.getState();
                      const activeV = st.activeSlot === 'A' ? document.getElementById('videoA') : document.getElementById('videoB');
                      const afterResume = {{
                        playing: st.playing,
                        vclk: st.virtualClockMode,
                        vPaused: activeV.paused,
                        vTime: activeV.currentTime,
                        t: st.globalTime
                      }};
                      if (st.playing) btn.click();
                      return {{ afterPauseRace, afterResume }};
                    }})()""",
                )
                logs = cdp.get_logs(sid)
                self.assertFalse(res["afterPauseRace"]["playing"])
                self.assertFalse(res["afterPauseRace"]["vclk"], "AbortError latched virtualClockMode=true after Play->Pause")
                self.assertTrue(res["afterResume"]["playing"])
                self.assertFalse(res["afterResume"]["vclk"], "virtualClockMode must stay false on playable H.264 resume")
                self.assertFalse(res["afterResume"]["vPaused"], "Active video must not remain paused after Play->Pause->Play")
                self.assertGreater(res["afterResume"]["vTime"], 0.05)
                self.assertGreater(res["afterResume"]["t"], 0.05)
                self.assertEqual(logs["exceptions"], [])
                self.assertEqual(logs["consoleErrors"], [])
            finally:
                cdp.close_tab(sid)

    def test_t5_timeline_click_and_scrub_while_playing_resumes_video(self) -> None:
        cdp = self._require_cdp()
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            self.skipTest("ffmpeg required to generate playable H.264 clips")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            colors = ["red", "blue", "green"]
            clips_b64 = []
            for idx, col in enumerate(colors, 1):
                p = tmp / f"0{idx}_{col}.mp4"
                _make_h264_mp4(ffmpeg_bin, p, duration=2.5, fps=30, color=col)
                clips_b64.append((p.name, base64.b64encode(p.read_bytes()).decode("ascii")))
            wav_b64 = base64.b64encode(make_wav_bytes(sample_rate=8000, duration_sec=8.5)).decode("ascii")

            sid = cdp.new_tab((WEB_DIR / "Seamless_Shorts_Player.html").resolve().as_uri(), wait_ms=250)
            try:
                cdp.set_viewport(sid, 1280, 900)
                rect = cdp.eval(
                    sid,
                    f"""(async () => {{
                      const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
                      const dt = new DataTransfer();
                      for (const [name, b64] of {json.dumps(clips_b64)}) {{
                        dt.items.add(new File([b64ToBytes(b64)], name, {{ type: 'video/mp4' }}));
                      }}
                      dt.items.add(new File([b64ToBytes({json.dumps(wav_b64)})], 'song.wav', {{ type: 'audio/wav' }}));
                      document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
                      for (let i = 0; i < 80; i++) {{
                        const st = window.SeamlessApp.getState();
                        const va = document.getElementById('videoA');
                        if (st.parts.length === 3 && st.songMedia?.audioBuffer && va.readyState >= 1) break;
                        await new Promise(r => setTimeout(r, 40));
                      }}
                      document.getElementById('waveformCanvas').scrollIntoView({{ block: 'center' }});
                      await new Promise(r => setTimeout(r, 60));
                      document.getElementById('playPauseBtn').click();
                      await new Promise(r => setTimeout(r, 150));
                      const r = document.getElementById('playheadOverlay').getBoundingClientRect();
                      const wr = document.getElementById('waveformCanvas').getBoundingClientRect();
                      return {{ left: r.left, width: r.width, y: wr.top + wr.height / 2 }};
                    }})()""",
                )

                # 1. Single click at 50% (t = 3.75s, inside Part 1) while playing
                click_x = rect["left"] + rect["width"] * 0.50
                click_y = rect["y"]
                cdp.dispatch_mouse(sid, "mousePressed", click_x, click_y)
                cdp.dispatch_mouse(sid, "mouseReleased", click_x, click_y)

                click_res = cdp.eval(
                    sid,
                    """(async () => {
                      await new Promise(r => setTimeout(r, 180));
                      const st1 = window.SeamlessApp.getState();
                      const v1 = st1.activeSlot === 'A' ? document.getElementById('videoA') : document.getElementById('videoB');
                      const s1 = st1.activeSlot === 'A' ? document.getElementById('videoB') : document.getElementById('videoA');
                      const t1 = st1.globalTime;
                      const vt1 = v1.currentTime;
                      await new Promise(r => setTimeout(r, 220));
                      const st2 = window.SeamlessApp.getState();
                      const v2 = st2.activeSlot === 'A' ? document.getElementById('videoA') : document.getElementById('videoB');
                      return {
                        playing: st2.playing,
                        vclk: st2.virtualClockMode,
                        idx: st2.activePartIdx,
                        vPaused: v2.paused,
                        standbyPaused: s1.paused,
                        t1,
                        t2: st2.globalTime,
                        vt1,
                        vt2: v2.currentTime
                      };
                    })()""",
                )
                self.assertTrue(click_res["playing"])
                self.assertFalse(click_res["vclk"])
                self.assertEqual(click_res["idx"], 1)
                self.assertFalse(click_res["vPaused"], "Timeline click while playing left active video paused")
                self.assertTrue(click_res["standbyPaused"], "Standby video must be paused after slot switch")
                self.assertGreater(click_res["t2"], click_res["t1"])
                self.assertGreater(click_res["vt2"], click_res["vt1"])

                # 2. Drag scrub from 45% to 80% (t = 6.0s, inside Part 2) while playing
                start_x = rect["left"] + rect["width"] * 0.45
                end_x = rect["left"] + rect["width"] * 0.80
                cdp.dispatch_mouse(sid, "mousePressed", start_x, click_y)
                cdp.dispatch_mouse(sid, "mouseMoved", end_x, click_y, buttons=1)
                cdp.dispatch_mouse(sid, "mouseReleased", end_x, click_y)

                drag_res = cdp.eval(
                    sid,
                    """(async () => {
                      await new Promise(r => setTimeout(r, 180));
                      const st1 = window.SeamlessApp.getState();
                      const t1 = st1.globalTime;
                      await new Promise(r => setTimeout(r, 220));
                      const st2 = window.SeamlessApp.getState();
                      const v2 = st2.activeSlot === 'A' ? document.getElementById('videoA') : document.getElementById('videoB');
                      const s2 = st2.activeSlot === 'A' ? document.getElementById('videoB') : document.getElementById('videoA');
                      const res = {
                        playing: st2.playing,
                        vclk: st2.virtualClockMode,
                        idx: st2.activePartIdx,
                        vPaused: v2.paused,
                        standbyPaused: s2.paused,
                        t1,
                        t2: st2.globalTime,
                        vt2: v2.currentTime
                      };
                      if (st2.playing) document.getElementById('playPauseBtn').click();
                      return res;
                    })()""",
                )
                logs = cdp.get_logs(sid)
                self.assertTrue(drag_res["playing"])
                self.assertFalse(drag_res["vclk"])
                self.assertEqual(drag_res["idx"], 2)
                self.assertFalse(drag_res["vPaused"], "Timeline scrub while playing left active video paused")
                self.assertTrue(drag_res["standbyPaused"])
                self.assertGreater(drag_res["t2"], drag_res["t1"])
                self.assertGreater(drag_res["vt2"], 0.5)
                self.assertEqual(logs["exceptions"], [])
                self.assertEqual(logs["consoleErrors"], [])
            finally:
                cdp.close_tab(sid)

    def test_t5_seek_into_codec_error_pauses_both_videos(self) -> None:
        cdp = self._require_cdp()
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            self.skipTest("ffmpeg required to generate playable H.264 clips")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            clip1 = tmp / "01_a.mp4"
            clip2 = tmp / "02_b.mp4"
            _make_h264_mp4(ffmpeg_bin, clip1, duration=2.0, fps=30, color="red")
            _make_h264_mp4(ffmpeg_bin, clip2, duration=2.0, fps=30, color="blue")
            c1_b64 = base64.b64encode(clip1.read_bytes()).decode("ascii")
            c2_b64 = base64.b64encode(clip2.read_bytes()).decode("ascii")
            wav_b64 = base64.b64encode(make_wav_bytes(sample_rate=8000, duration_sec=6.0)).decode("ascii")

            sid = cdp.new_tab((WEB_DIR / "Seamless_Shorts_Player.html").resolve().as_uri(), wait_ms=250)
            try:
                res = cdp.eval(
                    sid,
                    f"""(async () => {{
                      const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
                      const dt = new DataTransfer();
                      dt.items.add(new File([b64ToBytes({json.dumps(c1_b64)})], '01_a.mp4', {{ type: 'video/mp4' }}));
                      dt.items.add(new File([b64ToBytes({json.dumps(c2_b64)})], '02_b.mp4', {{ type: 'video/mp4' }}));
                      dt.items.add(new File([b64ToBytes({json.dumps(wav_b64)})], 'song.wav', {{ type: 'audio/wav' }}));
                      document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
                      for (let i = 0; i < 60; i++) {{
                        const st = window.SeamlessApp.getState();
                        const va = document.getElementById('videoA');
                        if (st.parts.length === 2 && st.songMedia?.audioBuffer && va.readyState >= 1) break;
                        await new Promise(r => setTimeout(r, 40));
                      }}

                      const st = window.SeamlessApp.getState();
                      st.parts.push({{
                        part_num: 3,
                        filename: 'Part_03_broken.mp4',
                        source_filename: '03_broken.mp4',
                        duration: 1.5,
                        song_start: 4.0,
                        song_end: 5.5,
                        verify_start: 4.0,
                        verify_end: 5.5,
                        codecError: true
                      }});
                      st.totalDuration = 5.5;

                      document.getElementById('playPauseBtn').click();
                      await new Promise(r => setTimeout(r, 150));
                      const beforeJumpPaused = document.getElementById('videoA').paused;

                      // Press 'L' (+5s) to jump directly from Part 0 into codecError Part 2
                      document.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'l', bubbles: true }}));
                      await new Promise(r => setTimeout(r, 180));
                      const afterJump = {{
                        beforeJumpPaused,
                        idx: st.activePartIdx,
                        vclk: st.virtualClockMode,
                        playing: st.playing,
                        vaPaused: document.getElementById('videoA').paused,
                        vbPaused: document.getElementById('videoB').paused,
                        t: st.globalTime
                      }};
                      if (st.playing) document.getElementById('playPauseBtn').click();
                      return afterJump;
                    }})()""",
                )
                logs = cdp.get_logs(sid)
                self.assertFalse(res["beforeJumpPaused"])
                self.assertEqual(res["idx"], 2)
                self.assertTrue(res["vclk"])
                self.assertTrue(res["playing"])
                self.assertTrue(res["vaPaused"], "videoA must be paused when jumping into codecError clip")
                self.assertTrue(res["vbPaused"], "videoB must be paused when jumping into codecError clip")
                self.assertGreaterEqual(res["t"], 4.0)
                self.assertEqual(logs["exceptions"], [])
                self.assertEqual(logs["consoleErrors"], [])
            finally:
                cdp.close_tab(sid)

    def test_t5_preloaded_corrupt_standby_enters_virtual_clock_and_recovers(self) -> None:
        cdp = self._require_cdp()
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            self.skipTest("ffmpeg required to generate playable H.264 clips")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            clip1 = tmp / "Part_01_red.mp4"
            clip3 = tmp / "Part_03_green.mp4"
            _make_h264_mp4(ffmpeg_bin, clip1, duration=1.2, fps=30, color="red")
            _make_h264_mp4(ffmpeg_bin, clip3, duration=1.2, fps=30, color="green")
            corrupt_bytes = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\xff" * 512

            manifest = {
                "fade_ms": 15,
                "song_name": "song.wav",
                "parts": [
                    {
                        "part_num": 1,
                        "filename": "Part_01_red.mp4",
                        "source_filename": "01_red.mp4",
                        "duration": 1.2,
                        "song_start": 0.0,
                        "song_end": 1.2,
                        "verify_start": 0.0,
                        "verify_end": 1.2,
                    },
                    {
                        "part_num": 2,
                        "filename": "Part_02_corrupt.mp4",
                        "source_filename": "02_corrupt.mp4",
                        "duration": 1.2,
                        "song_start": 1.2,
                        "song_end": 2.4,
                        "verify_start": 1.2,
                        "verify_end": 2.4,
                    },
                    {
                        "part_num": 3,
                        "filename": "Part_03_green.mp4",
                        "source_filename": "03_green.mp4",
                        "duration": 1.2,
                        "song_start": 2.4,
                        "song_end": 3.6,
                        "verify_start": 2.4,
                        "verify_end": 3.6,
                    },
                ],
            }

            c1_b64 = base64.b64encode(clip1.read_bytes()).decode("ascii")
            c2_b64 = base64.b64encode(corrupt_bytes).decode("ascii")
            c3_b64 = base64.b64encode(clip3.read_bytes()).decode("ascii")
            wav_b64 = base64.b64encode(make_wav_bytes(sample_rate=8000, duration_sec=4.2)).decode("ascii")
            manifest_json = json.dumps(manifest)

            sid = cdp.new_tab((WEB_DIR / "Seamless_Shorts_Player.html").resolve().as_uri(), wait_ms=250)
            try:
                res = cdp.eval(
                    sid,
                    f"""(async () => {{
                      const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
                      const manifestStr = {json.dumps(manifest_json)};
                      const c1 = b64ToBytes({json.dumps(c1_b64)});
                      const c2 = b64ToBytes({json.dumps(c2_b64)});
                      const c3 = b64ToBytes({json.dumps(c3_b64)});
                      const wav = b64ToBytes({json.dumps(wav_b64)});

                      const dropAll = async () => {{
                        const dt = new DataTransfer();
                        dt.items.add(new File([manifestStr], 'manifest.json', {{ type: 'application/json' }}));
                        dt.items.add(new File([c1], 'Part_01_red.mp4', {{ type: 'video/mp4' }}));
                        dt.items.add(new File([c2], 'Part_02_corrupt.mp4', {{ type: 'video/mp4' }}));
                        dt.items.add(new File([c3], 'Part_03_green.mp4', {{ type: 'video/mp4' }}));
                        dt.items.add(new File([wav], 'song.wav', {{ type: 'audio/wav' }}));
                        document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
                        for (let i = 0; i < 80; i++) {{
                          const st = window.SeamlessApp.getState();
                          const va = document.getElementById('videoA');
                          const vb = document.getElementById('videoB');
                          if (
                            st.parts.length === 3 &&
                            st.songMedia?.audioBuffer &&
                            va.readyState >= 1 &&
                            (vb.error !== null || st.parts[1]?.codecError === true)
                          ) {{
                            break;
                          }}
                          await new Promise(r => setTimeout(r, 40));
                        }}
                      }};

                      await dropAll();
                      const preloaded = {{
                        partsLen: window.SeamlessApp.getState().parts.length,
                        vaReady: document.getElementById('videoA').readyState,
                        vbErr: document.getElementById('videoB').error ? document.getElementById('videoB').error.code : null,
                        p1CodecErr: Boolean(window.SeamlessApp.getState().parts[1]?.codecError),
                        vclkBefore: window.SeamlessApp.getState().virtualClockMode,
                        noticeHiddenBefore: document.getElementById('codecNotice').hidden
                      }};

                      document.getElementById('playPauseBtn').click();

                      let inPart1 = null;
                      let inPart2 = null;
                      let completed = null;
                      const deadline = performance.now() + 7000;
                      while (performance.now() < deadline) {{
                        const st = window.SeamlessApp.getState();
                        const va = document.getElementById('videoA');
                        const vb = document.getElementById('videoB');
                        if (!inPart1 && st.activePartIdx === 1 && st.globalTime >= 1.25) {{
                          const t1 = st.globalTime;
                          const snap1 = {{
                            idx: st.activePartIdx,
                            playing: st.playing,
                            vclk: st.virtualClockMode,
                            noticeHidden: document.getElementById('codecNotice').hidden,
                            vaPaused: va.paused,
                            vbPaused: vb.paused,
                            t1
                          }};
                          await new Promise(r => setTimeout(r, 180));
                          const stAfter = window.SeamlessApp.getState();
                          inPart1 = {{ ...snap1, t2: stAfter.globalTime, idxAfter: stAfter.activePartIdx }};
                        }}
                        if (!inPart2 && st.activePartIdx === 2 && st.globalTime >= 2.48) {{
                          const activeV1 = st.activeSlot === 'A' ? va : vb;
                          const standbyV1 = st.activeSlot === 'A' ? vb : va;
                          const t1 = st.globalTime;
                          const vt1 = activeV1.currentTime;
                          const snap2 = {{
                            idx: st.activePartIdx,
                            playing: st.playing,
                            vclk: st.virtualClockMode,
                            activePaused: activeV1.paused,
                            standbyPaused: standbyV1.paused,
                            t1,
                            vt1
                          }};
                          await new Promise(r => setTimeout(r, 180));
                          const stAfter = window.SeamlessApp.getState();
                          const activeV2 = stAfter.activeSlot === 'A' ? va : vb;
                          inPart2 = {{ ...snap2, t2: stAfter.globalTime, vt2: activeV2.currentTime }};
                        }}
                        if (!st.playing && st.globalTime >= 3.55) {{
                          completed = {{
                            playing: st.playing,
                            t: st.globalTime,
                            vaPaused: va.paused,
                            vbPaused: vb.paused
                          }};
                          break;
                        }}
                        await new Promise(r => setTimeout(r, 40));
                      }}

                      // Second check: fresh drop -> direct click onto .clip-block[data-idx="1"] when preloaded corrupt
                      document.getElementById('clearWorkspaceBtn').click();
                      await dropAll();
                      const clip1Btn = document.querySelector('.clip-block[data-idx="1"]');
                      clip1Btn.click();
                      await new Promise(r => setTimeout(r, 140));
                      const stD1 = window.SeamlessApp.getState();
                      const direct1 = {{
                        playing: stD1.playing,
                        idx: stD1.activePartIdx,
                        vclk: stD1.virtualClockMode,
                        noticeHidden: document.getElementById('codecNotice').hidden,
                        vaPaused: document.getElementById('videoA').paused,
                        vbPaused: document.getElementById('videoB').paused,
                        t: stD1.globalTime
                      }};
                      await new Promise(r => setTimeout(r, 200));
                      const stD2 = window.SeamlessApp.getState();
                      const direct2 = {{
                        playing: stD2.playing,
                        vclk: stD2.virtualClockMode,
                        t: stD2.globalTime
                      }};
                      if (stD2.playing) document.getElementById('playPauseBtn').click();

                      return {{ preloaded, inPart1, inPart2, completed, direct1, direct2 }};
                    }})()""",
                )
                logs = cdp.get_logs(sid)

                self.assertEqual(res["preloaded"]["partsLen"], 3)
                self.assertGreaterEqual(res["preloaded"]["vaReady"], 1)
                self.assertIsNotNone(res["preloaded"]["vbErr"])
                self.assertTrue(res["preloaded"]["p1CodecErr"])
                self.assertFalse(res["preloaded"]["vclkBefore"])
                self.assertTrue(res["preloaded"]["noticeHiddenBefore"])

                self.assertIsNotNone(res["inPart1"], "Did not reach Part 1 across 1.2s boundary")
                self.assertTrue(res["inPart1"]["playing"])
                self.assertTrue(res["inPart1"]["vclk"])
                self.assertFalse(res["inPart1"]["noticeHidden"])
                self.assertTrue(res["inPart1"]["vaPaused"])
                self.assertTrue(res["inPart1"]["vbPaused"])
                self.assertGreater(res["inPart1"]["t2"], res["inPart1"]["t1"])

                self.assertIsNotNone(res["inPart2"], "Did not recover to Part 2 across 2.4s boundary")
                self.assertTrue(res["inPart2"]["playing"])
                self.assertFalse(res["inPart2"]["vclk"])
                self.assertFalse(res["inPart2"]["activePaused"])
                self.assertTrue(res["inPart2"]["standbyPaused"])
                self.assertGreater(res["inPart2"]["vt2"], res["inPart2"]["vt1"])
                self.assertGreater(res["inPart2"]["t2"], res["inPart2"]["t1"])

                self.assertIsNotNone(res["completed"], "Did not finish Part 2 at 3.6s")
                self.assertFalse(res["completed"]["playing"])
                self.assertGreaterEqual(res["completed"]["t"], 3.55)
                self.assertTrue(res["completed"]["vaPaused"])
                self.assertTrue(res["completed"]["vbPaused"])

                self.assertTrue(res["direct1"]["playing"])
                self.assertEqual(res["direct1"]["idx"], 1)
                self.assertTrue(res["direct1"]["vclk"])
                self.assertFalse(res["direct1"]["noticeHidden"])
                self.assertTrue(res["direct1"]["vaPaused"])
                self.assertTrue(res["direct1"]["vbPaused"])
                self.assertGreater(res["direct2"]["t"], res["direct1"]["t"])

                self.assertEqual(logs["exceptions"], [])
                self.assertEqual(logs["consoleErrors"], [])
            finally:
                cdp.close_tab(sid)


if __name__ == "__main__":
    unittest.main()
