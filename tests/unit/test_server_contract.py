#!/usr/bin/env python3
"""Unit and contract tests for Milestone M2 Studio HTTP server, tool discovery, and CLI."""

from __future__ import annotations

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
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
AGENT_PATH = ROOT_DIR / "seamless_shorts_agent.py"
WEB_DIR = ROOT_DIR / "web"

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import seamless_shorts_agent as agent  # noqa: E402


EXPECTED_CORS_METHODS = "GET, POST, OPTIONS, HEAD"


def build_local_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, str], bytes]:
    opener = build_local_opener()
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with opener.open(req, timeout=timeout) as resp:
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, resp_headers, resp.read()
    except urllib.error.HTTPError as err:
        resp_headers = {k.lower(): v for k, v in err.headers.items()}
        body = err.read() if method != "HEAD" else b""
        err.close()
        return err.code, resp_headers, body


def clean_subproc_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy", "HOST", "PORT"):
        env.pop(k, None)
    if extra:
        env.update(extra)
    return env


def start_agent_server(
    test_case: unittest.TestCase,
    script_path: Path,
    cwd: Path,
    port_arg: int | None = 0,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.Popen[str], str]:
    cmd = [sys.executable, str(script_path), "--ui", "--no-browser"]
    if port_arg is not None:
        cmd.extend(["--port", str(port_arg)])

    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=clean_subproc_env(extra_env),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    def cleanup() -> None:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        if proc.stdout:
            proc.stdout.close()

    test_case.addCleanup(cleanup)

    deadline = time.monotonic() + 12.0
    bound_url = None
    captured: list[str] = []
    assert proc.stdout is not None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            rest = proc.stdout.read()
            raise AssertionError(
                f"Server exited prematurely (rc={proc.returncode}): {''.join(captured)}{rest}"
            )
        line = proc.stdout.readline()
        if line:
            captured.append(line)
            m = re.search(r"http://127\.0\.0\.1:(\d+)", line)
            if m:
                bound_url = f"http://127.0.0.1:{m.group(1)}"
                break
        else:
            time.sleep(0.05)

    if not bound_url:
        raise AssertionError(f"Server did not report bound URL in time. Output: {''.join(captured)}")

    # Confirm /api/health responds cleanly
    for _ in range(20):
        try:
            status, _, _ = http_request(f"{bound_url}/api/health", timeout=2.0)
            if status == 200:
                return proc, bound_url
        except Exception:
            time.sleep(0.1)
    raise AssertionError(f"Server at {bound_url} failed health check")


class TestLiveDirectoryLayoutServer(unittest.TestCase):
    """Contract tests against /home/antigravity directory layout (read-only on shorts_workspace)."""

    base_url: str = ""
    proc: subprocess.Popen[str] | None = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.proc, cls.base_url = cls._start_shared_server()

    @classmethod
    def _start_shared_server(cls) -> tuple[subprocess.Popen[str], str]:
        cmd = [sys.executable, str(AGENT_PATH), "--ui", "--no-browser", "--port", "0"]
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT_DIR),
            env=clean_subproc_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + 12.0
        bound_url = None
        captured: list[str] = []
        assert proc.stdout is not None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError(f"Server exited early: {''.join(captured)}{proc.stdout.read()}")
            line = proc.stdout.readline()
            if line:
                captured.append(line)
                m = re.search(r"http://127\.0\.0\.1:(\d+)", line)
                if m:
                    bound_url = f"http://127.0.0.1:{m.group(1)}"
                    break
        if not bound_url:
            proc.terminate()
            raise AssertionError(f"No bound URL found: {''.join(captured)}")
        for _ in range(20):
            try:
                st, _, _ = http_request(f"{bound_url}/api/health", timeout=2.0)
                if st == 200:
                    return proc, bound_url
            except Exception:
                time.sleep(0.1)
        proc.terminate()
        raise AssertionError("Health check failed")

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.proc is not None and cls.proc.poll() is None:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
                cls.proc.wait(timeout=3)
        if cls.proc is not None and cls.proc.stdout:
            cls.proc.stdout.close()

    def assert_cors(self, headers: dict[str, str]) -> None:
        self.assertEqual(headers.get("access-control-allow-origin"), "*")
        self.assertEqual(headers.get("access-control-allow-methods"), EXPECTED_CORS_METHODS)
        self.assertEqual(headers.get("access-control-allow-headers"), "*")
        expose = headers.get("access-control-expose-headers", "")
        for token in ("Content-Range", "Accept-Ranges", "Content-Length"):
            self.assertIn(token, expose)

    def test_health_and_state_contract_keys(self) -> None:
        st, hdrs, body = http_request(f"{self.base_url}/api/health")
        self.assertEqual(st, 200)
        self.assert_cors(hdrs)
        health = json.loads(body.decode("utf-8"))
        self.assertEqual(health["ok"], True)
        self.assertEqual(health["app"], "seamless-shorts-studio")
        self.assertEqual(health["mode"], "studio")
        self.assertTrue(isinstance(health["version"], str) and len(health["version"]) > 0)

        st, hdrs, body = http_request(f"{self.base_url}/api/state")
        self.assertEqual(st, 200)
        self.assert_cors(hdrs)
        state = json.loads(body.decode("utf-8"))

        # Backward keys
        for key in (
            "tools",
            "playlist_url",
            "active_song_path",
            "is_demo_song",
            "audio_files",
            "source_count",
            "parts",
            "verify_exists",
            "verify_mtime",
            "job",
        ):
            self.assertIn(key, state, f"Missing backward key in /api/state: {key}")

        # Alias / forward keys
        for key in (
            "version",
            "sources",
            "active_playlist",
            "active_song",
            "manifest",
            "has_verify",
            "fade_ms",
            "song_name",
        ):
            self.assertIn(key, state, f"Missing alias/extra key in /api/state: {key}")

        self.assertEqual(state["playlist_url"], state["active_playlist"])
        self.assertEqual(state["active_song_path"], state["active_song"])
        self.assertEqual(state["verify_exists"], state["has_verify"])
        self.assertEqual(state["sources"], sorted(state["sources"]))
        self.assertEqual(len(state["sources"]), state["source_count"])
        self.assertEqual(state["parts"], state["manifest"]["parts"])
        self.assertIn("full_song.mp3", [f["name"] for f in state["audio_files"]])

    def test_web_assets_serving_and_unknown_404(self) -> None:
        expected_mimes = {
            "index.html": "text/html; charset=utf-8",
            "styles.css": "text/css; charset=utf-8",
            "app.js": "application/javascript; charset=utf-8",
            "jszip.min.js": "application/javascript; charset=utf-8",
            "Seamless_Shorts_Player.html": "text/html; charset=utf-8",
        }
        index_disk = (WEB_DIR / "index.html").read_bytes()
        for route in ("/", "/index.html"):
            st, hdrs, body = http_request(f"{self.base_url}{route}")
            self.assertEqual(st, 200)
            self.assertEqual(hdrs.get("content-type"), "text/html; charset=utf-8")
            self.assertEqual(hdrs.get("cache-control"), "no-store")
            self.assertEqual(body, index_disk)
            self.assert_cors(hdrs)

        for asset_name, expected_mime in expected_mimes.items():
            disk_bytes = (WEB_DIR / asset_name).read_bytes()
            for prefix in ("/web/", "/"):
                st, hdrs, body = http_request(f"{self.base_url}{prefix}{asset_name}")
                self.assertEqual(st, 200, f"Failed GET {prefix}{asset_name}")
                self.assertEqual(hdrs.get("content-type"), expected_mime)
                self.assertEqual(body, disk_bytes)
                self.assert_cors(hdrs)

        for unknown in ("/web/not_whitelisted.txt", "/not_whitelisted.txt", "/does/not/exist"):
            st, hdrs, _ = http_request(f"{self.base_url}{unknown}")
            self.assertEqual(st, 404, f"Expected 404 for {unknown}")
            self.assert_cors(hdrs)

    def test_head_parity_across_routes(self) -> None:
        _, _, state_raw = http_request(f"{self.base_url}/api/state")
        state = json.loads(state_raw.decode("utf-8"))
        first_part = state["parts"][0]["filename"]
        first_source = state["sources"][0]

        routes = [
            "/",
            "/index.html",
            "/web/styles.css",
            "/app.js",
            "/api/health",
            "/api/state",
            "/media/verify",
            f"/media/part/{first_part}",
            f"/media/source/{first_source}",
            "/media/audio/full_song.mp3",
        ]
        for route in routes:
            g_st, g_hdrs, _ = http_request(f"{self.base_url}{route}", method="GET")
            h_st, h_hdrs, h_body = http_request(f"{self.base_url}{route}", method="HEAD")
            self.assertEqual(h_st, g_st, f"HEAD status mismatch on {route}")
            self.assertEqual(h_body, b"", f"HEAD returned body on {route}")
            self.assertEqual(h_hdrs.get("content-type"), g_hdrs.get("content-type"), f"HEAD mime mismatch on {route}")
            self.assertEqual(h_hdrs.get("content-length"), g_hdrs.get("content-length"), f"HEAD length mismatch on {route}")
            self.assert_cors(h_hdrs)

        st_404, hdrs_404, body_404 = http_request(f"{self.base_url}/unknown/route", method="HEAD")
        self.assertEqual(st_404, 404)
        self.assertEqual(body_404, b"")
        self.assert_cors(hdrs_404)

    def test_range_streaming_206_416_download_and_media_audio(self) -> None:
        _, _, state_raw = http_request(f"{self.base_url}/api/state")
        state = json.loads(state_raw.decode("utf-8"))
        first_part = state["parts"][0]["filename"]
        first_source = state["sources"][0]

        media_checks = [
            (f"/media/verify", ROOT_DIR / "shorts_workspace" / "3_verification_full_playthrough.mp4", "video/mp4"),
            (f"/media/part/{first_part}", ROOT_DIR / "shorts_workspace" / "2_repurposed_shorts" / first_part, "video/mp4"),
            (f"/media/source/{first_source}", ROOT_DIR / "shorts_workspace" / "1_source_shorts" / first_source, "video/mp4"),
            ("/media/audio/full_song.mp3", ROOT_DIR / "full_song.mp3", "audio/mpeg"),
        ]

        for route, disk_path, expected_mime in media_checks:
            file_size = disk_path.stat().st_size
            with open(disk_path, "rb") as f:
                head_bytes = f.read(1024)
                f.seek(file_size - 256)
                tail_bytes = f.read(256)

            # 1. Closed range bytes=0-1023
            st, hdrs, body = http_request(f"{self.base_url}{route}", headers={"Range": "bytes=0-1023"})
            self.assertEqual(st, 206, f"Expected 206 for {route}")
            self.assertEqual(hdrs.get("content-type"), expected_mime)
            self.assertEqual(hdrs.get("accept-ranges"), "bytes")
            self.assertEqual(hdrs.get("content-range"), f"bytes 0-1023/{file_size}")
            self.assertEqual(body, head_bytes)
            self.assert_cors(hdrs)

            # 2. Open-ended range bytes=<size-512>-
            start_open = file_size - 512
            st, hdrs, body = http_request(f"{self.base_url}{route}", headers={"Range": f"bytes={start_open}-"})
            self.assertEqual(st, 206)
            self.assertEqual(hdrs.get("content-range"), f"bytes {start_open}-{file_size - 1}/{file_size}")
            self.assertEqual(len(body), 512)
            self.assert_cors(hdrs)

            # 3. Suffix range bytes=-256
            st, hdrs, body = http_request(f"{self.base_url}{route}", headers={"Range": "bytes=-256"})
            self.assertEqual(st, 206)
            self.assertEqual(hdrs.get("content-range"), f"bytes {file_size - 256}-{file_size - 1}/{file_size}")
            self.assertEqual(body, tail_bytes)

            # 4. Invalid / out-of-bounds ranges -> 416
            for bad_range in (f"bytes={file_size + 100}-", "bytes=500-100", "bytes=-", "bytes=-0"):
                st, hdrs, _ = http_request(f"{self.base_url}{route}", headers={"Range": bad_range})
                self.assertEqual(st, 416, f"Expected 416 on {route} with {bad_range}")
                self.assertEqual(hdrs.get("content-range"), f"bytes */{file_size}")
                self.assert_cors(hdrs)

            # 5. ?download=1 Content-Disposition attachment
            st, hdrs, _ = http_request(f"{self.base_url}{route}?download=1", method="HEAD")
            self.assertEqual(st, 200)
            self.assertIn("attachment;", hdrs.get("content-disposition", ""))
            self.assert_cors(hdrs)

    def test_path_traversal_and_options_cors_and_malformed_requests(self) -> None:
        traversal_paths = [
            "/media/part/../../seamless_shorts_agent.py",
            "/media/part/..%2f..%2fseamless_shorts_agent.py",
            "/media/part/%2e%2e%2fseamless_shorts_agent.py",
            "/media/part/..%5cseamless_shorts_agent.py",
            "/media/part/sub/Part_01_02_19EAck3_MSg.mp4",
            "/media/part/C:Part_01.mp4",
            "/media/part/..",
            "/media/source/../full_song.mp3",
            "/media/audio/%2e%2e%2ffull_song.mp3",
            "/web/..%2fREADME.txt",
        ]
        for bad_path in traversal_paths:
            for method in ("GET", "HEAD"):
                st, hdrs, _ = http_request(f"{self.base_url}{bad_path}", method=method)
                self.assertIn(st, (400, 404), f"Traversal NOT rejected on {method} {bad_path}: got {st}")
                self.assert_cors(hdrs)

        # OPTIONS preflight -> 204
        st, hdrs, body = http_request(f"{self.base_url}/api/run", method="OPTIONS")
        self.assertEqual(st, 204)
        self.assertEqual(body, b"")
        self.assertEqual(hdrs.get("content-length"), "0")
        self.assert_cors(hdrs)

        # POST /api/run malformed JSON -> 400
        for payload in (b"{bad json", b"[1, 2, 3]"):
            st, hdrs, _ = http_request(
                f"{self.base_url}/api/run",
                method="POST",
                headers={"Content-Type": "application/json"},
                data=payload,
            )
            self.assertEqual(st, 400)
            self.assert_cors(hdrs)

        # POST /api/run invalid cookies_from_browser -> 400
        st, hdrs, _ = http_request(
            f"{self.base_url}/api/run",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"cookies_from_browser": "--evil-flag"}).encode("utf-8"),
        )
        self.assertEqual(st, 400)
        self.assert_cors(hdrs)

        # POST /api/upload-song traversal / empty / non-audio -> 400
        for bad_name in ("../evil.mp3", "sub/evil.mp3", "evil.exe", ""):
            st, hdrs, _ = http_request(
                f"{self.base_url}/api/upload-song",
                method="POST",
                headers={"X-Filename": bad_name},
                data=b"ID3test",
            )
            self.assertEqual(st, 400, f"Expected 400 for upload X-Filename={bad_name!r}")
            self.assert_cors(hdrs)


class TestIsolatedLayoutsAndEnvServer(unittest.TestCase):
    """Contract tests for flat zip -j layout, fallback STUDIO_HTML, HOST/PORT env, upload, and unit helpers."""

    def test_flat_zip_layout_upload_media_audio_and_toolkit_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            shutil.copy2(AGENT_PATH, tmp / "seamless_shorts_agent.py")
            for name in ("index.html", "styles.css", "app.js", "jszip.min.js", "Seamless_Shorts_Player.html"):
                shutil.copy2(WEB_DIR / name, tmp / name)

            _, base_url = start_agent_server(self, tmp / "seamless_shorts_agent.py", cwd=tmp, port_arg=0)

            # Root serves flat index.html instead of STUDIO_HTML
            st, _, body = http_request(f"{base_url}/")
            self.assertEqual(st, 200)
            self.assertEqual(body, (WEB_DIR / "index.html").read_bytes())

            # Both /<asset> and /web/<asset> resolve from flat dir
            for name in ("styles.css", "app.js", "jszip.min.js", "Seamless_Shorts_Player.html"):
                expected = (WEB_DIR / name).read_bytes()
                st1, _, b1 = http_request(f"{base_url}/{name}")
                st2, _, b2 = http_request(f"{base_url}/web/{name}")
                self.assertEqual((st1, b1), (200, expected))
                self.assertEqual((st2, b2), (200, expected))

            # Dynamic /download/toolkit.zip includes script + flat web assets
            st, hdrs, zip_bytes = http_request(f"{base_url}/download/toolkit.zip")
            self.assertEqual(st, 200)
            self.assertEqual(hdrs.get("content-type"), "application/zip")
            with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
                names = set(zf.namelist())
                self.assertIn("seamless_shorts_agent.py", names)
                self.assertIn("index.html", names)
                self.assertIn("Seamless_Shorts_Player.html", names)

            # Upload valid audio song and stream it back via /media/audio/<name>
            wav_payload = b"RIFF" + b"\x00" * 100
            st, hdrs, body = http_request(
                f"{base_url}/api/upload-song",
                method="POST",
                headers={"X-Filename": "my%20track.wav"},
                data=wav_payload,
            )
            self.assertEqual(st, 200)
            self.assert_cors_headers(hdrs)
            res = json.loads(body.decode("utf-8"))
            self.assertTrue(res["ok"])
            saved_name = res["song_name"]
            self.assertTrue((tmp / "shorts_workspace" / saved_name).is_file())

            st, hdrs, audio_body = http_request(f"{base_url}/media/audio/{saved_name}")
            self.assertEqual(st, 200)
            self.assertEqual(hdrs.get("content-type"), "audio/wav")
            self.assertEqual(audio_body, wav_payload)

            st, hdrs, range_body = http_request(
                f"{base_url}/media/audio/{saved_name}",
                headers={"Range": "bytes=0-15"},
            )
            self.assertEqual(st, 206)
            self.assertEqual(range_body, wav_payload[:16])

    def assert_cors_headers(self, hdrs: dict[str, str]) -> None:
        self.assertEqual(hdrs.get("access-control-allow-origin"), "*")
        self.assertEqual(hdrs.get("access-control-allow-methods"), EXPECTED_CORS_METHODS)

    def test_fallback_studio_html_and_host_port_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            shutil.copy2(AGENT_PATH, tmp / "seamless_shorts_agent.py")

            # Use HOST and PORT env overrides without --port/--host CLI args
            _, base_url = start_agent_server(
                self,
                tmp / "seamless_shorts_agent.py",
                cwd=tmp,
                port_arg=None,
                extra_env={"HOST": "127.0.0.1", "PORT": "0"},
            )

            st, hdrs, body = http_request(f"{base_url}/")
            self.assertEqual(st, 200)
            self.assertEqual(hdrs.get("content-type"), "text/html; charset=utf-8")
            self.assertIn(b"3-Step Song Sync Studio", body)

            for missing in ("/styles.css", "/web/styles.css", "/app.js"):
                st, hdrs, _ = http_request(f"{base_url}{missing}")
                self.assertEqual(st, 404)
                self.assert_cors_headers(hdrs)

    def test_setup_paths_idempotent_and_windows_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            appdata = tmp / "Roaming"
            localappdata = tmp / "Local"
            roaming_scripts = appdata / "Python" / "Python315" / "Scripts"
            local_scripts = localappdata / "Programs" / "Python" / "Python315" / "Scripts"
            winget_links = localappdata / "Microsoft" / "WinGet" / "Links"
            winget_ffmpeg = localappdata / "Microsoft" / "WinGet" / "Packages" / "Gyan.FFmpeg_1" / "ffmpeg-7.0" / "bin"
            local_bin = tmp / "bin"
            for d in (roaming_scripts, local_scripts, winget_links, winget_ffmpeg, local_bin):
                d.mkdir(parents=True, exist_ok=True)

            orig_env = os.environ.copy()
            orig_script_dir = agent.SCRIPT_DIR
            try:
                os.environ["APPDATA"] = str(appdata)
                os.environ["LOCALAPPDATA"] = str(localappdata)
                os.environ["PATH"] = "/usr/bin:/bin"
                agent.SCRIPT_DIR = tmp

                agent.setup_paths()
                path_after_first = os.environ["PATH"]
                entries = path_after_first.split(os.pathsep)
                self.assertIn(str(roaming_scripts), entries)
                self.assertIn(str(local_scripts), entries)
                self.assertIn(str(winget_links), entries)
                self.assertIn(str(winget_ffmpeg), entries)
                self.assertIn(str(local_bin), entries)

                # Repeated polling calls must NEVER grow PATH
                for _ in range(10):
                    agent.setup_paths()
                self.assertEqual(os.environ["PATH"], path_after_first)
            finally:
                agent.SCRIPT_DIR = orig_script_dir
                os.environ.clear()
                os.environ.update(orig_env)

    def test_yt_dlp_cmd_and_ffprobe_absent_fallback(self) -> None:
        # 1. python -m yt_dlp vs yt-dlp binary
        with mock.patch("importlib.util.find_spec", return_value=object()):
            self.assertEqual(agent.get_yt_dlp_cmd(), [sys.executable, "-m", "yt_dlp"])
        with mock.patch("importlib.util.find_spec", return_value=None), mock.patch(
            "shutil.which", side_effect=lambda name: "/usr/bin/yt-dlp.exe" if name == "yt-dlp.exe" else None
        ):
            self.assertEqual(agent.get_yt_dlp_cmd(), ["/usr/bin/yt-dlp.exe"])

        # 2. get_media_duration and get_video_info degrade gracefully when ffprobe is absent
        fake_stderr = (
            "  Duration: 00:01:02.50, start: 0.000000, bitrate: 2000 kb/s\n"
            "  Stream #0:0(und): Video: h264 (High), yuv420p(tv, bt709), 1080x1920 [SAR 1:1 DAR 9:16], 30 fps\n"
        )
        fake_res = subprocess.CompletedProcess(args=["ffmpeg"], returncode=1, stdout="", stderr=fake_stderr)
        with tempfile.NamedTemporaryFile(suffix=".mp4") as tf:
            tf.write(b"\x00" * 128)
            tf.flush()
            with mock.patch.object(agent, "get_ffprobe_bin", return_value=None), mock.patch.object(
                agent, "get_ffmpeg_bin", return_value="/usr/bin/ffmpeg"
            ), mock.patch("subprocess.run", return_value=fake_res):
                dur = agent.get_media_duration(tf.name)
                self.assertAlmostEqual(dur, 62.5, places=3)
                info = agent.get_video_info(tf.name)
                self.assertAlmostEqual(info["duration"], 62.5, places=3)
                self.assertEqual(info["codec"], "h264")
                self.assertEqual(info["width"], 1080)
                self.assertEqual(info["height"], 1920)
            with mock.patch.object(agent, "get_ffprobe_bin", return_value=None), mock.patch.object(
                agent, "get_ffmpeg_bin", return_value=None
            ):
                info_empty = agent.get_video_info(tf.name)
                self.assertEqual(info_empty["codec"], "unknown")
                self.assertEqual(info_empty["width"], 0)
                self.assertEqual(info_empty["height"], 0)

    def test_cookies_from_browser_cli_pipeline_and_api_run(self) -> None:
        # 1. CLI --cookies-from-browser forwarding
        with mock.patch.object(agent, "run_pipeline") as mock_pipe, mock.patch.object(
            sys, "argv", ["seamless_shorts_agent.py", "--cli", "--skip-download", "--cookies-from-browser", "chrome"]
        ):
            agent.main()
            mock_pipe.assert_called_once()
            self.assertEqual(mock_pipe.call_args.kwargs.get("cookies_from_browser"), "chrome")

        # 2. download_playlist_shorts passes --cookies-from-browser ONLY when provided
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            (out_dir / "01_test.mp4").write_bytes(b"mp4")

            seen_cmds: list[list[str]] = []

            def fake_run(cmd, **kwargs):
                seen_cmds.append(cmd)
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

            with mock.patch.object(agent, "ensure_yt_dlp", return_value=["yt-dlp"]), mock.patch(
                "subprocess.run", side_effect=fake_run
            ):
                agent.download_playlist_shorts(
                    "https://yt/test", out_dir, log_cb=lambda *_: None, cookies_from_browser="firefox"
                )
                agent.download_playlist_shorts(
                    "https://yt/test", out_dir, log_cb=lambda *_: None, cookies_from_browser=None
                )

            self.assertIn("--cookies-from-browser", seen_cmds[0])
            idx = seen_cmds[0].index("--cookies-from-browser")
            self.assertEqual(seen_cmds[0][idx + 1], "firefox")
            self.assertNotIn("--cookies-from-browser", seen_cmds[1])

        # 3. In-process /api/run checks: boolean "false", 0 vs None, cookies_from_browser, 409 concurrency
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), agent.make_handler())
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))

        captured_calls: list[dict] = []
        done_evt = threading.Event()

        def fake_pipeline(**kwargs):
            captured_calls.append(kwargs)
            done_evt.set()
            return {"parts": [], "fade_ms": kwargs["fade_ms"], "song_name": "", "song_path": ""}

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
                        "browser_safe_h264": "false",
                        "cookies_from_browser": "edge",
                    }
                ).encode("utf-8"),
            )
            self.assertEqual(st, 200)
            self.assertTrue(done_evt.wait(timeout=3.0))
            self.assertEqual(captured_calls[0]["max_downloads"], 0)
            self.assertEqual(captured_calls[0]["fade_ms"], 0)
            self.assertIs(captured_calls[0]["skip_download"], False)
            self.assertIs(captured_calls[0]["browser_safe_h264"], False)
            self.assertEqual(captured_calls[0]["cookies_from_browser"], "edge")

        # None defaults & 409 conflict while running
        done_evt.clear()
        captured_calls.clear()
        gate = threading.Event()

        def blocking_pipeline(**kwargs):
            captured_calls.append(kwargs)
            gate.wait(timeout=3.0)
            done_evt.set()
            return {"parts": []}

        with mock.patch.object(agent, "run_pipeline", side_effect=blocking_pipeline):
            try:
                st1, _, _ = http_request(
                    f"http://127.0.0.1:{port}/api/run",
                    method="POST",
                    headers={"Content-Type": "application/json"},
                    data=json.dumps({"max_downloads": None, "fade_ms": None}).encode("utf-8"),
                )
                self.assertEqual(st1, 200)
                st2, hdrs2, _ = http_request(
                    f"http://127.0.0.1:{port}/api/run",
                    method="POST",
                    headers={"Content-Type": "application/json"},
                    data=b"{}",
                )
                self.assertEqual(st2, 409)
                self.assert_cors_headers(hdrs2)
            finally:
                gate.set()
                done_evt.wait(timeout=3.0)
            self.assertEqual(captured_calls[0]["max_downloads"], 10)
            self.assertEqual(captured_calls[0]["fade_ms"], 15)

    def test_find_free_port_respects_host_and_zero(self) -> None:
        self.assertEqual(agent.find_free_port(0, host="127.0.0.1"), 0)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            busy_port = s.getsockname()[1]
            found = agent.find_free_port(busy_port, host="127.0.0.1")
            self.assertNotEqual(found, busy_port)
            self.assertGreater(found, 0)
        for bad_port in (-1, 65536, True, False, 1.5, "1.5"):
            with self.assertRaises(ValueError):
                agent.find_free_port(bad_port, host="127.0.0.1")

    def test_playlist_url_validation_and_yt_dlp_double_dash(self) -> None:
        with self.assertRaises(ValueError):
            agent.validate_playlist_url("--version")
        with self.assertRaises(ValueError):
            agent.validate_playlist_url("")
        with self.assertRaises(ValueError):
            agent.validate_playlist_url("ftp://example.com/list")
        with self.assertRaises(ValueError):
            agent.validate_playlist_url("https://example.com/list\n--exec")
        self.assertEqual(
            agent.validate_playlist_url(" https://www.youtube.com/playlist?list=PL123 "),
            "https://www.youtube.com/playlist?list=PL123",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            (out_dir / "01_test.mp4").write_bytes(b"mp4")
            seen_cmds: list[list[str]] = []

            def fake_run(cmd, **kwargs):
                seen_cmds.append(cmd)
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

            with mock.patch.object(agent, "ensure_yt_dlp", return_value=["yt-dlp"]), mock.patch(
                "subprocess.run", side_effect=fake_run
            ):
                agent.download_playlist_shorts("https://yt/test", out_dir, log_cb=lambda *_: None)
                with self.assertRaises(ValueError):
                    agent.download_playlist_shorts("--version", out_dir, log_cb=lambda *_: None)
            self.assertEqual(seen_cmds[0][-2:], ["--", "https://yt/test"])

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), agent.make_handler())
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))

        for bad_url in ("--version", "", "ftp://example.com", "https://yt/a b"):
            st, hdrs, body = http_request(
                f"http://127.0.0.1:{port}/api/run",
                method="POST",
                headers={"Content-Type": "application/json"},
                data=json.dumps({"playlist_url": bad_url, "skip_download": False}).encode("utf-8"),
            )
            self.assertEqual(st, 400, f"Expected 400 for playlist_url={bad_url!r}, got {st}")
            self.assert_cors_headers(hdrs)
            self.assertIn("error", json.loads(body.decode("utf-8")))

    def test_parse_int_param_rejects_floats_and_api_run_returns_400(self) -> None:
        self.assertEqual(agent.parse_int_param(0, 10, min_val=0), 0)
        self.assertEqual(agent.parse_int_param("25", 10, min_val=0), 25)
        for bad in (1.5, "1.5", True, False, "abc"):
            with self.assertRaises(ValueError):
                agent.parse_int_param(bad, 10, min_val=0)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), agent.make_handler())
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))

        for payload in ({"max_downloads": 1.5}, {"fade_ms": 2.75}, {"fade_ms": "1.5"}):
            st, hdrs, _ = http_request(
                f"http://127.0.0.1:{port}/api/run",
                method="POST",
                headers={"Content-Type": "application/json"},
                data=json.dumps(payload).encode("utf-8"),
            )
            self.assertEqual(st, 400, f"Expected 400 for {payload!r}")
            self.assert_cors_headers(hdrs)

    def test_process_seamless_shorts_skips_tiny_clip_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            tiny = tmp / "01_tiny.mp4"
            normal = tmp / "02_normal.mp4"
            song = tmp / "song.mp3"
            out_dir = tmp / "out"
            for f in (tiny, normal, song):
                f.write_bytes(b"stub")

            def fake_vdur(p):
                return 0.05 if Path(p).name == "01_tiny.mp4" else 1.5

            def fake_run_cmd(cmd):
                Path(cmd[-1]).write_bytes(b"mp4data")
                return ""

            with mock.patch.object(agent, "ensure_ffmpeg", return_value="/usr/bin/ffmpeg"), mock.patch.object(
                agent, "get_audio_stream_duration", return_value=5.0
            ), mock.patch.object(agent, "get_video_stream_duration", side_effect=fake_vdur), mock.patch.object(
                agent, "run_cmd", side_effect=fake_run_cmd
            ), mock.patch.object(
                agent,
                "get_video_info",
                return_value={"duration": 1.5, "codec": "h264", "width": 1080, "height": 1920, "size_bytes": 7},
            ):
                out_files, parts = agent.process_seamless_shorts(
                    [tiny, normal], song, out_dir, log_cb=lambda *_: None
                )
            self.assertEqual(len(out_files), 1)
            self.assertEqual(len(parts), 1)
            self.assertEqual(parts[0]["source_filename"], "02_normal.mp4")

    def test_head_download_all_zip_parity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            shutil.copy2(AGENT_PATH, tmp / "seamless_shorts_agent.py")
            out_dir = tmp / "shorts_workspace" / "2_repurposed_shorts"
            out_dir.mkdir(parents=True)
            (out_dir / "Part_01_demo.mp4").write_bytes(b"DEMO_MP4_BYTES_1234567890")
            (tmp / "shorts_workspace" / "3_verification_full_playthrough.mp4").write_bytes(b"VERIFY_BYTES_987654321")

            _, base_url = start_agent_server(self, tmp / "seamless_shorts_agent.py", cwd=tmp, port_arg=0)
            g_st, g_hdrs, g_body = http_request(f"{base_url}/download/all.zip", method="GET")
            h_st, h_hdrs, h_body = http_request(f"{base_url}/download/all.zip", method="HEAD")
            self.assertEqual(g_st, 200)
            self.assertEqual(h_st, 200)
            self.assertEqual(h_body, b"")
            self.assertEqual(h_hdrs.get("accept-ranges"), "bytes")
            self.assertEqual(h_hdrs.get("content-length"), str(len(g_body)))
            self.assertEqual(h_hdrs.get("content-length"), g_hdrs.get("content-length"))
            self.assert_cors_headers(h_hdrs)


if __name__ == "__main__":
    unittest.main()
