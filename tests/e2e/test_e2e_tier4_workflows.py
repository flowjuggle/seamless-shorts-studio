#!/usr/bin/env python3
"""Tier 4 E2E tests: 8 Real-World Application Scenarios across F1–F15."""

from __future__ import annotations

import base64
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import wave
import zipfile
from pathlib import Path

import yaml

from tests.e2e import (
    AGENT_PATH,
    BAT_PATH,
    DEPLOY_DIR,
    README_PATH,
    ROOT_DIR,
    TOOLS_DIR,
    WEB_DIR,
    WORKSPACE_DIR,
    ZIP_PATH,
    ChromiumCDPBridge,
    http_request,
    make_wav_bytes,
    start_agent_server,
    start_static_server,
)


class TestRealWorldScenariosTier4(unittest.TestCase):
    """8 real-world end-to-end user scenarios."""

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

    # Scenario 1: Real FFmpeg --skip-download pipeline on temp-copied clips + sample_song slice
    def test_t4_scenario1_real_ffmpeg_pipeline_skip_download_and_truncation(self) -> None:
        ffmpeg_bin = shutil.which("ffmpeg")
        ffprobe_bin = shutil.which("ffprobe")
        if not ffmpeg_bin or not ffprobe_bin:
            self.skipTest("ffmpeg/ffprobe required for pipeline scenario")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            src_dir = tmp / "shorts_workspace" / "1_source_shorts"
            src_dir.mkdir(parents=True)
            # Sorted order: 04 (15.05s) + 05 (14.07s) = 29.12s + 09 (8.99s) = 38.11s total
            for clip_name in ("05_wIA-VYebvO4.mp4", "09_hjbE98E4pHc.mp4", "04_I8KIQyqBJ2E.mp4"):
                shutil.copy2(WORKSPACE_DIR / "1_source_shorts" / clip_name, src_dir / clip_name)

            # 32s slice of sample_song.mp3 so Part 1 (15.05s), Part 2 (14.07s), and Part 3 (~2.88s truncated) all run!
            song_path = tmp / "sample_song.mp3"
            subprocess.run(
                [ffmpeg_bin, "-y", "-i", str(ROOT_DIR / "sample_song.mp3"), "-t", "32", "-c", "copy", str(song_path)],
                check=True,
                capture_output=True,
            )

            res = subprocess.run(
                [
                    sys.executable,
                    str(AGENT_PATH),
                    "--cli",
                    "--skip-download",
                    "--fade-ms",
                    "15",
                    "--song",
                    str(song_path),
                ],
                cwd=str(tmp),
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(res.returncode, 0, f"Pipeline failed:\n{res.stdout}\n{res.stderr}")

            out_dir = tmp / "shorts_workspace" / "2_repurposed_shorts"
            parts_files = sorted(out_dir.glob("Part_*.mp4"))
            self.assertEqual(len(parts_files), 3)

            verify_mp4 = tmp / "shorts_workspace" / "3_verification_full_playthrough.mp4"
            self.assertTrue(verify_mp4.is_file())

            probe = subprocess.run(
                [
                    ffprobe_bin,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration:stream=codec_name,pix_fmt,width,height,codec_type",
                    "-of",
                    "json",
                    str(verify_mp4),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            pinfo = json.loads(probe.stdout)
            vstreams = [s for s in pinfo["streams"] if s["codec_type"] == "video"]
            astreams = [s for s in pinfo["streams"] if s["codec_type"] == "audio"]
            self.assertEqual(len(vstreams), 1)
            self.assertEqual(len(astreams), 1)
            self.assertEqual(vstreams[0]["codec_name"], "h264")
            self.assertEqual(vstreams[0]["pix_fmt"], "yuv420p")
            self.assertEqual((vstreams[0]["width"], vstreams[0]["height"]), (1080, 1920))
            self.assertEqual(astreams[0]["codec_name"], "aac")

            manifest = json.loads((tmp / "shorts_workspace" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["fade_ms"], 15)
            self.assertEqual(len(manifest["parts"]), 3)
            cursor = 0.0
            for idx, part in enumerate(manifest["parts"], 1):
                self.assertEqual(part["part_num"], idx)
                self.assertAlmostEqual(part["song_start"], cursor, delta=0.01)
                self.assertAlmostEqual(part["verify_start"], cursor, delta=0.01)
                cursor += part["duration"]
                self.assertAlmostEqual(part["song_end"], cursor, delta=0.01)
                self.assertAlmostEqual(part["verify_end"], cursor, delta=0.01)
            self.assertLess(manifest["parts"][2]["duration"], 5.0, "Part 3 should be truncated when 32s song ends")
            self.assertAlmostEqual(float(pinfo["format"]["duration"]), cursor, delta=0.6)

    # Scenario 2: Real headless Chromium Studio E2E against live workspace server
    def test_t4_scenario2_real_chromium_studio_workspace_and_cut_audition(self) -> None:
        cdp = self._require_cdp()
        _, base_url, stop = start_agent_server(cwd=ROOT_DIR, port_arg=0)
        sid = cdp.new_tab(f"{base_url}/", wait_ms=550)
        try:
            info = cdp.eval(
                sid,
                """(async () => {
                  const mode = document.getElementById('modeBadge').dataset.mode;
                  const clips = document.querySelectorAll('#clipsList .clip-row-card').length;
                  const blocks = document.querySelectorAll('#clipBlocksTrack .clip-block').length;
                  const cuts = document.querySelectorAll('#cutMarkersTrack .cut-marker').length;
                  const verifyEnabled = !document.getElementById('modeVerifyBtn').disabled;

                  document.getElementById('auditionCutBtn').click();
                  const tAudition1 = window.SeamlessApp.getState().globalTime;

                  document.querySelector('#cutMarkersTrack .cut-marker[data-idx="3"]').click();
                  const tAudition3 = window.SeamlessApp.getState().globalTime;
                  document.getElementById('prevCutBtn').click();
                  const tPrevCut = window.SeamlessApp.getState().globalTime;
                  if (window.SeamlessApp.getState().playing) document.getElementById('playPauseBtn').click();

                  document.getElementById('themeToggleBtn').click();
                  const lightTheme = document.documentElement.getAttribute('data-theme');
                  document.getElementById('themeToggleBtn').click();
                  const darkTheme = document.documentElement.getAttribute('data-theme');

                  return { mode, clips, blocks, cuts, verifyEnabled, tAudition1, tAudition3, tPrevCut, lightTheme, darkTheme };
                })()""",
            )
            logs = cdp.get_logs(sid)
            self.assertEqual(info["mode"], "studio")
            self.assertEqual(info["clips"], 9)
            self.assertEqual(info["blocks"], 9)
            self.assertEqual(info["cuts"], 8)
            self.assertTrue(info["verifyEnabled"])
            self.assertAlmostEqual(info["tAudition1"], 12.049, delta=0.35)
            self.assertAlmostEqual(info["tAudition3"], 87.143, delta=0.35)
            self.assertAlmostEqual(info["tPrevCut"], 72.096, delta=0.35)
            self.assertEqual(info["lightTheme"], "light")
            self.assertEqual(info["darkTheme"], "dark")
            self.assertEqual(logs["exceptions"], [])
            self.assertEqual(logs["consoleErrors"], [])
        finally:
            cdp.close_tab(sid)
            stop()

    # Scenario 3: Real headless Chromium Standalone file:// + .zip + CSV (_EF_O_bx_g4) + song swap + WAV ZIP verify
    def test_t4_scenario3_real_chromium_file_uri_zip_csv_song_swap_and_wav_zip(self) -> None:
        cdp = self._require_cdp()
        clip_b64 = base64.b64encode((WORKSPACE_DIR / "1_source_shorts" / "03__EF_O_bx_g4.mp4").read_bytes()).decode("ascii")
        clip2_b64 = base64.b64encode((WORKSPACE_DIR / "1_source_shorts" / "09_hjbE98E4pHc.mp4").read_bytes()).decode("ascii")
        wav_long_b64 = base64.b64encode(make_wav_bytes(sample_rate=8000, duration_sec=12.0, freq=440.0)).decode("ascii")
        wav_short_b64 = base64.b64encode(make_wav_bytes(sample_rate=8000, duration_sec=3.0, freq=523.25)).decode("ascii")
        csv_text = "\r\n".join(
            [
                "Content,Video title,Video publish time,Impressions click-through rate (%),Views,Average view duration,Average percentage viewed (%)",
                "Total,,,2.1,632683,0:00:20,39.69",
                '_EF_O_bx_g4,"Spider-Man learn 2 #flow Juggle @WSP","Jun 2, 2023",5.35,5685,0:00:21,35.41',
                'hjbE98E4pHc,"Making a Riff🎵W/Kosmos LED","May 1, 2024",2.10,2548,0:00:09,106.96',
            ]
        )

        sid = cdp.new_tab((WEB_DIR / "Seamless_Shorts_Player.html").resolve().as_uri(), wait_ms=250)
        try:
            res = cdp.eval(
                sid,
                f"""(async () => {{
                  const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
                  const z = new JSZip();
                  z.file('03__EF_O_bx_g4.mp4', b64ToBytes({json.dumps(clip_b64)}));
                  z.file('09_hjbE98E4pHc.mp4', b64ToBytes({json.dumps(clip2_b64)}));
                  z.file('long_song.wav', b64ToBytes({json.dumps(wav_long_b64)}));
                  z.file('youtube_stats.csv', {json.dumps(csv_text)});
                  const zipBlob = await z.generateAsync({{ type: 'blob' }});
                  const dt = new DataTransfer();
                  dt.items.add(new File([zipBlob], 'flowjuggle_clips.zip', {{ type: 'application/zip' }}));
                  document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
                  for (let i = 0; i < 80; i++) {{
                    const st = window.SeamlessApp.getState();
                    if (st.parts.length === 2 && st.songMedia?.audioBuffer && st.csvCount === 2) break;
                    await new Promise(r => setTimeout(r, 50));
                  }}
                  const initialListText = document.getElementById('clipsList').innerText;

                  const fSwap = new File([b64ToBytes({json.dumps(wav_short_b64)})], 'swap_3s.wav', {{ type: 'audio/wav' }});
                  const dt2 = new DataTransfer();
                  dt2.items.add(fSwap);
                  const inp = document.getElementById('songInput');
                  Object.defineProperty(inp, 'files', {{ value: dt2.files, configurable: true }});
                  inp.dispatchEvent(new Event('change'));
                  for (let i = 0; i < 50; i++) {{
                    const st = window.SeamlessApp.getState();
                    if (st.songMedia?.name === 'swap_3s.wav' && st.songMedia?.audioBuffer && st.parts.length === 1) break;
                    await new Promise(r => setTimeout(r, 40));
                  }}

                  let capturedZip = null;
                  const origCreate = URL.createObjectURL;
                  URL.createObjectURL = (blob) => {{ capturedZip = blob; return origCreate(blob); }};
                  document.getElementById('dlWavZipBtn').click();
                  for (let i = 0; i < 50; i++) {{
                    if (capturedZip && document.getElementById('exportStatus').textContent.startsWith('Exported')) break;
                    await new Promise(r => setTimeout(r, 40));
                  }}
                  URL.createObjectURL = origCreate;
                  const u8 = new Uint8Array(await capturedZip.arrayBuffer());
                  let bin = '';
                  for (let i = 0; i < u8.length; i++) bin += String.fromCharCode(u8[i]);

                  return {{
                    initialListText,
                    swappedParts: window.SeamlessApp.getState().parts.length,
                    swappedTitle: window.SeamlessApp.getState().parts[0]?.youtube_title || '',
                    zipB64: btoa(bin)
                  }};
                }})()""",
            )
            logs = cdp.get_logs(sid)
            self.assertIn("Spider-Man learn 2 #flow Juggle @WSP", res["initialListText"])
            self.assertIn("5,685 views", res["initialListText"])
            self.assertEqual(res["swappedParts"], 1)
            self.assertEqual(res["swappedTitle"], "Spider-Man learn 2 #flow Juggle @WSP")

            raw_zip = base64.b64decode(res["zipB64"])
            with zipfile.ZipFile(io.BytesIO(raw_zip), "r") as zf:
                self.assertIsNone(zf.testzip())
                man = json.loads(zf.read("manifest.json").decode("utf-8"))
                self.assertEqual(man["fade_ms"], 15)
                self.assertEqual(man["song_name"], "swap_3s.wav")
                wav_bytes = zf.read("audio_slices/Part_01_audio.wav")
                with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                    self.assertIn(wf.getframerate(), (8000, 44100, 48000))
                    dur = wf.getnframes() / wf.getframerate()
                    self.assertAlmostEqual(dur, 3.0, delta=0.1)
            self.assertEqual(logs["exceptions"], [])
            self.assertEqual(logs["consoleErrors"], [])
        finally:
            cdp.close_tab(sid)

    # Scenario 4: Real extracted YT_1m_to_full_song_1x.zip E2E (flat Studio server + file:// player)
    def test_t4_scenario4_real_extracted_distribution_zip_server_and_file_player(self) -> None:
        cdp = self._require_cdp()
        with tempfile.TemporaryDirectory() as tmpdir:
            ext = Path(tmpdir)
            with zipfile.ZipFile(ZIP_PATH, "r") as zf:
                zf.extractall(ext)
            src_dir = ext / "shorts_workspace" / "1_source_shorts"
            src_dir.mkdir(parents=True)
            shutil.copy2(WORKSPACE_DIR / "1_source_shorts" / "05_wIA-VYebvO4.mp4", src_dir / "05_wIA-VYebvO4.mp4")
            (ext / "demo.wav").write_bytes(make_wav_bytes(duration_sec=2.0))

            _, base_url, stop = start_agent_server(
                script_path=ext / "seamless_shorts_agent.py",
                cwd=ext,
                port_arg=0,
            )
            sid_srv = cdp.new_tab(f"{base_url}/", wait_ms=450)
            sid_file = ""
            try:
                srv_info = cdp.eval(
                    sid_srv,
                    """({
                      mode: document.getElementById('modeBadge').dataset.mode,
                      studioHidden: document.getElementById('studioPanel').hidden,
                      pills: document.getElementById('studioToolPills').innerText
                    })""",
                )
                srv_logs = cdp.get_logs(sid_srv)
                self.assertEqual(srv_info["mode"], "studio")
                self.assertFalse(srv_info["studioHidden"])
                self.assertIn("Sources: 1", srv_info["pills"])
                self.assertEqual(srv_logs["exceptions"], [])
                self.assertEqual(srv_logs["consoleErrors"], [])

                sid_file = cdp.new_tab((ext / "Seamless_Shorts_Player.html").resolve().as_uri(), wait_ms=250)
                file_info = cdp.eval(
                    sid_file,
                    """({
                      mode: document.getElementById('modeBadge').dataset.mode,
                      jszip: typeof JSZip,
                      studioHidden: document.getElementById('studioPanel').hidden
                    })""",
                )
                file_logs = cdp.get_logs(sid_file)
                self.assertEqual(file_info["mode"], "standalone")
                self.assertEqual(file_info["jszip"], "function")
                self.assertTrue(file_info["studioHidden"])
                self.assertEqual(file_logs["exceptions"], [])
                self.assertEqual(file_logs["consoleErrors"], [])
            finally:
                cdp.close_tab(sid_srv)
                if sid_file:
                    cdp.close_tab(sid_file)
                stop()

    # Scenario 5: Real static site (tools/build_pages.sh -> SimpleHTTPRequestHandler) in Chromium
    def test_t4_scenario5_real_static_pages_site_via_simple_http_in_chromium(self) -> None:
        cdp = self._require_cdp()
        with tempfile.TemporaryDirectory() as tmpdir:
            site = Path(tmpdir) / "site"
            subprocess.run(["bash", str(TOOLS_DIR / "build_pages.sh"), str(site)], cwd=str(ROOT_DIR), check=True, capture_output=True)
            _, url, stop = start_static_server(site)
            try:
                for page in ("/", "/Seamless_Shorts_Player.html"):
                    sid = cdp.new_tab(f"{url}{page}", wait_ms=350)
                    try:
                        info = cdp.eval(
                            sid,
                            """({
                              mode: document.getElementById('modeBadge').dataset.mode,
                              studioHidden: document.getElementById('studioPanel').hidden,
                              jszip: typeof JSZip
                            })""",
                        )
                        logs = cdp.get_logs(sid)
                        self.assertEqual(info["mode"], "standalone")
                        self.assertTrue(info["studioHidden"])
                        self.assertEqual(info["jszip"], "function")
                        self.assertEqual(logs["exceptions"], [])
                        self.assertEqual(logs["consoleErrors"], [])
                    finally:
                        cdp.close_tab(sid)
            finally:
                stop()

    # Scenario 6: Cross-origin CORS + Range streaming E2E in real Chromium fetch()
    def test_t4_scenario6_cross_origin_cors_and_range_streaming_e2e(self) -> None:
        cdp = self._require_cdp()
        _, studio_url, stop_studio = start_agent_server(cwd=ROOT_DIR, port_arg=0)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "host.html").write_text("<!doctype html><html><body>CORS Origin B</body></html>", encoding="utf-8")
            _, host_url, stop_host = start_static_server(Path(tmpdir))
            sid = cdp.new_tab(f"{host_url}/host.html", wait_ms=200)
            try:
                res = cdp.eval(
                    sid,
                    f"""(async () => {{
                      const api = {json.dumps(studio_url)};
                      const hResp = await fetch(api + '/api/health');
                      const health = await hResp.json();
                      const sResp = await fetch(api + '/api/state');
                      const state = await sResp.json();
                      const vResp = await fetch(api + '/media/verify', {{ headers: {{ Range: 'bytes=0-1023' }} }});
                      const vBytes = new Uint8Array(await vResp.arrayBuffer());
                      const aResp = await fetch(api + '/media/audio/full_song.mp3', {{ headers: {{ Range: 'bytes=0-511' }} }});
                      const aBytes = new Uint8Array(await aResp.arrayBuffer());
                      return {{
                        healthOk: health.ok,
                        sourceCount: state.source_count,
                        vStatus: vResp.status,
                        vContentRange: vResp.headers.get('Content-Range'),
                        vAcceptRanges: vResp.headers.get('Accept-Ranges'),
                        vContentLength: vResp.headers.get('Content-Length'),
                        vLen: vBytes.length,
                        aStatus: aResp.status,
                        aContentRange: aResp.headers.get('Content-Range'),
                        aLen: aBytes.length
                      }};
                    }})()""",
                )
                logs = cdp.get_logs(sid)
                self.assertTrue(res["healthOk"])
                self.assertEqual(res["sourceCount"], 9)
                self.assertEqual(res["vStatus"], 206)
                self.assertIsNotNone(res["vContentRange"])
                self.assertTrue(res["vContentRange"].startswith("bytes 0-1023/"))
                self.assertEqual(res["vAcceptRanges"], "bytes")
                self.assertEqual(res["vContentLength"], "1024")
                self.assertEqual(res["vLen"], 1024)
                self.assertEqual(res["aStatus"], 206)
                self.assertEqual(res["aLen"], 512)
                self.assertEqual(logs["exceptions"], [])
                self.assertEqual(logs["consoleErrors"], [])
            finally:
                cdp.close_tab(sid)
                stop_host()
                stop_studio()

    # Scenario 7: Mixed-resolution / mixed-codec UI resilience & codec error fallback
    def test_t4_scenario7_mixed_codec_and_aspect_ratio_resilience_in_chromium(self) -> None:
        cdp = self._require_cdp()
        clip_720 = base64.b64encode((WORKSPACE_DIR / "1_source_shorts" / "05_wIA-VYebvO4.mp4").read_bytes()).decode("ascii")
        clip_2400 = base64.b64encode((WORKSPACE_DIR / "1_source_shorts" / "09_hjbE98E4pHc.mp4").read_bytes()).decode("ascii")
        manifest_mixed = (WORKSPACE_DIR / "manifest.json").read_text(encoding="utf-8")

        sid = cdp.new_tab((WEB_DIR / "Seamless_Shorts_Player.html").resolve().as_uri(), wait_ms=250)
        try:
            res = cdp.eval(
                sid,
                f"""(async () => {{
                  const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
                  const dt = new DataTransfer();
                  dt.items.add(new File([b64ToBytes({json.dumps(clip_720)})], '05_wIA-VYebvO4.mp4', {{ type: 'video/mp4' }}));
                  dt.items.add(new File([b64ToBytes({json.dumps(clip_2400)})], '09_hjbE98E4pHc.mp4', {{ type: 'video/mp4' }}));
                  document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
                  for (let i = 0; i < 60; i++) {{
                    if (window.SeamlessApp.getState().parts.length === 2) break;
                    await new Promise(r => setTimeout(r, 50));
                  }}
                  const dims = window.SeamlessApp.getState().parts.map(p => [p.width, p.height]);
                  const objFit = getComputedStyle(document.getElementById('videoA')).objectFit;

                  // Clear standalone clips then load full mixed manifest (AV1 + VP9) and simulate unplayable codec error
                  document.getElementById('clearWorkspaceBtn').click();
                  await window.SeamlessApp.ingestManifestAndCsv({manifest_mixed}, '');
                  const cardsText = document.getElementById('clipsList').innerText;
                  const v = document.getElementById('videoA');
                  v.setAttribute('src', 'blob:unplayable-av1');
                  v.dispatchEvent(new Event('error'));
                  const noticeVisible = !document.getElementById('codecNotice').hidden;
                  const vclock = window.SeamlessApp.getState().virtualClockMode;
                  document.querySelector('#cutMarkersTrack .cut-marker[data-idx="5"]').click();
                  const jumpedTime = window.SeamlessApp.getState().globalTime;
                  if (window.SeamlessApp.getState().playing) document.getElementById('playPauseBtn').click();

                  return {{
                    dims, objFit,
                    hasAv1Badge: cardsText.includes('AV1'),
                    hasVp9Badge: cardsText.includes('VP9'),
                    has2400Badge: cardsText.includes('1080×2400'),
                    noticeVisible, vclock, jumpedTime
                  }};
                }})()""",
            )
            logs = cdp.get_logs(sid)
            self.assertEqual(res["dims"], [[720, 1280], [1080, 2400]])
            self.assertEqual(res["objFit"], "contain")
            self.assertTrue(res["hasAv1Badge"])
            self.assertTrue(res["hasVp9Badge"])
            self.assertTrue(res["has2400Badge"])
            self.assertTrue(res["noticeVisible"])
            self.assertTrue(res["vclock"])
            self.assertGreater(res["jumpedTime"], 150.0)
            self.assertEqual(logs["exceptions"], [])
            self.assertEqual(logs["consoleErrors"], [])
        finally:
            cdp.close_tab(sid)

    # Scenario 8: Non-technical Windows & Cloud readiness end-to-end consistency
    def test_t4_scenario8_windows_and_cloud_readiness_end_to_end_consistency(self) -> None:
        # 1. Windows bundle + launcher + README consistency
        bat_bytes = BAT_PATH.read_bytes()
        self.assertEqual(bat_bytes.count(b"\r\n"), bat_bytes.count(b"\n"))
        bat_txt = bat_bytes.decode("utf-8")
        self.assertIsNone(re.search(r"\bpython3(?:\.exe)?\b", bat_txt, re.IGNORECASE))
        readme_txt = README_PATH.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\bpython3\b", readme_txt, re.IGNORECASE))
        self.assertIsNone(re.search(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0)", readme_txt, re.IGNORECASE))
        for item in ("Extract All", "Seamless_Shorts_Player.html", "START_HERE.bat", "SmartScreen"):
            self.assertIn(item, readme_txt)

        # 2. Cloud / Container / Static Hosting port & health contract consistency
        df_txt = (ROOT_DIR / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("PORT=8766", df_txt)
        self.assertIn("/api/health", df_txt)
        dc = yaml.safe_load((ROOT_DIR / "docker-compose.yml").read_text(encoding="utf-8"))
        svc = next(iter(dc["services"].values()))
        self.assertEqual(str(svc["environment"]["PORT"]), "8766")
        render = yaml.safe_load((DEPLOY_DIR / "render.yaml").read_text(encoding="utf-8"))["services"][0]
        self.assertEqual(render["healthCheckPath"], "/api/health")
        fly = tomllib.loads((DEPLOY_DIR / "fly.toml").read_text(encoding="utf-8"))
        self.assertEqual(fly["http_service"]["internal_port"], 8766)
        self.assertEqual(fly["http_service"]["checks"][0]["path"], "/api/health")
        hf = (DEPLOY_DIR / "huggingface" / "README.md").read_text(encoding="utf-8")
        self.assertIn("app_port: 8766", hf)
        pages_wf = yaml.safe_load((ROOT_DIR / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8"))
        self.assertIn("build", pages_wf["jobs"])
        self.assertIn("deploy", pages_wf["jobs"])
        hosting_md = (DEPLOY_DIR / "HOSTING.md").read_text(encoding="utf-8")
        gh_md = (DEPLOY_DIR / "GITHUB_PAGES.md").read_text(encoding="utf-8")
        self.assertIn("explicit consent", gh_md.lower())
        self.assertIsNone(re.search(r"http://localhost:8766", hosting_md, re.IGNORECASE))

    # Scenario 9: Real 3-clip H.264 Clips A/B sequential playback without Clip 2 infinite loop
    def test_t4_scenario9_clips_ab_3clip_sequential_playback_no_loop(self) -> None:
        cdp = self._require_cdp()
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            self.skipTest("ffmpeg required to generate synthetic H.264 clips")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            clip_b64s: list[tuple[str, str]] = []
            for idx, color in enumerate(("red", "blue", "green"), 1):
                mp4_path = tmp / f"0{idx}_clip.mp4"
                subprocess.run(
                    [
                        ffmpeg_bin,
                        "-y",
                        "-f",
                        "lavfi",
                        "-i",
                        f"color=c={color}:s=360x640:r=30:d=1.2",
                        "-f",
                        "lavfi",
                        "-i",
                        "anullsrc=r=44100:cl=mono",
                        "-t",
                        "1.2",
                        "-c:v",
                        "libx264",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        str(mp4_path),
                    ],
                    check=True,
                    capture_output=True,
                )
                clip_b64s.append((mp4_path.name, base64.b64encode(mp4_path.read_bytes()).decode("ascii")))

            wav_b64 = base64.b64encode(make_wav_bytes(sample_rate=8000, duration_sec=4.5, freq=440.0)).decode("ascii")
            sid = cdp.new_tab((WEB_DIR / "Seamless_Shorts_Player.html").resolve().as_uri(), wait_ms=250)
            try:
                res = cdp.eval(
                    sid,
                    f"""(async () => {{
                      const b64ToBytes = (b) => Uint8Array.from(atob(b), c => c.charCodeAt(0));
                      const clips = {json.dumps(clip_b64s)};
                      const dt = new DataTransfer();
                      for (const [name, b64] of clips) {{
                        dt.items.add(new File([b64ToBytes(b64)], name, {{ type: 'video/mp4' }}));
                      }}
                      dt.items.add(new File([b64ToBytes({json.dumps(wav_b64)})], 'song.wav', {{ type: 'audio/wav' }}));
                      document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
                      for (let i = 0; i < 80; i++) {{
                        const st = window.SeamlessApp.getState();
                        if (st.parts.length === 3 && st.songMedia?.audioBuffer) break;
                        await new Promise(r => setTimeout(r, 50));
                      }}

                      document.getElementById('playPauseBtn').click();
                      const samples = [];
                      for (let i = 0; i < 65; i++) {{
                        await new Promise(r => setTimeout(r, 90));
                        const st = window.SeamlessApp.getState();
                        samples.push({{
                          t: Number(st.globalTime.toFixed(3)),
                          idx: st.activePartIdx,
                          slot: st.activeSlot,
                          playing: st.playing,
                          vclk: st.virtualClockMode
                        }});
                        if (!st.playing && i > 10) break;
                      }}
                      const finalState = window.SeamlessApp.getState();
                      return {{
                        partsCount: finalState.parts.length,
                        totalDuration: finalState.totalDuration,
                        finalPlaying: finalState.playing,
                        finalTime: finalState.globalTime,
                        finalIdx: finalState.activePartIdx,
                        samples
                      }};
                    }})()""",
                )
                logs = cdp.get_logs(sid)
                self.assertEqual(res["partsCount"], 3)
                seen_idxs = [s["idx"] for s in res["samples"]]
                self.assertIn(0, seen_idxs, f"Never observed Part 0: {res['samples']}")
                self.assertIn(1, seen_idxs, f"Never observed Part 1: {res['samples']}")
                self.assertIn(2, seen_idxs, f"Never observed Part 2 (stuck in Clip 2 loop?): {res['samples']}")
                # Ensure activePartIdx never regressed backwards (no 1 -> 0 reset)
                for prev_i, cur_i in zip(seen_idxs, seen_idxs[1:]):
                    self.assertGreaterEqual(cur_i, prev_i, f"Part index regressed in samples: {res['samples']}")
                self.assertFalse(res["finalPlaying"], f"Playback did not pause at completion: {res}")
                self.assertGreaterEqual(res["finalTime"], res["totalDuration"] - 0.15)
                self.assertEqual(res["finalIdx"], 2)
                self.assertEqual(logs["exceptions"], [])
                self.assertEqual(logs["consoleErrors"], [])
            finally:
                cdp.close_tab(sid)


if __name__ == "__main__":
    unittest.main()
