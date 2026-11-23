#!/usr/bin/env python3
"""Unit tests for Milestone M4 distribution zip & plain-language README (F11)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
ZIP_PATH = ROOT_DIR / "upload" / "YT_1m_to_full_song_1x.zip"
SYMLINK_PATH = ROOT_DIR / "YT_1m_to_full_song_1x.zip"
README_PATH = ROOT_DIR / "README.txt"

EXPECTED_ZIP_MEMBERS = {
    "seamless_shorts_agent.py",
    "START_HERE.bat",
    "START_HERE.command",
    "README.txt",
    "index.html",
    "styles.css",
    "app.js",
    "jszip.min.js",
    "Seamless_Shorts_Player.html",
}


def build_local_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_get(url: str, timeout: float = 10.0) -> tuple[int, dict[str, str], bytes]:
    opener = build_local_opener()
    req = urllib.request.Request(url, method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, headers, resp.read()
    except urllib.error.HTTPError as err:
        headers = {k.lower(): v for k, v in err.headers.items()}
        body = err.read()
        err.close()
        return err.code, headers, body


def clean_subproc_env() -> dict[str, str]:
    env = os.environ.copy()
    for k in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "HOST",
        "PORT",
    ):
        env.pop(k, None)
    return env


class TestDistributionZipAndReadme(unittest.TestCase):
    def test_zip_exists_symlink_resolves_and_exact_nine_flat_members(self) -> None:
        self.assertTrue(ZIP_PATH.is_file(), f"Missing distribution zip: {ZIP_PATH}")
        self.assertGreater(ZIP_PATH.stat().st_size, 0)

        self.assertTrue(SYMLINK_PATH.is_symlink(), f"Expected symlink at {SYMLINK_PATH}")
        self.assertEqual(SYMLINK_PATH.resolve(), ZIP_PATH.resolve())

        with zipfile.ZipFile(ZIP_PATH, "r") as zf:
            bad_member = zf.testzip()
            self.assertIsNone(bad_member, f"Corrupt member in distribution zip: {bad_member}")

            names = zf.namelist()
            self.assertEqual(len(names), 9, f"Expected 9 members, got {len(names)}: {names}")
            self.assertEqual(set(names), EXPECTED_ZIP_MEMBERS)
            for name in names:
                self.assertNotIn("/", name)
                self.assertNotIn("\\", name)

            bat_bytes = zf.read("START_HERE.bat")
            crlf_count = bat_bytes.count(b"\r\n")
            lf_count = bat_bytes.count(b"\n")
            self.assertGreater(crlf_count, 20)
            self.assertEqual(crlf_count, lf_count, "START_HERE.bat inside zip must have strict CRLF")

            source_map = {
                "seamless_shorts_agent.py": ROOT_DIR / "seamless_shorts_agent.py",
                "START_HERE.bat": ROOT_DIR / "START_HERE.bat",
                "START_HERE.command": ROOT_DIR / "START_HERE.command",
                "README.txt": ROOT_DIR / "README.txt",
                "index.html": ROOT_DIR / "web" / "index.html",
                "styles.css": ROOT_DIR / "web" / "styles.css",
                "app.js": ROOT_DIR / "web" / "app.js",
                "jszip.min.js": ROOT_DIR / "web" / "jszip.min.js",
                "Seamless_Shorts_Player.html": ROOT_DIR / "web" / "Seamless_Shorts_Player.html",
            }
            for member, src_path in source_map.items():
                self.assertEqual(
                    zf.read(member),
                    src_path.read_bytes(),
                    f"Zip member {member} does not match {src_path}",
                )

    def test_extracted_flat_zip_serves_web_app_and_api_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            extract_dir = Path(tmpdir)
            with zipfile.ZipFile(ZIP_PATH, "r") as zf:
                zf.extractall(extract_dir)

            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(extract_dir / "seamless_shorts_agent.py"),
                    "--ui",
                    "--no-browser",
                    "--port",
                    "0",
                ],
                cwd=str(extract_dir),
                env=clean_subproc_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            base_url = None
            captured_lines: list[str] = []
            try:
                deadline = time.monotonic() + 15.0
                assert proc.stdout is not None
                while time.monotonic() < deadline:
                    line = proc.stdout.readline()
                    if not line:
                        if proc.poll() is not None:
                            break
                        time.sleep(0.05)
                        continue
                    captured_lines.append(line)
                    m = re.search(r"Open in browser:\s*(http://127\.0\.0\.1:\d+)", line)
                    if m:
                        base_url = m.group(1)
                        break

                self.assertIsNotNone(
                    base_url,
                    f"Server from extracted zip failed to start:\n{''.join(captured_lines)}",
                )

                status, hdrs, body = http_get(f"{base_url}/")
                self.assertEqual(status, 200)
                self.assertIn("text/html", hdrs.get("content-type", ""))
                self.assertEqual(body, (extract_dir / "index.html").read_bytes())

                for asset, mime_substr in (
                    ("styles.css", "text/css"),
                    ("app.js", "javascript"),
                    ("jszip.min.js", "javascript"),
                    ("Seamless_Shorts_Player.html", "text/html"),
                ):
                    st, h, b = http_get(f"{base_url}/{asset}")
                    self.assertEqual(st, 200, f"GET /{asset} returned {st}")
                    self.assertIn(mime_substr, h.get("content-type", ""))
                    self.assertEqual(b, (extract_dir / asset).read_bytes())

                h_status, _, h_body = http_get(f"{base_url}/api/health")
                self.assertEqual(h_status, 200)
                health_json = json.loads(h_body.decode("utf-8"))
                self.assertTrue(health_json.get("ok"))
                self.assertEqual(health_json.get("mode"), "studio")

                s_status, _, s_body = http_get(f"{base_url}/api/state")
                self.assertEqual(s_status, 200)
                state_json = json.loads(s_body.decode("utf-8"))
                for key in ("version", "tools", "parts", "manifest"):
                    self.assertIn(key, state_json)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                if proc.stdout:
                    proc.stdout.close()

    def test_readme_plain_language_windows_guide_and_no_traps(self) -> None:
        self.assertTrue(README_PATH.is_file(), "README.txt missing")
        raw = README_PATH.read_bytes()
        crlf_count = raw.count(b"\r\n")
        lf_count = raw.count(b"\n")
        self.assertGreater(crlf_count, 20, "README.txt must use strict Windows CRLF")
        self.assertEqual(crlf_count, lf_count, "README.txt must not contain bare LF line endings")
        text = raw.decode("utf-8")

        for required in (
            "Extract",
            "Seamless_Shorts_Player.html",
            "START_HERE.bat",
            "START_HERE.command",
            "Audition Cut (−2s)",
            "Video Clip Audio",
            "Replacement Song",
            "Clips A/B",
            "Full Verify",
            "SmartScreen",
            "H.264",
            "Browser Cookies",
            "--cookies-from-browser",
        ):
            self.assertIn(required, text, f"README.txt missing guidance for: {required}")

        # Must NEVER tell Windows users to type python3
        self.assertIsNone(
            re.search(r"\bpython3\b", text, re.IGNORECASE),
            "README.txt must never instruct Windows users to run python3",
        )

        # Must NEVER include VM/localhost URL traps
        self.assertIsNone(
            re.search(
                r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\d+\.\d+\.\d+\.\d+)(?::\d+)?",
                text,
                re.IGNORECASE,
            ),
            "README.txt must not contain localhost/IP URLs",
        )


if __name__ == "__main__":
    unittest.main()
