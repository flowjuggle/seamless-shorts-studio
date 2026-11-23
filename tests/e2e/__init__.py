"""Shared utilities, HTTP/Proxy helpers, server runners, and Chromium CDP bridge for E2E tests."""

from __future__ import annotations

import functools
import http.server
import json
import os
import re
import shutil
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
WEB_DIR = ROOT_DIR / "web"
TOOLS_DIR = ROOT_DIR / "tools"
DEPLOY_DIR = ROOT_DIR / "deploy"
AGENT_PATH = ROOT_DIR / "seamless_shorts_agent.py"
WORKSPACE_DIR = ROOT_DIR / "shorts_workspace"
ZIP_PATH = ROOT_DIR / "upload" / "YT_1m_to_full_song_1x.zip"
SYMLINK_ZIP_PATH = ROOT_DIR / "YT_1m_to_full_song_1x.zip"
README_PATH = ROOT_DIR / "README.txt"
BAT_PATH = ROOT_DIR / "START_HERE.bat"
CMD_PATH = ROOT_DIR / "START_HERE.command"

EXPECTED_CORS_METHODS = "GET, POST, OPTIONS, HEAD"
JSZIP_SHA256 = "acc7e41455a80765b5fd9c7ee1b8078a6d160bbbca455aeae854de65c947d59e"


def build_local_opener() -> urllib.request.OpenerDirector:
    """Create an opener bypassing HTTP_PROXY/HTTPS_PROXY for 127.0.0.1/localhost requests."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, str], bytes]:
    """Execute a proxy-bypassed HTTP request and return (status, lower_headers, body)."""
    opener = build_local_opener()
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with opener.open(req, timeout=timeout) as resp:
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            body = b"" if method == "HEAD" else resp.read()
            return resp.status, resp_headers, body
    except urllib.error.HTTPError as err:
        resp_headers = {k.lower(): v for k, v in err.headers.items()}
        body = b"" if method == "HEAD" else err.read()
        err.close()
        return err.code, resp_headers, body


def clean_subproc_env(
    extra: dict[str, str] | None = None,
    strip_host_port: bool = True,
) -> dict[str, str]:
    """Return environment dict with proxy variables removed (and optionally HOST/PORT)."""
    env = os.environ.copy()
    keys = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]
    if strip_host_port:
        keys.extend(["HOST", "PORT"])
    for k in keys:
        env.pop(k, None)
    if extra:
        env.update(extra)
    return env


def make_wav_bytes(
    sample_rate: int = 8000,
    duration_sec: float = 2.0,
    freq: float = 440.0,
    amplitude: float = 0.35,
) -> bytes:
    """Generate deterministic valid RIFF PCM16 mono WAV bytes in memory."""
    import math

    num_samples = max(1, int(sample_rate * duration_sec))
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
    scale = max(0.0, min(1.0, amplitude)) * 32000.0
    two_pi_f = 2.0 * math.pi * freq
    for i in range(num_samples):
        val = int(scale * math.sin(two_pi_f * (i / sample_rate)))
        struct.pack_into("<h", samples, i * 2, val)
    return header + bytes(samples)


def start_agent_server(
    script_path: Path = AGENT_PATH,
    cwd: Path = ROOT_DIR,
    port_arg: int | None = 0,
    host_arg: str | None = None,
    extra_env: dict[str, str] | None = None,
    strip_host_port: bool = True,
    timeout: float = 15.0,
) -> tuple[subprocess.Popen[str], str, Callable[[], None]]:
    """Start seamless_shorts_agent.py --ui --no-browser as a subprocess and wait for /api/health."""
    cmd = [sys.executable, str(script_path), "--ui", "--no-browser"]
    if host_arg is not None:
        cmd.extend(["--host", host_arg])
    if port_arg is not None:
        cmd.extend(["--port", str(port_arg)])

    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=clean_subproc_env(extra_env, strip_host_port=strip_host_port),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    def stop() -> None:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        if proc.stdout:
            try:
                proc.stdout.close()
            except Exception:
                pass

    deadline = time.monotonic() + timeout
    bound_url = None
    captured: list[str] = []
    assert proc.stdout is not None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            rest = proc.stdout.read()
            stop()
            raise AssertionError(
                f"Studio server exited early (rc={proc.returncode}): {''.join(captured)}{rest}"
            )
        line = proc.stdout.readline()
        if line:
            captured.append(line)
            m = re.search(r"http://127\.0\.0\.1:(\d+)", line)
            if m:
                bound_url = f"http://127.0.0.1:{m.group(1)}"
                break
        else:
            time.sleep(0.04)

    if not bound_url:
        stop()
        raise AssertionError(f"Server did not report bound URL within {timeout}s. Output:\n{''.join(captured)}")

    while time.monotonic() < deadline:
        try:
            st, _, _ = http_request(f"{bound_url}/api/health", timeout=2.0)
            if st == 200:
                return proc, bound_url, stop
        except Exception:
            time.sleep(0.05)

    stop()
    raise AssertionError(f"Server at {bound_url} failed /api/health probe")


def start_static_server(
    directory: Path,
    health_response: tuple[int, str, bytes] | None = None,
) -> tuple[socketserver.TCPServer, str, Callable[[], None]]:
    """Start a threaded static HTTP server (optionally mocking /api/health failure cases)."""

    class StaticHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/api/health" and health_response is not None:
                code, ctype, body = health_response
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

    handler_cls = functools.partial(StaticHandler, directory=str(directory))

    class ReusableHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = ReusableHTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop() -> None:
        server.shutdown()
        server.server_close()

    return server, f"http://127.0.0.1:{port}", stop


def run_node_script(script: str, args: list[str] | tuple[str, ...] = (), timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    """Execute a Node.js snippet via node -e and return CompletedProcess."""
    node_bin = shutil.which("node")
    if not node_bin:
        raise RuntimeError("node binary not found in PATH")
    return subprocess.run(
        [node_bin, "-e", script, *[str(a) for a in args]],
        env=clean_subproc_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


_NODE_CDP_BRIDGE = r"""
const readline = require('readline');
const { spawn } = require('child_process');

const chromiumBin = process.argv[1];
const userDataDir = process.argv[2];

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
    const timer = setTimeout(() => reject(new Error('Chromium startup timeout: ' + stderr)), 15000);
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
      reject(new Error('Chromium exited early with code ' + code + ': ' + stderr));
    });
  });
}

class CDPConn {
  constructor(ws) {
    this.ws = ws;
    this.nextId = 1;
    this.pending = new Map();
    this.eventWaiters = [];
    this.logs = new Map(); // sessionId -> { consoleErrors, exceptions, networkUrls, failedRequests }
    ws.addEventListener('message', (evt) => {
      const msg = JSON.parse(evt.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(JSON.stringify(msg.error)));
        else resolve(msg.result);
      } else if (msg.method) {
        this._recordEvent(msg);
        for (let i = this.eventWaiters.length - 1; i >= 0; i--) {
          const w = this.eventWaiters[i];
          if (msg.method === w.method && (!w.sessionId || msg.sessionId === w.sessionId)) {
            this.eventWaiters.splice(i, 1);
            w.resolve(msg);
          }
        }
      }
    });
  }

  ensureLogs(sessionId) {
    if (!this.logs.has(sessionId)) {
      this.logs.set(sessionId, {
        consoleErrors: [],
        exceptions: [],
        networkUrls: [],
        failedRequests: []
      });
    }
    return this.logs.get(sessionId);
  }

  _recordEvent(msg) {
    const sid = msg.sessionId;
    if (!sid) return;
    const rec = this.ensureLogs(sid);
    if (msg.method === 'Runtime.exceptionThrown') {
      rec.exceptions.push(JSON.stringify(msg.params.exceptionDetails || msg.params));
    } else if (msg.method === 'Runtime.consoleAPICalled' && msg.params && msg.params.type === 'error') {
      rec.consoleErrors.push(JSON.stringify(msg.params.args || []));
    } else if (msg.method === 'Network.requestWillBeSent' && msg.params && msg.params.request) {
      rec.networkUrls.push(msg.params.request.url);
    } else if (msg.method === 'Network.loadingFailed' && msg.params) {
      rec.failedRequests.push(msg.params.errorText || 'loadingFailed');
    }
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
    return new Promise((resolve, reject) => {
      const entry = { method, sessionId, resolve: null };
      const timer = setTimeout(() => {
        const idx = this.eventWaiters.indexOf(entry);
        if (idx >= 0) this.eventWaiters.splice(idx, 1);
        reject(new Error('Timed out waiting for CDP event ' + method));
      }, timeoutMs);
      entry.resolve = (ev) => {
        clearTimeout(timer);
        resolve(ev);
      };
      this.eventWaiters.push(entry);
    });
  }
}

async function main() {
  const { proc, wsUrl } = await startChromium();
  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const cdp = new CDPConn(ws);
  const path = require('path');
  const fs = require('fs');
  const dlDir = path.join(userDataDir, 'downloads');
  fs.mkdirSync(dlDir, { recursive: true });
  await cdp.send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: dlDir });
  const targetsBySession = new Map();

  const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  const writeResp = (obj) => process.stdout.write(JSON.stringify(obj) + '\n');
  writeResp({ ready: true });

  for await (const line of rl) {
    if (!line.trim()) continue;
    let req;
    try {
      req = JSON.parse(line);
    } catch (e) {
      writeResp({ id: null, ok: false, error: 'Bad JSON command: ' + e.message });
      continue;
    }

    try {
      if (req.cmd === 'new_tab') {
        const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
        const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
        targetsBySession.set(sessionId, targetId);
        cdp.ensureLogs(sessionId);
        await cdp.send('Page.enable', {}, sessionId);
        await cdp.send('Runtime.enable', {}, sessionId);
        await cdp.send('Network.enable', {}, sessionId);
        if (req.url && req.url !== 'about:blank') {
          const loadP = cdp.waitForEvent('Page.loadEventFired', sessionId, 12000);
          await cdp.send('Page.navigate', { url: req.url }, sessionId);
          await loadP;
          await new Promise(r => setTimeout(r, req.waitMs || 220));
        }
        writeResp({ id: req.id, ok: true, result: { sessionId, targetId } });
      } else if (req.cmd === 'navigate') {
        cdp.logs.set(req.sessionId, { consoleErrors: [], exceptions: [], networkUrls: [], failedRequests: [] });
        const loadP = cdp.waitForEvent('Page.loadEventFired', req.sessionId, 12000);
        await cdp.send('Page.navigate', { url: req.url }, req.sessionId);
        await loadP;
        await new Promise(r => setTimeout(r, req.waitMs || 220));
        writeResp({ id: req.id, ok: true, result: true });
      } else if (req.cmd === 'reload') {
        cdp.logs.set(req.sessionId, { consoleErrors: [], exceptions: [], networkUrls: [], failedRequests: [] });
        const loadP = cdp.waitForEvent('Page.loadEventFired', req.sessionId, 12000);
        await cdp.send('Page.reload', {}, req.sessionId);
        await loadP;
        await new Promise(r => setTimeout(r, req.waitMs || 220));
        writeResp({ id: req.id, ok: true, result: true });
      } else if (req.cmd === 'eval') {
        const res = await cdp.send('Runtime.evaluate', {
          expression: req.expr,
          awaitPromise: true,
          returnByValue: true
        }, req.sessionId);
        if (res.exceptionDetails) {
          writeResp({ id: req.id, ok: false, error: JSON.stringify(res.exceptionDetails) });
        } else {
          writeResp({ id: req.id, ok: true, result: res.result ? res.result.value : undefined });
        }
      } else if (req.cmd === 'set_viewport') {
        await cdp.send('Emulation.setDeviceMetricsOverride', {
          width: req.width,
          height: req.height,
          deviceScaleFactor: req.deviceScaleFactor || 1,
          mobile: Boolean(req.mobile)
        }, req.sessionId);
        await new Promise(r => setTimeout(r, 60));
        writeResp({ id: req.id, ok: true, result: true });
      } else if (req.cmd === 'dispatch_mouse') {
        await cdp.send('Input.dispatchMouseEvent', {
          type: req.type,
          x: req.x,
          y: req.y,
          button: req.button || 'left',
          buttons: req.buttons !== undefined ? req.buttons : (req.type === 'mouseReleased' ? 0 : 1),
          clickCount: req.clickCount !== undefined ? req.clickCount : 1
        }, req.sessionId);
        await new Promise(r => setTimeout(r, 20));
        writeResp({ id: req.id, ok: true, result: true });
      } else if (req.cmd === 'get_logs') {
        const rec = cdp.ensureLogs(req.sessionId);
        writeResp({ id: req.id, ok: true, result: rec });
      } else if (req.cmd === 'clear_logs') {
        cdp.logs.set(req.sessionId, { consoleErrors: [], exceptions: [], networkUrls: [], failedRequests: [] });
        writeResp({ id: req.id, ok: true, result: true });
      } else if (req.cmd === 'close_tab') {
        const targetId = targetsBySession.get(req.sessionId);
        if (targetId) {
          await cdp.send('Target.closeTarget', { targetId });
          targetsBySession.delete(req.sessionId);
        }
        writeResp({ id: req.id, ok: true, result: true });
      } else if (req.cmd === 'shutdown') {
        try { ws.close(); } catch (_) {}
        try { proc.kill('SIGTERM'); } catch (_) {}
        writeResp({ id: req.id, ok: true, result: true });
        break;
      } else {
        writeResp({ id: req.id, ok: false, error: 'Unknown cmd: ' + req.cmd });
      }
    } catch (err) {
      writeResp({ id: req.id, ok: false, error: err && err.stack ? err.stack : String(err) });
    }
  }
  try { proc.kill('SIGKILL'); } catch (_) {}
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
"""


class ChromiumCDPBridge:
    """Fast persistent headless Chromium CDP bridge shared within a TestCase class."""

    def __init__(self) -> None:
        self.chromium_bin = shutil.which("chromium") or shutil.which("google-chrome")
        self.node_bin = shutil.which("node")
        if not self.chromium_bin or not self.node_bin:
            raise RuntimeError("chromium or node not available")
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._next_id = 1
        self._proc = subprocess.Popen(
            [self.node_bin, "-e", _NODE_CDP_BRIDGE, self.chromium_bin, self._tmp_dir.name],
            env=clean_subproc_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert self._proc.stdout is not None
        ready_line = self._proc.stdout.readline()
        if not ready_line:
            stderr_txt = self._proc.stderr.read() if self._proc.stderr else ""
            self.close()
            raise RuntimeError(f"CDP bridge failed to start: {stderr_txt}")
        ready = json.loads(ready_line)
        if not ready.get("ready"):
            self.close()
            raise RuntimeError(f"CDP bridge did not report ready: {ready}")

    def _call(self, payload: dict[str, Any]) -> Any:
        assert self._proc.stdin is not None and self._proc.stdout is not None
        req_id = self._next_id
        self._next_id += 1
        payload["id"] = req_id
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()
        resp_line = self._proc.stdout.readline()
        if not resp_line:
            err_out = self._proc.stderr.read() if self._proc.stderr else ""
            raise RuntimeError(f"CDP bridge closed unexpectedly: {err_out}")
        resp = json.loads(resp_line)
        if not resp.get("ok"):
            raise RuntimeError(f"CDP command {payload.get('cmd')} failed: {resp.get('error')}")
        return resp.get("result")

    def new_tab(self, url: str = "about:blank", wait_ms: int = 220) -> str:
        res = self._call({"cmd": "new_tab", "url": url, "waitMs": wait_ms})
        return str(res["sessionId"])

    def navigate(self, session_id: str, url: str, wait_ms: int = 220) -> None:
        self._call({"cmd": "navigate", "sessionId": session_id, "url": url, "waitMs": wait_ms})

    def reload(self, session_id: str, wait_ms: int = 220) -> None:
        self._call({"cmd": "reload", "sessionId": session_id, "waitMs": wait_ms})

    def eval(self, session_id: str, expr: str) -> Any:
        return self._call({"cmd": "eval", "sessionId": session_id, "expr": expr})

    def set_viewport(self, session_id: str, width: int, height: int, mobile: bool = False) -> None:
        self._call(
            {
                "cmd": "set_viewport",
                "sessionId": session_id,
                "width": width,
                "height": height,
                "mobile": mobile,
            }
        )

    def dispatch_mouse(
        self,
        session_id: str,
        event_type: str,
        x: float,
        y: float,
        button: str = "left",
        buttons: int | None = None,
        click_count: int = 1,
    ) -> None:
        payload: dict[str, Any] = {
            "cmd": "dispatch_mouse",
            "sessionId": session_id,
            "type": event_type,
            "x": x,
            "y": y,
            "button": button,
            "clickCount": click_count,
        }
        if buttons is not None:
            payload["buttons"] = buttons
        self._call(payload)

    def get_logs(self, session_id: str) -> dict[str, list[str]]:
        return self._call({"cmd": "get_logs", "sessionId": session_id})

    def clear_logs(self, session_id: str) -> None:
        self._call({"cmd": "clear_logs", "sessionId": session_id})

    def close_tab(self, session_id: str) -> None:
        self._call({"cmd": "close_tab", "sessionId": session_id})

    def close(self) -> None:
        try:
            if self._proc.poll() is None and self._proc.stdin:
                self._call({"cmd": "shutdown"})
        except Exception:
            pass
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)
        for stream in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
            if stream:
                try:
                    stream.close()
                except Exception:
                    pass
        try:
            self._tmp_dir.cleanup()
        except Exception:
            pass
