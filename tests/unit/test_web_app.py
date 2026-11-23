#!/usr/bin/env python3
"""Unit and live headless Chromium CDP tests for Milestone M1 web app & standalone builder."""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import re
import shutil
import socketserver
import struct
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
WEB_DIR = ROOT_DIR / "web"
TOOLS_DIR = ROOT_DIR / "tools"
JSZIP_SHA256 = "acc7e41455a80765b5fd9c7ee1b8078a6d160bbbca455aeae854de65c947d59e"


def clean_local_env() -> dict[str, str]:
    env = os.environ.copy()
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(k, None)
    return env


def make_tiny_wav_bytes(sample_rate: int = 8000, duration_sec: float = 0.5) -> bytes:
    num_samples = int(sample_rate * duration_sec)
    data_size = num_samples * 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        data_size,
    )
    samples = bytearray(data_size)
    for i in range(num_samples):
        val = int(12000 * (1 if (i // 20) % 2 == 0 else -1))
        struct.pack_into("<h", samples, i * 2, val)
    return header + bytes(samples)


class TestWebAppDeliverables(unittest.TestCase):
    def test_deliverables_exist_and_jszip_integrity(self) -> None:
        required = [
            WEB_DIR / "index.html",
            WEB_DIR / "styles.css",
            WEB_DIR / "app.js",
            WEB_DIR / "jszip.min.js",
            WEB_DIR / "Seamless_Shorts_Player.html",
            TOOLS_DIR / "build_standalone.py",
        ]
        for p in required:
            self.assertTrue(p.is_file(), f"Missing required deliverable: {p}")
            self.assertGreater(p.stat().st_size, 0, f"Empty deliverable: {p}")

        jszip_bytes = (WEB_DIR / "jszip.min.js").read_bytes()
        digest = hashlib.sha256(jszip_bytes).hexdigest()
        self.assertEqual(digest, JSZIP_SHA256)
        header_text = jszip_bytes[:400].decode("utf-8", errors="replace")
        self.assertIn("JSZip", header_text)
        self.assertIn("MIT", header_text)

    def test_offline_safety_no_external_cdn_and_no_es_modules(self) -> None:
        index_html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        standalone_html = (WEB_DIR / "Seamless_Shorts_Player.html").read_text(encoding="utf-8")
        app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

        for label, html in [("index.html", index_html), ("Seamless_Shorts_Player.html", standalone_html)]:
            self.assertNotRegex(
                html,
                r'<script\b[^>]*\btype\s*=\s*["\']module["\']',
                f"{label} must not use ES module <script type='module'>",
            )
            self.assertNotRegex(
                html,
                r'<(?:script|link)\b[^>]*(?:src|href)\s*=\s*["\']https?://',
                f"{label} must not contain external http(s) script/stylesheet URLs",
            )

        self.assertIn('<script src="jszip.min.js"></script>', index_html)
        self.assertIn('<script src="app.js"></script>', index_html)
        self.assertIn('<link rel="stylesheet" href="styles.css"', index_html)

        self.assertNotRegex(app_js, r'(?m)^\s*(?:import|export)\b')
        self.assertNotRegex(app_js, r'\bexport\s+(?:default|function|const|let|var|class|{)\b')

        node_bin = shutil.which("node")
        self.assertIsNotNone(node_bin, "node is required for syntax validation")
        chk = subprocess.run([node_bin, "--check", str(WEB_DIR / "app.js")], capture_output=True, text=True)
        self.assertEqual(chk.returncode, 0, f"node --check failed: {chk.stderr}")

    def test_build_standalone_script_and_escaping(self) -> None:
        import sys
        sys.path.insert(0, str(TOOLS_DIR))
        try:
            import build_standalone
            escaped = build_standalone.escape_script_body('var s = "</script><SCRIPT></SCRIPT>";')
            self.assertNotIn("</script", escaped.lower())
            self.assertIn("<\\/script", escaped)
        finally:
            sys.path.pop(0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_out = Path(tmpdir) / "built.html"
            res = subprocess.run(
                ["python3", str(TOOLS_DIR / "build_standalone.py"), "--output", str(tmp_out)],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, f"build_standalone.py failed: {res.stderr}")
            self.assertTrue(tmp_out.is_file())
            built_text = tmp_out.read_text(encoding="utf-8")
            committed_text = (WEB_DIR / "Seamless_Shorts_Player.html").read_text(encoding="utf-8")
            self.assertEqual(built_text, committed_text, "web/Seamless_Shorts_Player.html is out of date with web/* sources")

            scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", built_text, flags=re.DOTALL | re.IGNORECASE)
            self.assertEqual(len(scripts), 2, "Expected exactly 2 inline <script> blocks (JSZip + app.js)")
            node_bin = shutil.which("node")
            for idx, body in enumerate(scripts):
                js_tmp = Path(tmpdir) / f"script_{idx}.js"
                js_tmp.write_text(body, encoding="utf-8")
                chk = subprocess.run([node_bin, "--check", str(js_tmp)], capture_output=True, text=True)
                self.assertEqual(chk.returncode, 0, f"Inline script {idx} syntax error: {chk.stderr}")

    def test_node_core_csv_id_matching_plan_and_wav_dsp(self) -> None:
        node_bin = shutil.which("node")
        self.assertIsNotNone(node_bin)
        manifest_path = ROOT_DIR / "shorts_workspace" / "manifest.json"
        sheet_csv_path = Path("/tmp/flowjuggle_sheet.csv")

        node_script = r"""
const assert = require('assert');
const fs = require('fs');
const core = require(process.argv[1]);

// 1. YouTube ID extraction incl. double-underscore _EF_O_bx_g4
assert.strictEqual(core.extractYouTubeId('03__EF_O_bx_g4.mp4'), '_EF_O_bx_g4');
assert.strictEqual(core.extractYouTubeId('Part_02_03__EF_O_bx_g4.mp4'), '_EF_O_bx_g4');
assert.strictEqual(core.extractYouTubeId('02_19EAck3_MSg.mp4'), '19EAck3_MSg');

// 2. RFC 4180 CSV parser: CRLF, Total row skip, quoted comma, doubled quotes, emoji
const csvSample = [
  'Content,Video title,Video publish time,Impressions click-through rate (%),Views,Average view duration,Average percentage viewed (%)',
  'Total,,,2.1,632683,0:00:20,39.69',
  '_EF_O_bx_g4,"Spider-Man ""Flow"" 🕸️, NYC","Jun 2, 2023",5.35,5685,0:00:21,35.41',
  '19EAck3_MSg,Flowmies Try Props,"May 28, 2024",1.46,5901,0:00:14,103.59'
].join('\r\n');

const parsed = core.parseCsvRfc4180(csvSample);
assert.strictEqual(parsed.rows.length, 2, 'Total row must be skipped');
assert.strictEqual(parsed.byId['_EF_O_bx_g4'].title, 'Spider-Man "Flow" 🕸️, NYC');
assert.strictEqual(parsed.byId['_EF_O_bx_g4'].views, 5685);
const matched = core.matchClipMetadata('03__EF_O_bx_g4.mp4', parsed.byId);
assert.ok(matched && matched.id === '_EF_O_bx_g4');

// 3. Optional full workspace manifest + exported sheet check if present
const manifestFile = process.argv[2];
const sheetFile = process.argv[3];
if (fs.existsSync(manifestFile) && fs.existsSync(sheetFile)) {
  const manifest = JSON.parse(fs.readFileSync(manifestFile, 'utf8'));
  const sheet = core.parseCsvRfc4180(fs.readFileSync(sheetFile, 'utf8'));
  assert.strictEqual(manifest.parts.length, 9);
  for (const part of manifest.parts) {
    const m = core.matchClipMetadata(part.source_filename, sheet.byId);
    assert.ok(m && m.title.length > 0, 'Missing sheet match for ' + part.source_filename);
  }
}

// 4. Sequential offset & song-end truncation math
const clips = [
  { filename: '02_b.mp4', rawDuration: 12.0 },
  { filename: '01_a.mp4', rawDuration: 10.0 },
  { filename: '03_c.mp4', rawDuration: 20.0 }
];
const plan = core.computeSequentialPlan(clips, 25.0, 15);
assert.strictEqual(plan.fade_ms, 15);
assert.strictEqual(plan.parts.length, 3);
assert.strictEqual(plan.parts[0].source_filename, '01_a.mp4');
assert.strictEqual(plan.parts[0].song_start, 0);
assert.strictEqual(plan.parts[0].song_end, 10);
assert.strictEqual(plan.parts[1].source_filename, '02_b.mp4');
assert.strictEqual(plan.parts[1].song_start, 10);
assert.strictEqual(plan.parts[1].song_end, 22);
assert.strictEqual(plan.parts[2].duration, 3);
assert.strictEqual(plan.parts[2].truncated, true);
assert.strictEqual(plan.parts[2].song_start, 22);
assert.strictEqual(plan.parts[2].song_end, 25);

// 5. Studio state normalization for both legacy & alias keys
const sLegacy = core.normalizeStudioState({
  playlist_url: 'https://yt/legacy',
  active_song_path: 'full_song.mp3',
  parts: [{ part_num: 1 }],
  verify_exists: true
});
assert.strictEqual(sLegacy.playlistUrl, 'https://yt/legacy');
assert.strictEqual(sLegacy.activeSongPath, 'full_song.mp3');
assert.strictEqual(sLegacy.verifyExists, true);

const sAlias = core.normalizeStudioState({
  active_playlist: 'https://yt/alias',
  active_song: 'custom.wav',
  manifest: { fade_ms: 20, parts: [{ part_num: 1 }, { part_num: 2 }] },
  has_verify: false,
  sources: ['01.mp4', '02.mp4']
});
assert.strictEqual(sAlias.playlistUrl, 'https://yt/alias');
assert.strictEqual(sAlias.activeSongPath, 'custom.wav');
assert.strictEqual(sAlias.parts.length, 2);
assert.strictEqual(sAlias.sourceCount, 2);
assert.strictEqual(sAlias.verifyExists, false);
assert.strictEqual(sAlias.fadeMs, 20);

// 6. WAV slice with 15 ms micro-fade & RIFF PCM16 encoder
const sr = 10000;
const ch = new Float32Array(sr * 2).fill(0.5);
const sliced = core.sliceAudioChannelsWithFade([ch], sr, 0.5, 1.5, 15);
assert.strictEqual(sliced[0].length, 10000);
assert.strictEqual(sliced[0][0], 0, 'First sample must fade in from 0');
assert.ok(Math.abs(sliced[0][5000] - 0.5) < 1e-6, 'Middle sample must keep full amplitude');
assert.strictEqual(sliced[0][9999], 0, 'Last sample must fade out to 0');
const wavBytes = core.encodeWavFromChannels(sliced, sr);
assert.strictEqual(Buffer.from(wavBytes.slice(0, 4)).toString('ascii'), 'RIFF');
assert.strictEqual(Buffer.from(wavBytes.slice(8, 12)).toString('ascii'), 'WAVE');
assert.strictEqual(wavBytes.length, 44 + 10000 * 2);

console.log('NODE_CORE_TESTS_OK');
"""
        res = subprocess.run(
            [node_bin, "-e", node_script, str(WEB_DIR / "app.js"), str(manifest_path), str(sheet_csv_path)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"Node core test failed:\nSTDOUT:{res.stdout}\nSTDERR:{res.stderr}")
        self.assertIn("NODE_CORE_TESTS_OK", res.stdout)


class TestHeadlessChromiumCDP(unittest.TestCase):
    def test_standalone_and_studio_ui_in_chromium(self) -> None:
        chromium_bin = shutil.which("chromium") or shutil.which("google-chrome")
        node_bin = shutil.which("node")
        if not chromium_bin or not node_bin:
            self.skipTest("chromium or node not available for headless CDP verification")

        wav_bytes = make_tiny_wav_bytes()

        class MockStudioHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path in ("/", "/index.html"):
                    data = (WEB_DIR / "index.html").read_bytes()
                    ctype = "text/html; charset=utf-8"
                elif path == "/styles.css":
                    data = (WEB_DIR / "styles.css").read_bytes()
                    ctype = "text/css; charset=utf-8"
                elif path == "/app.js":
                    data = (WEB_DIR / "app.js").read_bytes()
                    ctype = "application/javascript; charset=utf-8"
                elif path == "/jszip.min.js":
                    data = (WEB_DIR / "jszip.min.js").read_bytes()
                    ctype = "application/javascript; charset=utf-8"
                elif path == "/api/health":
                    data = json.dumps({"ok": True, "app": "seamless-shorts-studio", "version": "test", "mode": "studio"}).encode()
                    ctype = "application/json"
                elif path == "/api/state":
                    payload = {
                        "tools": {"python": "3.11.2", "ffmpeg": True, "ffprobe": True, "yt_dlp": True},
                        "active_playlist": "https://www.youtube.com/playlist?list=TEST123",
                        "active_song": "test.wav",
                        "song_name": "test.wav",
                        "audio_files": [{"name": "test.wav", "path": "test.wav", "size_bytes": len(wav_bytes)}],
                        "sources": ["02_19EAck3_MSg.mp4", "03__EF_O_bx_g4.mp4"],
                        "source_count": 2,
                        "has_verify": False,
                        "fade_ms": 15,
                        "manifest": {
                            "fade_ms": 15,
                            "song_name": "test.wav",
                            "parts": [
                                {"part_num": 1, "filename": "Part_01_02_19EAck3_MSg.mp4", "source_filename": "02_19EAck3_MSg.mp4", "duration": 10.0, "song_start": 0.0, "song_end": 10.0, "verify_start": 0.0, "verify_end": 10.0, "codec": "av1", "width": 1080, "height": 1920},
                                {"part_num": 2, "filename": "Part_02_03__EF_O_bx_g4.mp4", "source_filename": "03__EF_O_bx_g4.mp4", "duration": 12.0, "song_start": 10.0, "song_end": 22.0, "verify_start": 10.0, "verify_end": 22.0, "codec": "av1", "width": 720, "height": 1280},
                            ],
                        },
                        "job": {"running": False, "step": "Ready", "progress": 0, "logs": ["Studio test ready."], "error": None},
                    }
                    data = json.dumps(payload).encode()
                    ctype = "application/json"
                elif path == "/media/audio/test.wav":
                    data = wav_bytes
                    ctype = "audio/wav"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return

                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        class ReusableTCPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
            allow_reuse_address = True

        server = ReusableTCPServer(("127.0.0.1", 0), MockStudioHandler)
        port = server.server_address[1]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        manifest_json = (ROOT_DIR / "shorts_workspace" / "manifest.json").read_text(encoding="utf-8")
        sample_csv = "\r\n".join([
            "Content,Video title,Video publish time,Impressions click-through rate (%),Views,Average view duration,Average percentage viewed (%)",
            "Total,,,2.1,632683,0:00:20,39.69",
            '19EAck3_MSg,"Flowmies Try Props","May 28, 2024",1.46,5901,0:00:14,103.59',
            '_EF_O_bx_g4,"Spider-Man learn 2 #flow Juggle @WSP","Jun 2, 2023",5.35,5685,0:00:21,35.41',
        ])

        node_cdp_driver = r"""
const assert = require('assert');
const { spawn } = require('child_process');

const chromiumBin = process.argv[1];
const userDataDir = process.argv[2];
const standaloneUrl = process.argv[3];
const studioUrl = process.argv[4];
const manifestJson = process.argv[5];
const sampleCsv = process.argv[6];

function startChromium() {
  return new Promise((resolve, reject) => {
    const proc = spawn(chromiumBin, [
      '--headless',
      '--no-sandbox',
      '--disable-gpu',
      '--disable-dev-shm-usage',
      '--remote-debugging-port=0',
      '--user-data-dir=' + userDataDir,
      'about:blank'
    ], { stdio: ['ignore', 'pipe', 'pipe'] });

    let stderr = '';
    const timer = setTimeout(() => reject(new Error('Chromium CDP timeout. stderr=' + stderr)), 15000);
    proc.stderr.on('data', (chunk) => {
      stderr += chunk.toString('utf8');
      const m = stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (m) {
        clearTimeout(timer);
        resolve({ proc, wsUrl: m[1] });
      }
    });
    proc.on('exit', (code) => {
      clearTimeout(timer);
      reject(new Error('Chromium exited prematurely with code ' + code + ' stderr=' + stderr));
    });
  });
}

class CDPClient {
  constructor(ws) {
    this.ws = ws;
    this.nextId = 1;
    this.pending = new Map();
    this.events = [];
    ws.addEventListener('message', (evt) => {
      const msg = JSON.parse(evt.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(JSON.stringify(msg.error)));
        else resolve(msg.result);
      } else if (msg.method) {
        this.events.push(msg);
      }
    });
  }
  send(method, params = {}, sessionId = undefined) {
    const id = this.nextId++;
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify(payload));
    });
  }
  waitForEvent(method, sessionId, timeoutMs = 10000) {
    const start = Date.now();
    return new Promise((resolve, reject) => {
      const check = () => {
        const idx = this.events.findIndex(e => e.method === method && (!sessionId || e.sessionId === sessionId));
        if (idx >= 0) {
          const ev = this.events.splice(idx, 1)[0];
          resolve(ev);
          return;
        }
        if (Date.now() - start > timeoutMs) {
          reject(new Error('Timed out waiting for event ' + method));
          return;
        }
        setTimeout(check, 25);
      };
      check();
    });
  }
  close() {
    try { this.ws.close(); } catch (_) {}
  }
}

async function run() {
  const { proc, wsUrl } = await startChromium();
  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const cdp = new CDPClient(ws);

  try {
    // 1. Test Standalone file:// URL
    const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
    await cdp.send('Page.enable', {}, sessionId);
    await cdp.send('Runtime.enable', {}, sessionId);

    const errors = [];
    const studioErrors = [];
    let studioSessId = null;
    const origPush = cdp.events.push.bind(cdp.events);
    cdp.events.push = (ev) => {
      if (ev.sessionId === sessionId) {
        if (ev.method === 'Runtime.exceptionThrown') errors.push('exception: ' + JSON.stringify(ev.params));
        if (ev.method === 'Runtime.consoleAPICalled' && ev.params.type === 'error') {
          errors.push('console.error: ' + JSON.stringify(ev.params.args));
        }
      } else if (studioSessId && ev.sessionId === studioSessId) {
        if (ev.method === 'Runtime.exceptionThrown') studioErrors.push('exception: ' + JSON.stringify(ev.params));
        if (ev.method === 'Runtime.consoleAPICalled' && ev.params.type === 'error') {
          studioErrors.push('console.error: ' + JSON.stringify(ev.params.args));
        }
      }
      return origPush(ev);
    };

    const loadP = cdp.waitForEvent('Page.loadEventFired', sessionId);
    await cdp.send('Page.navigate', { url: standaloneUrl }, sessionId);
    await loadP;
    await new Promise(r => setTimeout(r, 250));

    const evalExpr = async (expr) => {
      const res = await cdp.send('Runtime.evaluate', {
        expression: expr,
        awaitPromise: true,
        returnByValue: true
      }, sessionId);
      if (res.exceptionDetails) {
        throw new Error('Eval exception: ' + JSON.stringify(res.exceptionDetails));
      }
      return res.result.value;
    };

    const initCheck = await evalExpr(`({
      jszipType: typeof JSZip,
      mode: document.getElementById('modeBadge').dataset.mode,
      studioHidden: document.getElementById('studioPanel').hidden,
      theme: document.documentElement.getAttribute('data-theme')
    })`);
    assert.strictEqual(initCheck.jszipType, 'function');
    assert.strictEqual(initCheck.mode, 'standalone');
    assert.strictEqual(initCheck.studioHidden, true);

    // Theme toggle + persistence across reload
    await evalExpr(`document.getElementById('themeToggleBtn').click()`);
    const afterToggle = await evalExpr(`({
      theme: document.documentElement.getAttribute('data-theme'),
      stored: localStorage.getItem('seamless_shorts_theme')
    })`);
    assert.strictEqual(afterToggle.theme, 'light');
    assert.strictEqual(afterToggle.stored, 'light');

    const reloadP = cdp.waitForEvent('Page.loadEventFired', sessionId);
    await cdp.send('Page.reload', {}, sessionId);
    await reloadP;
    await new Promise(r => setTimeout(r, 200));
    const persistedTheme = await evalExpr(`document.documentElement.getAttribute('data-theme')`);
    assert.strictEqual(persistedTheme, 'light');
    await evalExpr(`document.getElementById('themeToggleBtn').click()`);

    // Load .zip archive via JSZip + DataTransfer drop onto #dropzone!
    const manifestArg = JSON.stringify(manifestJson);
    const csvArg = JSON.stringify(sampleCsv);
    const zipDropCheck = await evalExpr(`(async () => {
      const z = new JSZip();
      z.file('manifest.json', ${manifestArg});
      z.file('stats.csv', ${csvArg});
      const zipBlob = await z.generateAsync({ type: 'blob' });
      const zipFile = new File([zipBlob], 'workspace_test.zip', { type: 'application/zip' });
      const dt = new DataTransfer();
      dt.items.add(zipFile);
      const dropEvt = new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt });
      document.getElementById('dropzone').dispatchEvent(dropEvt);

      // Wait briefly for async JSZip unpacking
      for (let i = 0; i < 40; i++) {
        if (document.querySelectorAll('#clipsList .clip-row-card').length === 9) break;
        await new Promise(r => setTimeout(r, 50));
      }

      const blocks = document.querySelectorAll('#clipBlocksTrack .clip-block').length;
      const cuts = document.querySelectorAll('#cutMarkersTrack .cut-marker').length;
      const cards = document.querySelectorAll('#clipsList .clip-row-card').length;
      const listHtml = document.getElementById('clipsList').innerText;

      // Audition Cut #1 (jump to verify_start[1] - 2.0)
      document.querySelector('#cutMarkersTrack .cut-marker[data-idx="1"]').click();
      const st = window.SeamlessApp.getState();
      const jumpedTime = st.globalTime;
      if (st.playing) document.getElementById('playPauseBtn').click();

      // Test WAV + JSZip bundle generation inside browser
      const sr = 8000;
      const fakeBuf = {
        numberOfChannels: 1,
        sampleRate: sr,
        getChannelData: () => new Float32Array(sr * 20).fill(0.25)
      };
      const wav = window.SeamlessCore.sliceAudioBufferToWav(fakeBuf, 0, 14.049, 15);
      const zOut = new JSZip();
      zOut.file('manifest.json', JSON.stringify(st.parts));
      zOut.file('Part_01_audio.wav', wav.bytes);
      const zipU8 = await zOut.generateAsync({ type: 'uint8array' });

      return {
        blocks, cuts, cards,
        hasSpiderManTitle: listHtml.includes('Spider-Man learn 2 #flow Juggle'),
        hasViews: listHtml.includes('5,685 views'),
        jumpedTime,
        wavHeader: String.fromCharCode(...wav.bytes.slice(0, 4)),
        zipHeader: [zipU8[0], zipU8[1]]
      };
    })()`);

    assert.strictEqual(zipDropCheck.blocks, 9);
    assert.strictEqual(zipDropCheck.cuts, 8);
    assert.strictEqual(zipDropCheck.cards, 9);
    assert.strictEqual(zipDropCheck.hasSpiderManTitle, true);
    assert.strictEqual(zipDropCheck.hasViews, true);
    assert.ok(Math.abs(zipDropCheck.jumpedTime - 12.049) < 0.25, 'Expected audition jump ~12.049s, got ' + zipDropCheck.jumpedTime);
    assert.strictEqual(zipDropCheck.wavHeader, 'RIFF');
    assert.deepStrictEqual(zipDropCheck.zipHeader, [0x50, 0x4b]);

    // Keyboard shortcuts: 'm' mute toggle, ']' next cut, '[' prev cut
    const kbdCheck = await evalExpr(`(() => {
      const beforeMute = document.getElementById('muteVideoBtn').getAttribute('aria-pressed');
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'm', bubbles: true }));
      const afterMute = document.getElementById('muteVideoBtn').getAttribute('aria-pressed');
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'm', bubbles: true }));
      document.dispatchEvent(new KeyboardEvent('keydown', { key: ']', bubbles: true }));
      const nextTime = window.SeamlessApp.getState().globalTime;
      if (window.SeamlessApp.getState().playing) document.getElementById('playPauseBtn').click();
      return { beforeMute, afterMute, nextTime };
    })()`);
    assert.notStrictEqual(kbdCheck.beforeMute, kbdCheck.afterMute);
    assert.ok(kbdCheck.nextTime > 10, 'Expected ] shortcut to jump to next cut');

    // Graceful codec error banner check
    const codecCheck = await evalExpr(`(() => {
      const v = document.getElementById('videoA');
      v.setAttribute('src', 'blob:invalid-test');
      v.dispatchEvent(new Event('error'));
      const shown = !document.getElementById('codecNotice').hidden;
      document.getElementById('dismissCodecNoticeBtn').click();
      return { shown, dismissed: document.getElementById('codecNotice').hidden };
    })()`);
    assert.strictEqual(codecCheck.shown, true);
    assert.strictEqual(codecCheck.dismissed, true);

    // Responsive check at desktop (1366x900) and mobile (390x844)
    for (const vp of [{ width: 1366, height: 900 }, { width: 390, height: 844 }]) {
      await cdp.send('Emulation.setDeviceMetricsOverride', {
        width: vp.width, height: vp.height, deviceScaleFactor: 1, mobile: vp.width < 600
      }, sessionId);
      await new Promise(r => setTimeout(r, 80));
      const overflow = await evalExpr(`document.documentElement.scrollWidth - window.innerWidth`);
      assert.ok(overflow <= 1, 'Horizontal overflow at ' + vp.width + 'px: ' + overflow);
    }

    // Real source clip + WAV song drop check (if workspace clip present)
    const fs = require('fs');
    const path = require('path');
    const clipPath = path.resolve('shorts_workspace/1_source_shorts/05_wIA-VYebvO4.mp4');
    if (fs.existsSync(clipPath)) {
      const b64Clip = fs.readFileSync(clipPath).toString('base64');
      const realClipRes = await evalExpr(`(async () => {
        document.getElementById('clearWorkspaceBtn').click();
        const bin = Uint8Array.from(atob(${JSON.stringify(b64Clip)}), c => c.charCodeAt(0));
        const f = new File([bin], '05_wIA-VYebvO4.mp4', { type: 'video/mp4' });
        const dt = new DataTransfer();
        dt.items.add(f);
        document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt }));
        for (let i = 0; i < 60; i++) {
          if (window.SeamlessApp.getState().parts.length === 1) break;
          await new Promise(r => setTimeout(r, 50));
        }
        const st = window.SeamlessApp.getState();
        return {
          partsLen: st.parts.length,
          dur: st.parts[0] ? st.parts[0].duration : 0
        };
      })()`);
      assert.strictEqual(realClipRes.partsLen, 1);
      assert.ok(realClipRes.dur > 13.5 && realClipRes.dur < 14.5, 'Unexpected measured clip duration: ' + realClipRes.dur);
    }

    assert.strictEqual(errors.length, 0, 'Unexpected standalone console/page errors: ' + errors.join('; '));

    // 2. Test Studio mode detection over http://
    const studioTarget = await cdp.send('Target.createTarget', { url: 'about:blank' });
    const studioSess = (await cdp.send('Target.attachToTarget', { targetId: studioTarget.targetId, flatten: true })).sessionId;
    studioSessId = studioSess;
    await cdp.send('Page.enable', {}, studioSess);
    await cdp.send('Runtime.enable', {}, studioSess);

    const studioLoadP = cdp.waitForEvent('Page.loadEventFired', studioSess);
    await cdp.send('Page.navigate', { url: studioUrl }, studioSess);
    await studioLoadP;
    await new Promise(r => setTimeout(r, 500));

    const studioCheck = await cdp.send('Runtime.evaluate', {
      expression: `({
        mode: document.getElementById('modeBadge').dataset.mode,
        studioHidden: document.getElementById('studioPanel').hidden,
        playlistVal: document.getElementById('playlistUrl').value,
        cookiesOptions: document.getElementById('cookiesFromBrowser').options.length,
        clipsCount: document.querySelectorAll('#clipsList .clip-row-card').length,
        pillsText: document.getElementById('studioToolPills').innerText,
        wavZipDisabled: document.getElementById('dlWavZipBtn').disabled
      })`,
      returnByValue: true
    }, studioSess);
    const sv = studioCheck.result.value;
    assert.strictEqual(sv.mode, 'studio');
    assert.strictEqual(sv.studioHidden, false);
    assert.strictEqual(sv.playlistVal, 'https://www.youtube.com/playlist?list=TEST123');
    assert.ok(sv.cookiesOptions >= 5);
    assert.strictEqual(sv.clipsCount, 2);
    assert.ok(sv.pillsText.includes('FFmpeg: Ready'));
    assert.strictEqual(sv.wavZipDisabled, false, 'Expected Studio WAV audio decode to enable WAV slices ZIP button');
    assert.strictEqual(studioErrors.length, 0, 'Unexpected studio console/page errors: ' + studioErrors.join('; '));

    console.log('CHROMIUM_CDP_TESTS_OK');
  } finally {
    cdp.close();
    proc.kill('SIGTERM');
  }
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
"""

        try:
            with tempfile.TemporaryDirectory() as user_data_dir:
                standalone_uri = (WEB_DIR / "Seamless_Shorts_Player.html").resolve().as_uri()
                studio_uri = f"http://127.0.0.1:{port}/"
                res = subprocess.run(
                    [
                        node_bin,
                        "-e",
                        node_cdp_driver,
                        chromium_bin,
                        user_data_dir,
                        standalone_uri,
                        studio_uri,
                        manifest_json,
                        sample_csv,
                    ],
                    env=clean_local_env(),
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
                self.assertEqual(
                    res.returncode,
                    0,
                    f"Chromium CDP test failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}",
                )
                self.assertIn("CHROMIUM_CDP_TESTS_OK", res.stdout)
        finally:
            server.shutdown()
            server.server_close()

    def test_light_theme_wcag_aa_contrast_and_dropzone_aria(self) -> None:
        import colorsys

        css_text = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
        m_light = re.search(r'\[data-theme="light"\]\s*\{([^}]*)\}', css_text, re.DOTALL)
        self.assertIsNotNone(m_light, "Missing [data-theme='light'] block in web/styles.css")
        block = m_light.group(1)

        hsl_vars: dict[str, tuple[float, float, float]] = {}
        for var_m in re.finditer(r'(--[a-z0-9-]+)\s*:\s*hsl\(\s*([\d.]+)\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%\s*\)', block):
            hsl_vars[var_m.group(1)] = (float(var_m.group(2)), float(var_m.group(3)), float(var_m.group(4)))

        def contrast_ratio(c1: tuple[float, float, float], c2: tuple[float, float, float]) -> float:
            def to_lum(hsl: tuple[float, float, float]) -> float:
                r, g, b = colorsys.hls_to_rgb(hsl[0] / 360.0, hsl[2] / 100.0, hsl[1] / 100.0)
                lin = [ch / 12.92 if ch <= 0.03928 else ((ch + 0.055) / 1.055) ** 2.4 for ch in (r, g, b)]
                return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

            l1, l2 = to_lum(c1), to_lum(c2)
            if l1 < l2:
                l1, l2 = l2, l1
            return (l1 + 0.05) / (l2 + 0.05)

        for fg, bg in (
            ("--text-dim", "--surface-well"),
            ("--text-dim", "--surface"),
            ("--cut-ink", "--cut"),
            ("--accent-ink", "--accent"),
        ):
            self.assertIn(fg, hsl_vars, f"Missing {fg} in light theme")
            self.assertIn(bg, hsl_vars, f"Missing {bg} in light theme")
            ratio = contrast_ratio(hsl_vars[fg], hsl_vars[bg])
            self.assertGreaterEqual(ratio, 4.5, f"Light theme {fg} on {bg} contrast {ratio:.2f}:1 < 4.5:1")

        html_text = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        dz_m = re.search(r'<div\s+class="dropzone"[^>]*>', html_text)
        self.assertIsNotNone(dz_m, "Missing dropzone element in web/index.html")
        dz_tag = dz_m.group(0)
        self.assertNotIn('role="button"', dz_tag)
        self.assertNotIn('tabindex="0"', dz_tag)

    def test_node_core_csv_exact_header_empty_views_and_infinity_duration(self) -> None:
        node_bin = shutil.which("node")
        if not node_bin:
            self.skipTest("node not available")
        node_script = r"""
const assert = require('assert');
const core = require(process.argv[1]);

// 1. Empty Views -> null (not 0)
const csvEmptyViews = 'Content,Video title,Views\nabc12345678,My Short,\nxyz98765432,Blank Views,   \n';
const parsedEmpty = core.parseCsvRfc4180(csvEmptyViews);
assert.strictEqual(parsedEmpty.byId['abc12345678'].views, null);
assert.strictEqual(parsedEmpty.byId['xyz98765432'].views, null);

// 2. Exact header match beats substring match (Paid vs Content, Subtitle vs Video title)
const csvSubstr = 'Paid,Subtitle,Video title,Content,Views\nYes,SubText,Real Title,abc12345678,42\n';
const parsedSubstr = core.parseCsvRfc4180(csvSubstr);
assert.ok(parsedSubstr.byId['abc12345678'], 'Content column must beat Paid substring match on id');
assert.strictEqual(parsedSubstr.byId['abc12345678'].title, 'Real Title', 'Video title must beat Subtitle');
assert.strictEqual(parsedSubstr.byId['abc12345678'].views, 42);

// 3. Infinity duration skipped by computeSequentialPlan
const plan = core.computeSequentialPlan([
  { filename: '01_inf.mp4', duration: Infinity },
  { filename: '02_ok.mp4', duration: 2.5 }
], 10.0, 15);
assert.strictEqual(plan.parts.length, 1);
assert.strictEqual(plan.parts[0].source_filename, '02_ok.mp4');
console.log('WEB_CORE_EDGE_OK');
"""
        res = subprocess.run(
            [node_bin, "-e", node_script, str(WEB_DIR / "app.js")],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("WEB_CORE_EDGE_OK", res.stdout)


if __name__ == "__main__":
    unittest.main()
