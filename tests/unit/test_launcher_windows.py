#!/usr/bin/env python3
"""Unit tests for Milestone M2 Windows and Mac/Linux launchers (F8)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BAT_PATH = ROOT_DIR / "START_HERE.bat"
CMD_PATH = ROOT_DIR / "START_HERE.command"


class TestWindowsAndMacLaunchers(unittest.TestCase):
    def test_start_here_bat_crlf_and_no_python3_command(self) -> None:
        self.assertTrue(BAT_PATH.is_file(), "START_HERE.bat missing")
        raw = BAT_PATH.read_bytes()
        self.assertGreater(len(raw), 0)

        crlf_count = raw.count(b"\r\n")
        lf_count = raw.count(b"\n")
        self.assertGreater(crlf_count, 20, "START_HERE.bat must have CRLF line endings")
        self.assertEqual(crlf_count, lf_count, "START_HERE.bat must not contain bare LF line endings")

        text = raw.decode("utf-8")
        # Must NEVER invoke python3 command on Windows (allow Python3xx directory names like C:\Python315)
        self.assertIsNone(
            re.search(r"\bpython3(?:\.exe)?\b", text, re.IGNORECASE),
            "START_HERE.bat must never invoke python3",
        )

    def test_start_here_bat_discovery_ladder_winget_refresh_and_error_pause(self) -> None:
        text = BAT_PATH.read_bytes().decode("utf-8")

        # 1. Local portable runtime & kit bin + WinGet Links PATH prepend
        self.assertIn(r"%~dp0runtime\python.exe", text)
        self.assertIn(r"%~dp0python\python.exe", text)
        self.assertIn(r"%~dp0bin", text)
        self.assertIn(r"%LOCALAPPDATA%\Microsoft\WinGet\Links", text)

        # 2. py -3 launcher + Microsoft Store WindowsApps stub guard
        self.assertIn("py -3", text)
        self.assertIn(r"\WindowsApps" + "\\", text)

        # 3. Registry PythonCore lookups
        for reg_key in (
            r"HKCU\Software\Python\PythonCore",
            r"HKLM\Software\Python\PythonCore",
            r"HKLM\Software\Wow6432Node\Python\PythonCore",
        ):
            self.assertIn(reg_key, text)
        self.assertIn("InstallPath", text)

        # 4. Standard install directories incl. C:\Python315, LocalAppData, ProgramFiles, scoop, pyenv, choco
        for expected_dir in (
            r"C:\Python315\python.exe",
            r"C:\Python310\python.exe",
            r"%LOCALAPPDATA%\Programs\Python\Python315\python.exe",
            r"%ProgramFiles%\Python312\python.exe",
            r"%USERPROFILE%\scoop\shims\python.exe",
            r"%USERPROFILE%\.pyenv\pyenv-win\shims\python",
            r"C:\ProgramData\chocolatey\bin\python.exe",
            r"%APPDATA%\Python\Python315\Scripts",
        ):
            self.assertIn(expected_dir, text, f"Missing discovery path in START_HERE.bat: {expected_dir}")

        # 5. winget install + Registry PATH refresh + re-probe
        self.assertIn("winget install -e --id Python.Python.3.12", text)
        self.assertIn(r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment", text)
        self.assertIn(r"HKCU\Environment", text)
        self.assertIn("call :refresh_path_from_registry", text)
        self.assertIn("call :probe_installed_python", text)

        # 6. Zero-admin fallback / guidance + pause + --ui %* forwarding
        self.assertIn("Seamless_Shorts_Player.html", text)
        self.assertIn("seamless_shorts_agent.py --ui %*", text)
        self.assertGreaterEqual(text.lower().count("pause"), 2)

    def test_start_here_command_executable_lf_and_shellcheck(self) -> None:
        self.assertTrue(CMD_PATH.is_file(), "START_HERE.command missing")
        raw = CMD_PATH.read_bytes()
        self.assertEqual(raw.count(b"\r\n"), 0, "START_HERE.command must use LF line endings")
        self.assertTrue(os.access(CMD_PATH, os.X_OK), "START_HERE.command must be executable")

        text = raw.decode("utf-8")
        self.assertIn("$(pwd)/bin", text)
        self.assertIn("$HOME/.local/bin", text)
        self.assertIn("python3", text)
        self.assertIn("python", text)
        self.assertIn('seamless_shorts_agent.py --ui "$@"', text)
        self.assertIn("read -r -p", text)

        sc = shutil.which("shellcheck")
        if not sc:
            self.skipTest("shellcheck not installed")
        res = subprocess.run([sc, str(CMD_PATH)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"shellcheck failed:\n{res.stdout}\n{res.stderr}")

    def test_start_here_bat_uses_find_for_windowsapps_call_for_shims_and_safe_powershell_env(self) -> None:
        text = BAT_PATH.read_bytes().decode("utf-8")
        # Must use find /I "\WindowsApps\" instead of findstr /I /C:"\WindowsApps\" (MSVC \" escape bug)
        self.assertNotIn(r'findstr /I /C:"\WindowsApps\"', text)
        self.assertGreaterEqual(text.count(r'find /I "\WindowsApps\"'), 2)
        # Must invoke PY_EXE and :try_python target via call so .bat/.cmd shims do not terminate batch execution
        self.assertIn('call "%PY_EXE%"', text)
        self.assertIn('call "%~1"', text)
        # Must pass %~dp0runtime to PowerShell via env var instead of single-quoted literal interpolation
        self.assertIn('set "SS_RT_DIR=%~dp0runtime"', text)
        self.assertIn("$env:SS_RT_DIR", text)
        self.assertNotIn("'%~dp0runtime'", text)
        # Must strip trailing backslash from PY_DIR before joining \Scripts
        self.assertIn(r'if "%PY_DIR:~-1%"=="\" set "PY_DIR=%PY_DIR:~0,-1%"', text)


if __name__ == "__main__":
    unittest.main()
