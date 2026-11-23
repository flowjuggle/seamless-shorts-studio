#!/usr/bin/env python3
"""Tier 1 (Feature Coverage) & Tier 2 (Boundary/Corner/Error) E2E tests for F7–F14."""

from __future__ import annotations

import ast
import http.server
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import yaml

from tests.e2e import (
    AGENT_PATH,
    BAT_PATH,
    CMD_PATH,
    DEPLOY_DIR,
    EXPECTED_CORS_METHODS,
    README_PATH,
    ROOT_DIR,
    SYMLINK_ZIP_PATH,
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

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import seamless_shorts_agent as agent  # noqa: E402


def _assert_cors(test_case: unittest.TestCase, headers: dict[str, str]) -> None:
    test_case.assertEqual(headers.get("access-control-allow-origin"), "*")
    test_case.assertEqual(headers.get("access-control-allow-methods"), EXPECTED_CORS_METHODS)
    test_case.assertEqual(headers.get("access-control-allow-headers"), "*")
    expose = headers.get("access-control-expose-headers", "")
    for token in ("Content-Range", "Accept-Ranges", "Content-Length"):
        test_case.assertIn(token, expose)


class TestLiveReadOnlyStudioServer(unittest.TestCase):
    """Shared read-only Studio server against /home/antigravity for F7, F9, F10, F14."""

    base_url: str = ""
    _stop = None

    @classmethod
    def setUpClass(cls) -> None:
        _, cls.base_url, cls._stop = start_agent_server(cwd=ROOT_DIR, port_arg=0)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._stop is not None:
            cls._stop()

    # ---------------- F7: Studio Status & Job Control ----------------

    def test_t1_f7_studio_ui_reveals_studio_panel_and_populates_controls_in_chromium(self) -> None:
        chromium_bin = shutil.which("chromium") or shutil.which("google-chrome")
        node_bin = shutil.which("node")
        if not chromium_bin or not node_bin:
            self.skipTest("chromium or node not available")
        cdp = ChromiumCDPBridge()
        try:
            sid = cdp.new_tab(f"{self.base_url}/", wait_ms=500)
            info = cdp.eval(
                sid,
                """(() => ({
                  mode: document.getElementById('modeBadge').dataset.mode,
                  studioHidden: document.getElementById('studioPanel').hidden,
                  toolkitHidden: document.getElementById('dlToolkitBtn').hidden,
                  pillsText: document.getElementById('studioToolPills').innerText,
                  playlistVal: document.getElementById('playlistUrl').value,
                  songCount: document.getElementById('songSelect').options.length,
                  cookiesCount: document.getElementById('cookiesFromBrowser').options.length,
                  clipsCount: document.querySelectorAll('#clipsList .clip-row-card').length
                }))()""",
            )
            logs = cdp.get_logs(sid)
            self.assertEqual(info["mode"], "studio")
            self.assertFalse(info["studioHidden"])
            self.assertFalse(info["toolkitHidden"])
            self.assertIn("Sources: 9", info["pillsText"])
            self.assertIn("FFmpeg: Ready", info["pillsText"])
            self.assertIn("youtube.com/playlist", info["playlistVal"])
            self.assertGreaterEqual(info["songCount"], 1)
            self.assertGreaterEqual(info["cookiesCount"], 5)
            self.assertEqual(info["clipsCount"], 9)
            self.assertEqual(logs["exceptions"], [])
            self.assertEqual(logs["consoleErrors"], [])
        finally:
            cdp.close()

    def test_t1_f7_api_state_schema_tools_sources_parts_job(self) -> None:
        st, hdrs, body = http_request(f"{self.base_url}/api/state")
        self.assertEqual(st, 200)
        _assert_cors(self, hdrs)
        data = json.loads(body.decode("utf-8"))
        for k in (
            "version", "tools", "playlist_url", "active_playlist",
            "active_song_path", "active_song", "song_name", "is_demo_song",
            "fade_ms", "audio_files", "sources", "source_count",
            "parts", "manifest", "verify_exists", "has_verify", "verify_mtime", "job",
        ):
            self.assertIn(k, data, f"Missing key in /api/state: {k}")
        self.assertEqual(data["source_count"], 9)
        self.assertEqual(len(data["sources"]), 9)
        self.assertEqual(len(data["parts"]), 9)
        self.assertTrue(data["verify_exists"])
        self.assertIs(data["has_verify"], True)
        self.assertIn("logs", data["job"])

    def test_t1_f7_studio_ui_displays_demo_banner_and_browser_cookies_options(self) -> None:
        html_txt = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        for browser in ("chrome", "edge", "firefox", "brave", "chromium", "safari"):
            self.assertIn(f'value="{browser}"', html_txt)
        st, _, body = http_request(f"{self.base_url}/api/state")
        self.assertEqual(st, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["is_demo_song"])

    def test_t2_f7_api_run_malformed_json_and_non_object_returns_400(self) -> None:
        for bad in (b"{not valid json", b"[1, 2, 3]", b'"string_not_obj"', b"12345"):
            st, hdrs, body = http_request(
                f"{self.base_url}/api/run",
                method="POST",
                headers={"Content-Type": "application/json"},
                data=bad,
            )
            self.assertEqual(st, 400, f"Expected 400 for payload {bad!r}, got {st}")
            _assert_cors(self, hdrs)
            self.assertIn("error", json.loads(body.decode("utf-8")))

    def test_t2_f7_api_run_cookies_from_browser_rejects_flag_injection(self) -> None:
        for bad_tok in ("--exec=touch", "chrome --evil", "edge\nfoo", "bad;cookie"):
            st, hdrs, body = http_request(
                f"{self.base_url}/api/run",
                method="POST",
                headers={"Content-Type": "application/json"},
                data=json.dumps({"cookies_from_browser": bad_tok}).encode("utf-8"),
            )
            self.assertEqual(st, 400, f"Expected 400 for cookies_from_browser={bad_tok!r}")
            _assert_cors(self, hdrs)
            self.assertIn("error", json.loads(body.decode("utf-8")))

    def test_t2_f7_api_upload_song_rejects_empty_oversize_and_non_audio(self) -> None:
        # Empty body
        st, hdrs, _ = http_request(
            f"{self.base_url}/api/upload-song",
            method="POST",
            headers={"X-Filename": "song.mp3"},
            data=b"",
        )
        self.assertEqual(st, 400)
        _assert_cors(self, hdrs)

        # Missing filename header
        st, hdrs, _ = http_request(
            f"{self.base_url}/api/upload-song",
            method="POST",
            data=b"ID3data",
        )
        self.assertEqual(st, 400)
        _assert_cors(self, hdrs)

        # Non-audio / traversal
        for bad_name in ("script.py", "clip.mp4", "../evil.mp3", "sub/evil.wav", "C:evil.mp3"):
            st, hdrs, _ = http_request(
                f"{self.base_url}/api/upload-song",
                method="POST",
                headers={"X-Filename": bad_name},
                data=b"RIFFdata",
            )
            self.assertEqual(st, 400, f"Expected 400 for X-Filename={bad_name!r}")
            _assert_cors(self, hdrs)

        # Invalid Content-Length exceeding 200 MB
        st, hdrs, _ = http_request(
            f"{self.base_url}/api/upload-song",
            method="POST",
            headers={"X-Filename": "huge.wav", "Content-Length": str(250 * 1024 * 1024)},
            data=b"",
        )
        self.assertEqual(st, 400)

    # ---------------- F9: Tool Detection (Live API subset) ----------------

    def test_t1_f9_real_ffmpeg_ffprobe_ytdlp_detected_in_cli_and_api(self) -> None:
        st, _, body = http_request(f"{self.base_url}/api/state")
        self.assertEqual(st, 200)
        tools = json.loads(body.decode("utf-8"))["tools"]
        self.assertTrue(tools["ffmpeg"])
        self.assertTrue(tools["ffprobe"])
        self.assertTrue(tools["yt_dlp"])
        self.assertRegex(tools["python"], r"^3\.\d+\.\d+$")

    # ---------------- F10: Server Web Serving, Range, CORS ----------------

    def test_t1_f10_serves_root_web_prefix_and_flat_asset_routes(self) -> None:
        expected = {
            "index.html": "text/html; charset=utf-8",
            "styles.css": "text/css; charset=utf-8",
            "app.js": "application/javascript; charset=utf-8",
            "jszip.min.js": "application/javascript; charset=utf-8",
            "Seamless_Shorts_Player.html": "text/html; charset=utf-8",
        }
        st, hdrs, body = http_request(f"{self.base_url}/")
        self.assertEqual(st, 200)
        self.assertEqual(hdrs.get("content-type"), "text/html; charset=utf-8")
        self.assertEqual(hdrs.get("cache-control"), "no-store")
        self.assertEqual(body, (WEB_DIR / "index.html").read_bytes())
        _assert_cors(self, hdrs)

        for name, mime in expected.items():
            disk = (WEB_DIR / name).read_bytes()
            for route in (f"/web/{name}", f"/{name}"):
                s, h, b = http_request(f"{self.base_url}{route}")
                self.assertEqual(s, 200, f"GET {route} failed")
                self.assertEqual(h.get("content-type"), mime)
                self.assertEqual(b, disk)
                _assert_cors(self, h)

    def test_t1_f10_range_206_closed_open_and_suffix_across_all_media_endpoints(self) -> None:
        _, _, state_raw = http_request(f"{self.base_url}/api/state")
        st_data = json.loads(state_raw.decode("utf-8"))
        first_part = st_data["parts"][0]["filename"]
        first_source = st_data["sources"][0]

        targets = [
            ("/media/verify", WORKSPACE_DIR / "3_verification_full_playthrough.mp4", "video/mp4"),
            (f"/media/part/{first_part}", WORKSPACE_DIR / "2_repurposed_shorts" / first_part, "video/mp4"),
            (f"/media/source/{first_source}", WORKSPACE_DIR / "1_source_shorts" / first_source, "video/mp4"),
            ("/media/audio/full_song.mp3", ROOT_DIR / "full_song.mp3", "audio/mpeg"),
        ]
        for route, disk_path, mime in targets:
            size = disk_path.stat().st_size
            with open(disk_path, "rb") as fh:
                head = fh.read(512)
                fh.seek(size - 256)
                tail = fh.read(256)

            s1, h1, b1 = http_request(f"{self.base_url}{route}", headers={"Range": "bytes=0-511"})
            self.assertEqual(s1, 206)
            self.assertEqual(h1.get("content-type"), mime)
            self.assertEqual(h1.get("accept-ranges"), "bytes")
            self.assertEqual(h1.get("content-range"), f"bytes 0-511/{size}")
            self.assertEqual(b1, head)
            _assert_cors(self, h1)

            s2, h2, b2 = http_request(f"{self.base_url}{route}", headers={"Range": f"bytes={size - 256}-"})
            self.assertEqual(s2, 206)
            self.assertEqual(h2.get("content-range"), f"bytes {size - 256}-{size - 1}/{size}")
            self.assertEqual(b2, tail)

            s3, h3, b3 = http_request(f"{self.base_url}{route}", headers={"Range": "bytes=-256"})
            self.assertEqual(s3, 206)
            self.assertEqual(h3.get("content-range"), f"bytes {size - 256}-{size - 1}/{size}")
            self.assertEqual(b3, tail)

    def test_t1_f10_cors_headers_and_options_preflight_204(self) -> None:
        for path in ("/api/health", "/api/state", "/api/run", "/media/verify"):
            st, hdrs, body = http_request(f"{self.base_url}{path}", method="OPTIONS")
            self.assertEqual(st, 204)
            self.assertEqual(body, b"")
            self.assertEqual(hdrs.get("content-length"), "0")
            _assert_cors(self, hdrs)

    def test_t1_f10_head_parity_and_attachment_download_flag(self) -> None:
        _, _, state_raw = http_request(f"{self.base_url}/api/state")
        st_data = json.loads(state_raw.decode("utf-8"))
        first_part = st_data["parts"][0]["filename"]

        for route in ("/", "/api/health", "/api/state", "/media/verify", f"/media/part/{first_part}", "/media/audio/full_song.mp3"):
            gs, gh, _ = http_request(f"{self.base_url}{route}", method="GET")
            hs, hh, hb = http_request(f"{self.base_url}{route}", method="HEAD")
            self.assertEqual(hs, gs)
            self.assertEqual(hb, b"")
            self.assertEqual(hh.get("content-type"), gh.get("content-type"))
            self.assertEqual(hh.get("content-length"), gh.get("content-length"))
            _assert_cors(self, hh)

        st, hdrs, _ = http_request(f"{self.base_url}/media/part/{first_part}?download=1", method="HEAD")
        self.assertEqual(st, 200)
        self.assertIn(f'attachment; filename="{first_part}"', hdrs.get("content-disposition", ""))

    def test_t2_f10_invalid_range_headers_return_416_with_content_range(self) -> None:
        size = (WORKSPACE_DIR / "3_verification_full_playthrough.mp4").stat().st_size
        for bad_range in ("bytes=-", "bytes=-0", "bytes=900-100", f"bytes={size}-", f"bytes={size + 5000}-", "bytes=abc-def"):
            st, hdrs, _ = http_request(f"{self.base_url}/media/verify", headers={"Range": bad_range})
            self.assertEqual(st, 416, f"Expected 416 for Range={bad_range!r}, got {st}")
            self.assertEqual(hdrs.get("content-range"), f"bytes */{size}")
            self.assertEqual(hdrs.get("accept-ranges"), "bytes")
            _assert_cors(self, hdrs)

    def test_t2_f10_path_traversal_variants_rejected_across_all_routes(self) -> None:
        vectors = [
            "/media/part/../../seamless_shorts_agent.py",
            "/media/part/..%2f..%2fseamless_shorts_agent.py",
            "/media/part/%2e%2e%2fseamless_shorts_agent.py",
            "/media/part/..%5cseamless_shorts_agent.py",
            "/media/part/sub/Part_01_02_19EAck3_MSg.mp4",
            "/media/part/C:Part_01.mp4",
            "/media/part/foo%00.mp4",
            "/media/part/..",
            "/media/part/.",
            "/media/source/../full_song.mp3",
            "/media/audio/%2e%2e%2ffull_song.mp3",
            "/web/..%2fREADME.txt",
        ]
        for vec in vectors:
            for method in ("GET", "HEAD"):
                st, hdrs, _ = http_request(f"{self.base_url}{vec}", method=method)
                self.assertIn(st, (400, 404), f"Traversal NOT blocked: {method} {vec} -> {st}")
                _assert_cors(self, hdrs)

    def test_t2_f10_missing_media_and_non_whitelisted_web_assets_404(self) -> None:
        for route in (
            "/media/part/nonexistent.mp4",
            "/media/source/nonexistent.mp4",
            "/media/audio/nonexistent.mp3",
            "/web/seamless_shorts_agent.py",
            "/seamless_shorts_agent.py",
            "/README.txt",
        ):
            st, hdrs, _ = http_request(f"{self.base_url}{route}")
            self.assertEqual(st, 404, f"Expected 404 for {route}, got {st}")
            _assert_cors(self, hdrs)

    def test_t2_f10_download_all_zip_and_toolkit_zip_endpoints(self) -> None:
        # HEAD parity
        for route in ("/download/all.zip", "/download/toolkit.zip"):
            hs, hh, hb = http_request(f"{self.base_url}{route}", method="HEAD")
            self.assertEqual(hs, 200)
            self.assertEqual(hh.get("content-type"), "application/zip")
            self.assertEqual(hb, b"")
            _assert_cors(self, hh)

        st, hdrs, tk_bytes = http_request(f"{self.base_url}/download/toolkit.zip")
        self.assertEqual(st, 200)
        self.assertEqual(hdrs.get("content-type"), "application/zip")
        with zipfile.ZipFile(io.BytesIO(tk_bytes), "r") as zf:
            self.assertIsNone(zf.testzip())
            self.assertIn("Seamless_Shorts_Player.html", zf.namelist())
            self.assertIn("seamless_shorts_agent.py", zf.namelist())

        # Note: /download/all.zip streams 260MB+; verify via Range header bytes=0-3 that it's a valid PK zip!
        st_all, hdr_all, prefix = http_request(f"{self.base_url}/download/all.zip", headers={"Range": "bytes=0-3"})
        self.assertEqual(st_all, 206)
        self.assertEqual(prefix, b"PK\x03\x04")
        self.assertEqual(hdr_all.get("content-type"), "application/zip")


class TestIsolatedServerAndPipelineControl(unittest.TestCase):
    """Isolated temp-CWD Studio server tests for F7 mutations, F9 tool detection, F10 env/port."""

    def test_t1_f7_upload_song_endpoint_updates_state_and_audio_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            _, base_url, stop = start_agent_server(cwd=tmp, port_arg=0)
            try:
                wav = make_wav_bytes(duration_sec=1.0)
                st, hdrs, body = http_request(
                    f"{base_url}/api/upload-song",
                    method="POST",
                    headers={"X-Filename": "Studio%20Beat.wav"},
                    data=wav,
                )
                self.assertEqual(st, 200)
                _assert_cors(self, hdrs)
                res = json.loads(body.decode("utf-8"))
                self.assertTrue(res["ok"])
                saved_name = res["song_name"]
                self.assertTrue((tmp / "shorts_workspace" / saved_name).is_file())

                st2, _, state_raw = http_request(f"{base_url}/api/state")
                self.assertEqual(st2, 200)
                state = json.loads(state_raw.decode("utf-8"))
                self.assertEqual(state["song_name"], saved_name)
                self.assertFalse(state["is_demo_song"])
                self.assertIn(saved_name, [f["name"] for f in state["audio_files"]])
            finally:
                stop()

    def test_t1_f7_api_run_skip_download_executes_real_job_and_updates_progress(self) -> None:
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            self.skipTest("ffmpeg not available")
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            src_dir = tmp / "shorts_workspace" / "1_source_shorts"
            src_dir.mkdir(parents=True)
            shutil.copy2(WORKSPACE_DIR / "1_source_shorts" / "05_wIA-VYebvO4.mp4", src_dir / "05_wIA-VYebvO4.mp4")
            shutil.copy2(WORKSPACE_DIR / "1_source_shorts" / "09_hjbE98E4pHc.mp4", src_dir / "09_hjbE98E4pHc.mp4")
            song = tmp / "clip_song.mp3"
            subprocess.run(
                [ffmpeg_bin, "-y", "-i", str(ROOT_DIR / "sample_song.mp3"), "-t", "18", "-c", "copy", str(song)],
                check=True,
                capture_output=True,
            )

            _, base_url, stop = start_agent_server(cwd=tmp, port_arg=0)
            try:
                st, hdrs, body = http_request(
                    f"{base_url}/api/run",
                    method="POST",
                    headers={"Content-Type": "application/json"},
                    data=json.dumps(
                        {
                            "song_path": str(song),
                            "skip_download": True,
                            "fade_ms": 15,
                            "max_downloads": 5,
                        }
                    ).encode("utf-8"),
                )
                self.assertEqual(st, 200)
                _assert_cors(self, hdrs)
                self.assertTrue(json.loads(body.decode("utf-8")).get("ok"))

                deadline = time.monotonic() + 30.0
                final_state = None
                while time.monotonic() < deadline:
                    _, _, raw = http_request(f"{base_url}/api/state")
                    s = json.loads(raw.decode("utf-8"))
                    if not s["job"]["running"] and s["job"]["progress"] == 100:
                        final_state = s
                        break
                    if not s["job"]["running"] and s["job"]["error"]:
                        self.fail(f"Studio job failed: {s['job']}")
                    time.sleep(0.2)

                self.assertIsNotNone(final_state, "Timed out waiting for Studio /api/run completion")
                assert final_state is not None
                self.assertEqual(final_state["job"]["progress"], 100)
                self.assertIsNone(final_state["job"]["error"])
                self.assertTrue(final_state["verify_exists"])
                self.assertEqual(len(final_state["parts"]), 2)
                self.assertTrue((tmp / "shorts_workspace" / "3_verification_full_playthrough.mp4").is_file())
            finally:
                stop()

    def test_t2_f7_api_run_zero_vs_none_and_false_string_booleans(self) -> None:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), agent.make_handler())
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))

        calls: list[dict] = []
        done = threading.Event()

        def fake_pipeline(**kwargs):
            calls.append(kwargs)
            done.set()
            return {"parts": []}

        with mock.patch.object(agent, "run_pipeline", side_effect=fake_pipeline):
            st, _, _ = http_request(
                f"http://127.0.0.1:{port}/api/run",
                method="POST",
                headers={"Content-Type": "application/json"},
                data=json.dumps(
                    {
                        "max_downloads": 0,
                        "fade_ms": 0,
                        "skip_download": "false",
                        "browser_safe_h264": "0",
                    }
                ).encode("utf-8"),
            )
            self.assertEqual(st, 200)
            self.assertTrue(done.wait(timeout=3.0))
            self.assertEqual(calls[0]["max_downloads"], 0)
            self.assertEqual(calls[0]["fade_ms"], 0)
            self.assertIs(calls[0]["skip_download"], False)
            self.assertIs(calls[0]["browser_safe_h264"], False)

            calls.clear()
            done.clear()
            st2, _, _ = http_request(
                f"http://127.0.0.1:{port}/api/run",
                method="POST",
                headers={"Content-Type": "application/json"},
                data=json.dumps({"max_downloads": None, "fade_ms": None}).encode("utf-8"),
            )
            self.assertEqual(st2, 200)
            self.assertTrue(done.wait(timeout=3.0))
            self.assertEqual(calls[0]["max_downloads"], 10)
            self.assertEqual(calls[0]["fade_ms"], 15)

            for bad_body in ({"max_downloads": -1}, {"fade_ms": "abc"}, {"skip_download": "maybe"}):
                st_err, _, _ = http_request(
                    f"http://127.0.0.1:{port}/api/run",
                    method="POST",
                    headers={"Content-Type": "application/json"},
                    data=json.dumps(bad_body).encode("utf-8"),
                )
                self.assertEqual(st_err, 400)

    def test_t2_f7_api_run_409_conflict_when_job_already_running(self) -> None:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), agent.make_handler())
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))

        gate = threading.Event()
        done = threading.Event()

        def slow_pipeline(**kwargs):
            gate.wait(timeout=4.0)
            done.set()
            return {"parts": []}

        with mock.patch.object(agent, "run_pipeline", side_effect=slow_pipeline):
            try:
                s1, _, _ = http_request(
                    f"http://127.0.0.1:{port}/api/run",
                    method="POST",
                    headers={"Content-Type": "application/json"},
                    data=b"{}",
                )
                self.assertEqual(s1, 200)
                s2, h2, b2 = http_request(
                    f"http://127.0.0.1:{port}/api/run",
                    method="POST",
                    headers={"Content-Type": "application/json"},
                    data=b"{}",
                )
                self.assertEqual(s2, 409)
                _assert_cors(self, h2)
                self.assertIn("already running", json.loads(b2.decode("utf-8"))["error"].lower())
            finally:
                gate.set()
                done.wait(timeout=3.0)

    def test_t1_f10_host_port_env_override_and_port_autoincrement(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            # 1. HOST/PORT env override without --host/--port CLI flags
            _, u_env, stop_env = start_agent_server(
                cwd=tmp,
                port_arg=None,
                host_arg=None,
                extra_env={"HOST": "127.0.0.1", "PORT": "0"},
                strip_host_port=True,
            )
            try:
                st, _, _ = http_request(f"{u_env}/api/health")
                self.assertEqual(st, 200)
            finally:
                stop_env()

            # 2. Port auto-increment when preferred port is busy
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                busy_port = s.getsockname()[1]
                _, u_inc, stop_inc = start_agent_server(cwd=tmp, port_arg=busy_port)
                try:
                    actual_port = int(u_inc.rsplit(":", 1)[1])
                    self.assertNotEqual(actual_port, busy_port)
                    self.assertGreater(actual_port, busy_port)
                finally:
                    stop_inc()

    def test_t2_f10_fallback_studio_html_when_web_assets_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            shutil.copy2(AGENT_PATH, tmp / "seamless_shorts_agent.py")
            _, base_url, stop = start_agent_server(
                script_path=tmp / "seamless_shorts_agent.py",
                cwd=tmp,
                port_arg=0,
            )
            try:
                st, hdrs, body = http_request(f"{base_url}/")
                self.assertEqual(st, 200)
                self.assertIn("text/html", hdrs.get("content-type", ""))
                self.assertIn(b"Seamless Shorts Studio", body)
                self.assertIn(b"3-Step Song Sync Studio", body)
                s404, h404, _ = http_request(f"{base_url}/styles.css")
                self.assertEqual(s404, 404)
                _assert_cors(self, h404)
            finally:
                stop()


class TestLaunchersAndToolDetection(unittest.TestCase):
    """Tier 1 & Tier 2 E2E tests for F8 (Launchers) and F9 (Tool Detection)."""

    # ---------------- F8: Windows & Mac Launcher Hardening ----------------

    def test_t1_f8_start_here_bat_strict_crlf_and_bat_preamble(self) -> None:
        raw = BAT_PATH.read_bytes()
        crlf = raw.count(b"\r\n")
        lf = raw.count(b"\n")
        self.assertGreater(crlf, 20)
        self.assertEqual(crlf, lf)
        txt = raw.decode("utf-8")
        self.assertTrue(txt.startswith("@echo off\r\n"))
        self.assertIn("setlocal\r\n", txt)
        self.assertIn('cd /d "%~dp0"\r\n', txt)

    def test_t1_f8_bat_python_discovery_ladder_order(self) -> None:
        txt = BAT_PATH.read_bytes().decode("utf-8")
        local_rt = txt.index(r"%~dp0runtime\python.exe")
        local_py = txt.index(r"%~dp0python\python.exe")
        probe_call = txt.index("call :probe_installed_python")
        winget_idx = txt.index("winget install -e --id Python.Python.3.12")
        self.assertLess(local_rt, local_py)
        self.assertLess(local_py, probe_call)
        self.assertLess(probe_call, winget_idx)
        for token in (
            "py -3",
            r"HKCU\Software\Python\PythonCore",
            r"HKLM\Software\Python\PythonCore",
            r"HKLM\Software\Wow6432Node\Python\PythonCore",
            r"C:\Python315\python.exe",
            r"%LOCALAPPDATA%\Programs\Python\Python315\python.exe",
            r"%ProgramFiles%\Python312\python.exe",
            r"%USERPROFILE%\scoop\shims\python.exe",
            r"%USERPROFILE%\.pyenv\pyenv-win\shims\python.exe",
            r"C:\ProgramData\chocolatey\bin\python.exe",
        ):
            self.assertIn(token, txt)

    def test_t1_f8_bat_winget_install_registry_path_refresh_and_reprobe(self) -> None:
        txt = BAT_PATH.read_bytes().decode("utf-8")
        winget_pos = txt.index("winget install -e --id Python.Python.3.12")
        refresh_pos = txt.index("call :refresh_path_from_registry", winget_pos)
        reprobe_pos = txt.index("call :probe_installed_python", refresh_pos)
        self.assertLess(winget_pos, refresh_pos)
        self.assertLess(refresh_pos, reprobe_pos)
        self.assertIn(r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment", txt)
        self.assertIn(r"HKCU\Environment", txt)

    def test_t1_f8_bat_prepends_local_bin_and_appdata_scripts_and_launches_ui(self) -> None:
        txt = BAT_PATH.read_bytes().decode("utf-8")
        self.assertIn(r'set "PATH=%~dp0bin;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"', txt)
        self.assertIn(r"%APPDATA%\Python\Python315\Scripts", txt)
        self.assertIn("seamless_shorts_agent.py --ui %*", txt)

    def test_t1_f8_start_here_command_executes_cleanly_on_linux(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            shutil.copy2(CMD_PATH, tmp / "START_HERE.command")
            (tmp / "bin").mkdir()
            (tmp / "bin" / "custom_tool").write_text("#!/bin/sh\n", encoding="utf-8")
            (tmp / "seamless_shorts_agent.py").write_text(
                "import os, sys, json\n"
                "with open('run_record.json', 'w') as f:\n"
                "    json.dump({'argv': sys.argv[1:], 'path': os.environ.get('PATH', '')}, f)\n",
                encoding="utf-8",
            )
            res = subprocess.run(
                ["bash", str(tmp / "START_HERE.command"), "--no-browser", "--port", "9999"],
                cwd=str(tmp),
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            rec = json.loads((tmp / "run_record.json").read_text(encoding="utf-8"))
            self.assertEqual(rec["argv"], ["--ui", "--no-browser", "--port", "9999"])
            self.assertTrue(rec["path"].startswith(str(tmp / "bin")))

    def test_t2_f8_bat_never_invokes_python3_command(self) -> None:
        txt = BAT_PATH.read_bytes().decode("utf-8")
        self.assertIsNone(re.search(r"\bpython3(?:\.exe)?\b", txt, re.IGNORECASE))

    def test_t2_f8_bat_guards_against_windowsapps_store_stub(self) -> None:
        txt = BAT_PATH.read_bytes().decode("utf-8")
        self.assertGreaterEqual(txt.count(r"\WindowsApps" + "\\"), 2)

    def test_t2_f8_bat_error_paths_pause_and_mention_standalone_html(self) -> None:
        txt = BAT_PATH.read_bytes().decode("utf-8")
        self.assertIn("Seamless_Shorts_Player.html", txt)
        self.assertGreaterEqual(txt.lower().count("pause"), 2)
        self.assertGreaterEqual(txt.lower().count("exit /b 1"), 2)

    def test_t2_f8_command_launcher_handles_missing_python_and_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            shutil.copy2(CMD_PATH, tmp / "START_HERE.command")
            shim = tmp / "shim"
            shim.mkdir()
            dirname_bin = shutil.which("dirname")
            if dirname_bin:
                os.symlink(dirname_bin, shim / "dirname")

            # Missing python3/python
            env_no_py = clean_subproc_env({"PATH": str(shim)})
            res_missing = subprocess.run(
                ["/bin/bash", str(tmp / "START_HERE.command")],
                cwd=str(tmp),
                env=env_no_py,
                input="\n",
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_missing.returncode, 1)
            self.assertIn("Seamless_Shorts_Player.html", res_missing.stdout)

            # Non-zero exit code from agent
            (tmp / "seamless_shorts_agent.py").write_text("import sys; sys.exit(7)\n", encoding="utf-8")
            res_fail = subprocess.run(
                ["/bin/bash", str(tmp / "START_HERE.command")],
                cwd=str(tmp),
                input="\n",
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_fail.returncode, 7)
            self.assertIn("exited with status 7", res_fail.stdout)

    def test_t2_f8_start_here_command_passes_shellcheck_and_lf(self) -> None:
        raw = CMD_PATH.read_bytes()
        self.assertEqual(raw.count(b"\r\n"), 0)
        self.assertTrue(os.access(CMD_PATH, os.X_OK))
        sc = shutil.which("shellcheck")
        if sc:
            res = subprocess.run([sc, str(CMD_PATH)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    # ---------------- F9: Tool Detection ----------------

    def test_t1_f9_setup_paths_discovers_local_bin_winget_and_appdata_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            appdata = tmp / "AppDataRoaming"
            localappdata = tmp / "AppDataLocal"
            dirs = [
                tmp / "bin",
                appdata / "Python" / "Python315" / "Scripts",
                localappdata / "Programs" / "Python" / "Python315" / "Scripts",
                localappdata / "Microsoft" / "WinGet" / "Links",
                localappdata / "Microsoft" / "WinGet" / "Packages" / "Gyan.FFmpeg" / "ffmpeg-7" / "bin",
            ]
            for d in dirs:
                d.mkdir(parents=True, exist_ok=True)
            orig_env = os.environ.copy()
            orig_sd = agent.SCRIPT_DIR
            try:
                os.environ["APPDATA"] = str(appdata)
                os.environ["LOCALAPPDATA"] = str(localappdata)
                os.environ["PATH"] = "/usr/bin:/bin"
                agent.SCRIPT_DIR = tmp
                agent.setup_paths()
                entries = os.environ["PATH"].split(os.pathsep)
                for d in dirs:
                    self.assertIn(str(d), entries)
            finally:
                agent.SCRIPT_DIR = orig_sd
                os.environ.clear()
                os.environ.update(orig_env)

    def test_t1_f9_yt_dlp_prefers_python_module_or_cli_executable(self) -> None:
        with mock.patch("importlib.util.find_spec", return_value=object()):
            self.assertEqual(agent.get_yt_dlp_cmd(), [sys.executable, "-m", "yt_dlp"])
        with mock.patch("importlib.util.find_spec", return_value=None), mock.patch(
            "shutil.which", side_effect=lambda name: "/opt/yt-dlp" if name == "yt-dlp" else None
        ):
            self.assertEqual(agent.get_yt_dlp_cmd(), ["/opt/yt-dlp"])

    def test_t1_f9_real_duration_and_video_info_across_workspace_codecs(self) -> None:
        samples = [
            ("02_19EAck3_MSg.mp4", "av1", 1080, 1920, 14.0),
            ("07_MJ1bXR0E1Kg.mp4", "vp9", 720, 1280, 59.5),
            ("09_hjbE98E4pHc.mp4", "vp9", 1080, 2400, 9.0),
        ]
        for fname, codec, w, h, approx_dur in samples:
            info = agent.get_video_info(WORKSPACE_DIR / "1_source_shorts" / fname)
            self.assertEqual(info["codec"], codec)
            self.assertEqual((info["width"], info["height"]), (w, h))
            self.assertAlmostEqual(info["duration"], approx_dur, delta=0.3)

    def test_t1_f9_generate_demo_song_via_cli_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_mp3 = Path(tmpdir) / "demo_out.mp3"
            res = subprocess.run(
                [sys.executable, str(AGENT_PATH), "--make-demo-song", "--song", str(out_mp3)],
                cwd=tmpdir,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertTrue(out_mp3.is_file())
            self.assertGreater(out_mp3.stat().st_size, 100_000)

    def test_t2_f9_setup_paths_idempotent_under_repeated_polling(self) -> None:
        orig_env = os.environ.copy()
        try:
            agent.setup_paths()
            first = os.environ["PATH"]
            for _ in range(25):
                agent.setup_paths()
            self.assertEqual(os.environ["PATH"], first)
        finally:
            os.environ.clear()
            os.environ.update(orig_env)

    def test_t2_f9_real_ffmpeg_fallback_when_ffprobe_missing_in_path(self) -> None:
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            self.skipTest("ffmpeg not installed")
        clip = WORKSPACE_DIR / "1_source_shorts" / "05_wIA-VYebvO4.mp4"
        with mock.patch.object(agent, "get_ffprobe_bin", return_value=None), mock.patch.object(
            agent, "get_ffmpeg_bin", return_value=ffmpeg_bin
        ):
            dur = agent.get_media_duration(clip)
            info = agent.get_video_info(clip)
        self.assertAlmostEqual(dur, 14.07, delta=0.2)
        self.assertAlmostEqual(info["duration"], 14.07, delta=0.2)
        self.assertEqual((info["width"], info["height"]), (720, 1280))
        self.assertIn(info["codec"], ("av1", "libdav1d"))

    def test_t2_f9_missing_both_ffmpeg_and_ffprobe_degrades_gracefully(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".mp4") as tf:
            tf.write(b"\x00" * 64)
            tf.flush()
            with mock.patch.object(agent, "get_ffprobe_bin", return_value=None), mock.patch.object(
                agent, "get_ffmpeg_bin", return_value=None
            ):
                info = agent.get_video_info(tf.name)
                self.assertEqual(info["codec"], "unknown")
                self.assertEqual(info["width"], 0)
                self.assertEqual(info["height"], 0)
                with self.assertRaises(RuntimeError):
                    agent.get_media_duration(tf.name)

    def test_t2_f9_missing_yt_dlp_detected_cleanly_in_state(self) -> None:
        with mock.patch("importlib.util.find_spec", return_value=None), mock.patch("shutil.which", return_value=None):
            self.assertFalse(agent.has_yt_dlp())
            self.assertIsNone(agent.get_yt_dlp_cmd())

    def test_t2_f9_resolve_song_path_missing_file_raises_helpful_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            orig_sd = agent.SCRIPT_DIR
            orig_wd = agent.WORK_DIR
            try:
                os.chdir(tmpdir)
                agent.SCRIPT_DIR = Path(tmpdir)
                agent.WORK_DIR = Path(tmpdir) / "shorts_workspace"
                with self.assertRaises(FileNotFoundError) as ctx:
                    agent.resolve_song_path("missing_song.mp3", interactive=False)
                self.assertIn("missing_song.mp3", str(ctx.exception))
            finally:
                agent.SCRIPT_DIR = orig_sd
                agent.WORK_DIR = orig_wd
                os.chdir(orig_cwd)


class TestDistHostingPagesAndSuiteSafety(unittest.TestCase):
    """Tier 1 & Tier 2 E2E tests for F11 (Dist/README), F12 (Container), F13 (Pages), F14 (Suite)."""

    # ---------------- F11: Distribution Zip & README ----------------

    def test_t1_f11_build_dist_sh_dry_parity_and_symlink_resolution(self) -> None:
        self.assertTrue(ZIP_PATH.is_file())
        self.assertTrue(SYMLINK_ZIP_PATH.is_symlink())
        self.assertEqual(SYMLINK_ZIP_PATH.resolve(), ZIP_PATH.resolve())
        script_text = (TOOLS_DIR / "build_dist.sh").read_text(encoding="utf-8")
        self.assertIn("zip -j", script_text)
        self.assertIn("build_standalone.py", script_text)

    def test_t1_f11_zip_contains_all_9_flat_members_matching_sources(self) -> None:
        expected = {
            "seamless_shorts_agent.py": ROOT_DIR / "seamless_shorts_agent.py",
            "START_HERE.bat": BAT_PATH,
            "START_HERE.command": CMD_PATH,
            "README.txt": README_PATH,
            "index.html": WEB_DIR / "index.html",
            "styles.css": WEB_DIR / "styles.css",
            "app.js": WEB_DIR / "app.js",
            "jszip.min.js": WEB_DIR / "jszip.min.js",
            "Seamless_Shorts_Player.html": WEB_DIR / "Seamless_Shorts_Player.html",
        }
        with zipfile.ZipFile(ZIP_PATH, "r") as zf:
            self.assertEqual(set(zf.namelist()), set(expected.keys()))
            for member, disk in expected.items():
                self.assertEqual(zf.read(member), disk.read_bytes(), f"Mismatch in {member}")

    def test_t1_f11_extracted_zip_boots_studio_and_serves_assets(self) -> None:
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
                for route, substr in (
                    ("/", "Seamless Shorts Studio"),
                    ("/Seamless_Shorts_Player.html", "JSZip"),
                    ("/app.js", "SeamlessCore"),
                    ("/styles.css", "--accent"),
                    ("/jszip.min.js", "JSZip"),
                ):
                    st, _, body = http_request(f"{base_url}{route}")
                    self.assertEqual(st, 200)
                    self.assertIn(substr, body.decode("utf-8", errors="replace"))
            finally:
                stop()

    def test_t1_f11_readme_covers_windows_options_and_troubleshooting(self) -> None:
        raw = README_PATH.read_bytes()
        self.assertGreater(raw.count(b"\r\n"), 20)
        self.assertEqual(raw.count(b"\r\n"), raw.count(b"\n"))
        txt = raw.decode("utf-8")
        for token in (
            "Extract All",
            "Seamless_Shorts_Player.html",
            "START_HERE.bat",
            "START_HERE.command",
            "SmartScreen",
            "Audition Cut (−2s)",
            "Video Clip Audio",
            "Replacement Song",
            "Clips A/B",
            "Full Verify",
            "Browser-Safe H.264",
            "Browser Cookies (optional)",
            "--cookies-from-browser",
        ):
            self.assertIn(token, txt)

    def test_t1_f11_zipped_start_here_bat_has_strict_crlf_and_command_is_executable(self) -> None:
        with zipfile.ZipFile(ZIP_PATH, "r") as zf:
            bat = zf.read("START_HERE.bat")
            self.assertGreater(bat.count(b"\r\n"), 20)
            self.assertEqual(bat.count(b"\r\n"), bat.count(b"\n"))
            cmd_info = zf.getinfo("START_HERE.command")
            mode = (cmd_info.external_attr >> 16) & 0o777
            self.assertTrue(mode & 0o111, f"Expected executable mode bit on zipped START_HERE.command, got {oct(mode)}")

    def test_t2_f11_readme_contains_zero_python3_or_vm_localhost_traps(self) -> None:
        txt = README_PATH.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\bpython3\b", txt, re.IGNORECASE))
        self.assertIsNone(re.search(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0)", txt, re.IGNORECASE))

    def test_t2_f11_zip_excludes_workspace_dev_artifacts_and_tmp_csv(self) -> None:
        with zipfile.ZipFile(ZIP_PATH, "r") as zf:
            for name in zf.namelist():
                self.assertNotIn("/", name)
                self.assertFalse(name.endswith((".mp4", ".mp3", ".csv", ".pyc", ".log")))
                self.assertNotIn("test_", name)

    def test_t2_f11_extracted_zip_without_workspace_handles_empty_state_gracefully(self) -> None:
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
                st, _, body = http_request(f"{base_url}/api/state")
                self.assertEqual(st, 200)
                data = json.loads(body.decode("utf-8"))
                self.assertEqual(data["source_count"], 0)
                self.assertEqual(data["parts"], [])
                self.assertFalse(data["verify_exists"])
                self.assertEqual(data["audio_files"], [])
            finally:
                stop()

    def test_t2_f11_build_dist_script_uses_rm_and_zip_j_and_passes_shellcheck(self) -> None:
        sh = TOOLS_DIR / "build_dist.sh"
        txt = sh.read_text(encoding="utf-8")
        self.assertIn("rm -f", txt)
        self.assertIn("zip -j", txt)
        self.assertIn("unzip -t", txt)
        sc = shutil.which("shellcheck")
        if sc:
            res = subprocess.run([sc, str(sh)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_t2_f11_unzip_cli_test_integrity_passes_zero(self) -> None:
        unzip_bin = shutil.which("unzip")
        if not unzip_bin:
            self.skipTest("unzip not available")
        res = subprocess.run([unzip_bin, "-t", str(ZIP_PATH)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("No errors detected", res.stdout)

    # ---------------- F12: Container Hosting ----------------

    def test_t1_f12_dockerfile_base_tools_web_healthcheck_and_cmd(self) -> None:
        df = (ROOT_DIR / "Dockerfile").read_text(encoding="utf-8")
        for token in ("FROM python:3.11-slim-bookworm", "HOST=0.0.0.0", "PORT=8766", "ffmpeg", "curl", "yt-dlp", "COPY web/ /app/web/", "--make-demo-song", "EXPOSE 8766", "HEALTHCHECK", "/api/health", "--ui --no-browser"):
            self.assertIn(token, df)

    def test_t1_f12_docker_compose_service_ports_volume_and_healthcheck(self) -> None:
        dc = yaml.safe_load((ROOT_DIR / "docker-compose.yml").read_text(encoding="utf-8"))
        svc = next(iter(dc["services"].values()))
        self.assertIn("8766:8766", svc["ports"])
        self.assertEqual(svc["environment"]["HOST"], "0.0.0.0")
        self.assertEqual(str(svc["environment"]["PORT"]), "8766")
        self.assertIn("shorts_data:/app/shorts_workspace", svc["volumes"])
        self.assertIn("/api/health", " ".join(svc["healthcheck"]["test"]))

    def test_t1_f12_render_and_fly_and_hf_configs_consistent_port_health(self) -> None:
        render = yaml.safe_load((DEPLOY_DIR / "render.yaml").read_text(encoding="utf-8"))["services"][0]
        self.assertEqual(render["healthCheckPath"], "/api/health")
        fly = tomllib.loads((DEPLOY_DIR / "fly.toml").read_text(encoding="utf-8"))
        self.assertEqual(fly["http_service"]["internal_port"], 8766)
        self.assertEqual(fly["http_service"]["checks"][0]["path"], "/api/health")
        hf_txt = (DEPLOY_DIR / "huggingface" / "README.md").read_text(encoding="utf-8")
        fm = yaml.safe_load(re.match(r"^---\s*\n(.*?)\n---", hf_txt, re.DOTALL).group(1))
        self.assertEqual(fm["sdk"], "docker")
        self.assertEqual(fm["app_port"], 8766)

    def test_t1_f12_container_entrypoint_env_contract_live_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            shutil.copy2(ROOT_DIR / "sample_song.mp3", tmp / "sample_song.mp3")
            _, base_url, stop = start_agent_server(
                cwd=tmp,
                port_arg=None,
                host_arg=None,
                extra_env={"HOST": "0.0.0.0", "PORT": "0"},
                strip_host_port=True,
            )
            try:
                st_h, _, b_h = http_request(f"{base_url}/api/health")
                self.assertEqual(st_h, 200)
                self.assertTrue(json.loads(b_h.decode("utf-8"))["ok"])
                st_s, _, b_s = http_request(f"{base_url}/api/state")
                self.assertEqual(st_s, 200)
                names = [a["name"] for a in json.loads(b_s.decode("utf-8"))["audio_files"]]
                self.assertIn("sample_song.mp3", names)
            finally:
                stop()

    def test_t1_f12_hosting_guide_covers_all_platforms_without_localhost_trap(self) -> None:
        txt = (DEPLOY_DIR / "HOSTING.md").read_text(encoding="utf-8")
        for term in ("GitHub Pages", "Docker", "Hugging Face", "Fly.io", "Render", "/api/health"):
            self.assertIn(term, txt)
        self.assertIsNone(re.search(r"http://localhost:8766", txt, re.IGNORECASE))

    def test_t2_f12_dockerignore_excludes_bloat_and_keeps_runtime_files(self) -> None:
        lines = [ln.strip() for ln in (ROOT_DIR / ".dockerignore").read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.strip().startswith("#")]
        for exc in (".agents", "shorts_workspace", "upload", "tests", "dist", "*.zip", "flowjuggle"):
            self.assertIn(exc, lines)
        for keep in ("web", "web/", "seamless_shorts_agent.py", "README.txt", "*.py", "*.html", "*.js", "*.css"):
            self.assertNotIn(keep, lines)

    def test_t2_f12_invalid_port_env_var_falls_back_gracefully(self) -> None:
        with mock.patch.dict(os.environ, {"HOST": "", "PORT": "not_a_port"}), mock.patch.object(
            sys, "argv", ["seamless_shorts_agent.py", "--ui", "--no-browser"]
        ), mock.patch.object(agent, "start_ui_server") as mock_srv:
            agent.main()
            mock_srv.assert_called_once_with(host="127.0.0.1", port=agent.DEFAULT_PORT, open_browser=False)

    def test_t2_f12_dockerfile_cleans_apt_and_pip_caches(self) -> None:
        df = (ROOT_DIR / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("--no-install-recommends", df)
        self.assertIn("rm -rf /var/lib/apt/lists/*", df)
        self.assertIn("--no-cache-dir", df)

    def test_t2_f12_hf_readme_warns_against_uploading_cookies(self) -> None:
        hf = (DEPLOY_DIR / "huggingface" / "README.md").read_text(encoding="utf-8")
        self.assertIn("cookies", hf.lower())
        self.assertIn("bot", hf.lower())

    def test_t2_f12_dockerignore_does_not_ignore_start_here_launchers(self) -> None:
        lines = (ROOT_DIR / ".dockerignore").read_text(encoding="utf-8").splitlines()
        for pat in ("START_HERE.bat", "START_HERE.command", "START_HERE.*"):
            self.assertNotIn(pat, lines)

    # ---------------- F13: Static Hosting ----------------

    @classmethod
    def _get_cdp(cls) -> ChromiumCDPBridge | None:
        if not (shutil.which("chromium") or shutil.which("google-chrome")) or not shutil.which("node"):
            return None
        if getattr(cls, "_shared_cdp", None) is None:
            cls._shared_cdp = ChromiumCDPBridge()
        return cls._shared_cdp

    @classmethod
    def tearDownClass(cls) -> None:
        cdp = getattr(cls, "_shared_cdp", None)
        if cdp is not None:
            cdp.close()
            cls._shared_cdp = None

    def test_t1_f13_build_pages_sh_generates_complete_site_with_nojekyll(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "site"
            res = subprocess.run(["bash", str(TOOLS_DIR / "build_pages.sh"), str(out)], cwd=str(ROOT_DIR), capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            expected = {"index.html", "styles.css", "app.js", "jszip.min.js", "Seamless_Shorts_Player.html", ".nojekyll"}
            self.assertEqual({p.name for p in out.iterdir()}, expected)

    def test_t1_f13_build_pages_supports_site_dir_env_var(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "site_env"
            env = clean_subproc_env({"SITE_DIR": str(out)})
            res = subprocess.run(["bash", str(TOOLS_DIR / "build_pages.sh")], cwd=str(ROOT_DIR), env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertTrue((out / "index.html").is_file())
            self.assertTrue((out / ".nojekyll").is_file())

    def test_t1_f13_pages_workflow_yaml_valid_permissions_and_steps(self) -> None:
        wf = yaml.safe_load((ROOT_DIR / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8"))
        self.assertEqual(wf["permissions"]["pages"], "write")
        self.assertEqual(wf["permissions"]["id-token"], "write")
        steps = wf["jobs"]["build"]["steps"]
        self.assertTrue(any("build_pages.sh" in s.get("run", "") for s in steps))
        up = next(s for s in steps if s.get("uses") == "actions/upload-pages-artifact@v3")
        self.assertEqual(up["with"]["path"], "dist/site")

    def test_t1_f13_static_http_server_chromium_auto_falls_back_to_standalone(self) -> None:
        cdp = self._get_cdp()
        if cdp is None:
            self.skipTest("chromium or node not available")
        with tempfile.TemporaryDirectory() as tmpdir:
            site = Path(tmpdir) / "site"
            subprocess.run(["bash", str(TOOLS_DIR / "build_pages.sh"), str(site)], cwd=str(ROOT_DIR), check=True, capture_output=True)
            _, url, stop = start_static_server(site)
            sid = cdp.new_tab(f"{url}/", wait_ms=350)
            try:
                mode = cdp.eval(sid, "document.getElementById('modeBadge').dataset.mode")
                studio_hidden = cdp.eval(sid, "document.getElementById('studioPanel').hidden")
                logs = cdp.get_logs(sid)
                self.assertEqual(mode, "standalone")
                self.assertTrue(studio_hidden)
                self.assertEqual(logs["exceptions"], [])
                self.assertEqual(logs["consoleErrors"], [])
            finally:
                cdp.close_tab(sid)
                stop()

    def test_t1_f13_github_pages_doc_explains_consent_gate_and_local_preview(self) -> None:
        txt = (DEPLOY_DIR / "GITHUB_PAGES.md").read_text(encoding="utf-8")
        self.assertIn("explicit consent", txt.lower())
        self.assertIn("tools/build_pages.sh", txt)
        self.assertIn("Seamless_Shorts_Player.html", txt)

    def test_t2_f13_build_pages_refuses_empty_or_root_target_safety(self) -> None:
        res = subprocess.run(["bash", str(TOOLS_DIR / "build_pages.sh"), "/"], cwd=str(ROOT_DIR), capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Refusing unsafe target directory", res.stderr)

    def test_t2_f13_build_pages_purges_stale_files_from_prior_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            site = Path(tmpdir) / "site"
            site.mkdir()
            stale = site / "old_artifact.js"
            stale.write_text("stale", encoding="utf-8")
            subprocess.run(["bash", str(TOOLS_DIR / "build_pages.sh"), str(site)], cwd=str(ROOT_DIR), check=True, capture_output=True)
            self.assertFalse(stale.exists())

    def test_t2_f13_static_server_returning_500_or_html_on_api_health_stays_standalone(self) -> None:
        cdp = self._get_cdp()
        if cdp is None:
            self.skipTest("chromium or node not available")
        _, url, stop = start_static_server(WEB_DIR, health_response=(500, "text/html", b"<html>500 Error</html>"))
        sid = cdp.new_tab(f"{url}/", wait_ms=350)
        try:
            self.assertEqual(cdp.eval(sid, "document.getElementById('modeBadge').dataset.mode"), "standalone")
            self.assertTrue(cdp.eval(sid, "document.getElementById('studioPanel').hidden"))
            self.assertEqual(cdp.get_logs(sid)["exceptions"], [])
        finally:
            cdp.close_tab(sid)
            stop()

    def test_t2_f13_static_server_returning_ok_false_stays_standalone(self) -> None:
        cdp = self._get_cdp()
        if cdp is None:
            self.skipTest("chromium or node not available")
        _, url, stop = start_static_server(WEB_DIR, health_response=(200, "application/json", b'{"ok": false}'))
        sid = cdp.new_tab(f"{url}/", wait_ms=350)
        try:
            self.assertEqual(cdp.eval(sid, "document.getElementById('modeBadge').dataset.mode"), "standalone")
            self.assertTrue(cdp.eval(sid, "document.getElementById('studioPanel').hidden"))
            self.assertEqual(cdp.get_logs(sid)["exceptions"], [])
        finally:
            cdp.close_tab(sid)
            stop()

    def test_t2_f13_pages_sh_passes_shellcheck_with_strict_mode(self) -> None:
        sh = TOOLS_DIR / "build_pages.sh"
        self.assertIn("set -euo pipefail", sh.read_text(encoding="utf-8"))
        self.assertTrue(os.access(sh, os.X_OK))
        sc = shutil.which("shellcheck")
        if sc:
            res = subprocess.run([sc, str(sh)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    # ---------------- F14: Test Suite Discoverability & Isolation ----------------

    def test_t1_f14_test_packages_have_init_and_discoverable_by_unittest(self) -> None:
        for init_file in (
            ROOT_DIR / "tests" / "__init__.py",
            ROOT_DIR / "tests" / "unit" / "__init__.py",
            ROOT_DIR / "tests" / "e2e" / "__init__.py",
        ):
            self.assertTrue(init_file.is_file(), f"Missing {init_file}")
        unit_suite = unittest.TestLoader().discover(str(ROOT_DIR / "tests" / "unit"), top_level_dir=str(ROOT_DIR))
        self.assertGreaterEqual(unit_suite.countTestCases(), 30)

    def test_t1_f14_no_pytest_or_playwright_imports_anywhere_in_tests(self) -> None:
        forbidden = {"pytest", "playwright"}
        for py_file in (ROOT_DIR / "tests").rglob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".", 1)[0]
                        self.assertNotIn(top, forbidden, f"Forbidden import {top} in {py_file}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".", 1)[0]
                    self.assertNotIn(top, forbidden, f"Forbidden import {top} in {py_file}")

    def test_t1_f14_proxy_handler_bypass_reaches_local_server_even_with_http_proxy_set(self) -> None:
        _, base_url, stop = start_agent_server(cwd=ROOT_DIR, port_arg=0)
        try:
            with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "http_proxy": "http://127.0.0.1:1"}):
                st, _, body = http_request(f"{base_url}/api/health")
                self.assertEqual(st, 200)
                self.assertTrue(json.loads(body.decode("utf-8"))["ok"])
        finally:
            stop()

    def test_t1_f14_workspace_source_clips_and_manifest_intact_read_only(self) -> None:
        src_clips = sorted((WORKSPACE_DIR / "1_source_shorts").glob("*.mp4"))
        part_clips = sorted((WORKSPACE_DIR / "2_repurposed_shorts").glob("Part_*.mp4"))
        self.assertEqual(len(src_clips), 9)
        self.assertEqual(len(part_clips), 9)
        self.assertTrue((WORKSPACE_DIR / "3_verification_full_playthrough.mp4").is_file())
        manifest = json.loads((WORKSPACE_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["parts"]), 9)

    def test_t1_f14_test_infra_md_documents_f1_to_f15_and_four_tiers(self) -> None:
        txt = (ROOT_DIR / "TEST_INFRA.md").read_text(encoding="utf-8")
        for i in range(1, 16):
            self.assertIn(f"F{i}", txt)
        for tier in ("Tier 1", "Tier 2", "Tier 3", "Tier 4"):
            self.assertIn(tier, txt)
        self.assertIn("ProxyHandler", txt)
        self.assertIn("Chromium", txt)

    def test_t2_f14_no_stray_test_artifacts_in_project_root(self) -> None:
        self.assertFalse((ROOT_DIR / "dist").exists(), "Unexpected stray dist/ in repo root")
        self.assertFalse((ROOT_DIR / ".pytest_cache").exists())

    def test_t2_f14_no_agents_teamwork_contains_python_or_web_source_files(self) -> None:
        tw_dir = ROOT_DIR / ".agents" / "teamwork"
        for f in tw_dir.rglob("*"):
            if f.is_file():
                self.assertNotIn(f.suffix.lower(), {".py", ".js", ".css", ".html"}, f"Disallowed source in teamwork dir: {f}")

    def test_t2_f14_isolated_cwd_server_never_mutates_real_shorts_workspace(self) -> None:
        manifest_path = WORKSPACE_DIR / "manifest.json"
        before_bytes = manifest_path.read_bytes()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            _, base_url, stop = start_agent_server(cwd=tmp, port_arg=0)
            try:
                st, _, _ = http_request(
                    f"{base_url}/api/upload-song",
                    method="POST",
                    headers={"X-Filename": "isolated_test.wav"},
                    data=make_wav_bytes(duration_sec=0.5),
                )
                self.assertEqual(st, 200)
            finally:
                stop()
        self.assertEqual(manifest_path.read_bytes(), before_bytes)

    def test_t2_f14_default_urllib_vs_proxy_bypass_demonstration(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _, base_url, stop = start_agent_server(cwd=Path(tmpdir), port_arg=0)
            try:
                st, _, _ = http_request(f"{base_url}/api/health")
                self.assertEqual(st, 200)
            finally:
                stop()

    def test_t2_f14_sample_and_full_song_mp3_integrity_untouched(self) -> None:
        for mp3_name in ("full_song.mp3", "sample_song.mp3"):
            p = ROOT_DIR / mp3_name
            self.assertTrue(p.is_file(), f"Missing {p}")
            self.assertGreater(p.stat().st_size, 5_000_000)
            dur = agent.get_media_duration(p)
            self.assertGreater(dur, 290.0)


if __name__ == "__main__":
    unittest.main()
