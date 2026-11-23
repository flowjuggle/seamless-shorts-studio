import argparse
import html
import importlib.util
import io
import json
import math
import mimetypes
import os
import re
import shutil
import site
import socket
import struct
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import urllib.parse
import wave
import webbrowser
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

# ================= CONFIGURATION =================
STUDIO_VERSION = "2.0.0"
SCRIPT_DIR = Path(__file__).resolve().parent
PLAYLIST_URL = "https://www.youtube.com/playlist?list=PL2vNBvHyEXihiuQ2htu6rLcOjw7lzsKcD"
MASTER_SONG_PATH = "full_song.mp3"  # Path to the full song you want to play across Shorts
WORK_DIR = Path("./shorts_workspace")
SOURCE_DIR = WORK_DIR / "1_source_shorts"
OUTPUT_DIR = WORK_DIR / "2_repurposed_shorts"
VERIFY_FILE = WORK_DIR / "3_verification_full_playthrough.mp4"
MANIFEST_FILE = WORK_DIR / "manifest.json"
FADE_DURATION = 0.015  # 15ms micro-fade to eliminate audio pops at cut points
DEFAULT_PORT = 8765
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
WEB_ASSET_WHITELIST = {
    "index.html",
    "styles.css",
    "app.js",
    "jszip.min.js",
    "Seamless_Shorts_Player.html",
}
WEB_MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}
MEDIA_MIME_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".opus": "audio/opus",
}
# =================================================


def resolve_web_asset(name: str) -> Optional[Path]:
    """Resolve a whitelisted web asset from <script_dir>/web/ first, then flat <script_dir>/."""
    if name not in WEB_ASSET_WHITELIST:
        return None
    for candidate in (SCRIPT_DIR / "web" / name, SCRIPT_DIR / name):
        if candidate.is_file():
            return candidate
    return None


def _norm_path_key(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def setup_paths():
    """Ensure Python user scripts, local bin/, and common FFmpeg locations are on PATH (idempotent)."""
    extra_dirs = []
    seen_candidates = set()

    def add_candidate(path_obj):
        if not path_obj:
            return
        try:
            p = Path(path_obj)
        except Exception:
            return
        key = _norm_path_key(p)
        if key in seen_candidates:
            return
        seen_candidates.add(key)
        if p.is_dir():
            extra_dirs.append(p)

    # Local bundled bin directories first so project-local tools take priority
    add_candidate(SCRIPT_DIR / "bin")
    add_candidate(Path.cwd() / "bin")
    add_candidate(Path.home() / ".local" / "bin")

    # Python interpreter & user scripts directories (including site/sysconfig)
    try:
        add_candidate(sysconfig.get_path("scripts"))
    except Exception:
        pass
    try:
        user_scheme = "nt_user" if os.name == "nt" else "posix_user"
        add_candidate(sysconfig.get_path("scripts", user_scheme))
    except Exception:
        pass
    try:
        user_site = site.getusersitepackages()
        if user_site:
            site_parent = Path(user_site).parent
            add_candidate(site_parent / "Scripts")
            add_candidate(site_parent / "bin")
    except Exception:
        pass
    add_candidate(Path(sys.executable).resolve().parent / "Scripts")
    add_candidate(Path(sys.executable).resolve().parent)

    # Windows AppData/LocalAppData Python Scripts (covers C:\Python315 & Roaming Python3xx)
    appdata_raw = os.environ.get("APPDATA")
    appdata_dir = Path(appdata_raw) if appdata_raw else (Path.home() / "AppData" / "Roaming")
    localappdata_raw = os.environ.get("LOCALAPPDATA")
    localappdata_dir = Path(localappdata_raw) if localappdata_raw else (Path.home() / "AppData" / "Local")

    if os.name == "nt" or appdata_raw:
        py_roaming = appdata_dir / "Python"
        if py_roaming.is_dir():
            for d in sorted(py_roaming.glob("Python*/Scripts"), reverse=True):
                add_candidate(d)
        add_candidate(
            appdata_dir / "Python" / f"Python{sys.version_info.major}{sys.version_info.minor}" / "Scripts"
        )

    if os.name == "nt" or localappdata_raw:
        py_local = localappdata_dir / "Programs" / "Python"
        if py_local.is_dir():
            for d in sorted(py_local.glob("Python*/Scripts"), reverse=True):
                add_candidate(d)
        add_candidate(localappdata_dir / "Microsoft" / "WinGet" / "Links")
        winget_dir = localappdata_dir / "Microsoft" / "WinGet" / "Packages"
        if winget_dir.is_dir():
            for pattern in ("*FFmpeg*/*/bin/ffmpeg.exe", "*FFmpeg*/*/*/bin/ffmpeg.exe", "*FFmpeg*/*/bin"):
                for hit in sorted(winget_dir.glob(pattern)):
                    add_candidate(hit.parent if hit.is_file() else hit)

    if os.name == "nt":
        for p in (
            Path("C:/ffmpeg/bin"),
            Path("C:/ProgramData/chocolatey/bin"),
            Path.home() / "scoop" / "shims",
        ):
            add_candidate(p)

    add_candidate(SCRIPT_DIR)
    add_candidate(Path.cwd())

    current_path = os.environ.get("PATH", "")
    existing_entries = [p for p in current_path.split(os.pathsep) if p]
    existing_keys = {_norm_path_key(p) for p in existing_entries}

    missing = [str(d) for d in extra_dirs if _norm_path_key(d) not in existing_keys]
    if missing:
        os.environ["PATH"] = os.pathsep.join(missing + existing_entries)


setup_paths()


def get_ffmpeg_bin():
    """Locate ffmpeg executable, checking PATH and imageio-ffmpeg fallback."""
    setup_paths()
    exe = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def get_ffprobe_bin():
    """Locate ffprobe executable on PATH."""
    setup_paths()
    return shutil.which("ffprobe") or shutil.which("ffprobe.exe")


def has_yt_dlp():
    """Check if yt-dlp is available either as a Python module or CLI executable."""
    return get_yt_dlp_cmd() is not None


def get_yt_dlp_cmd():
    """Return the safest command list to invoke yt-dlp."""
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    setup_paths()
    exe = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if exe:
        return [exe]
    return None


def ensure_yt_dlp(log_cb=print):
    """Ensure yt-dlp is installed, installing via pip if missing."""
    cmd = get_yt_dlp_cmd()
    if cmd:
        return cmd
    log_cb("[Setup] Installing yt-dlp automatically...")
    pip_cmd = [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"]
    res = subprocess.run(
        pip_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace"
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"Could not auto-install yt-dlp.\nRun manually: pip install yt-dlp\nDetails: {res.stderr}"
        )
    setup_paths()
    cmd = get_yt_dlp_cmd()
    if not cmd:
        raise RuntimeError("yt-dlp installed but could not be located on PATH.")
    log_cb("[Setup] yt-dlp installed successfully.")
    return cmd


def ensure_ffmpeg(log_cb=print):
    """Ensure ffmpeg is available; attempt automatic Windows install or pip fallback if missing."""
    ffmpeg_bin = get_ffmpeg_bin()
    if ffmpeg_bin:
        return ffmpeg_bin

    if os.name == "nt" and shutil.which("winget"):
        log_cb("[Setup] FFmpeg not found. Attempting automatic install via winget...")
        res = subprocess.run(
            [
                "winget",
                "install",
                "-e",
                "--id",
                "Gyan.FFmpeg",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        setup_paths()
        ffmpeg_bin = get_ffmpeg_bin()
        if ffmpeg_bin:
            log_cb("[Setup] FFmpeg installed successfully.")
            return ffmpeg_bin
        log_cb(f"[Setup] winget output: {res.stderr or res.stdout}")

    log_cb("[Setup] Installing bundled imageio-ffmpeg fallback...")
    res = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-U", "imageio-ffmpeg"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if res.returncode == 0:
        ffmpeg_bin = get_ffmpeg_bin()
        if ffmpeg_bin:
            log_cb("[Setup] Bundled FFmpeg ready.")
            return ffmpeg_bin

    raise RuntimeError(
        "FFmpeg was not found. Install it once:\n"
        "  Windows: winget install Gyan.FFmpeg\n"
        "  Mac:     brew install ffmpeg\n"
        "  Linux:   sudo apt install ffmpeg"
    )


def run_cmd(cmd):
    """Run a shell command safely with UTF-8 decoding and raise an error if it fails."""
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\nError: {result.stderr}")
    return result.stdout.strip()


def get_media_duration(file_path):
    """Return the exact duration of a media file in seconds (float)."""
    ffprobe_bin = get_ffprobe_bin()
    if ffprobe_bin:
        try:
            cmd = [
                ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(file_path),
            ]
            output = run_cmd(cmd)
            data = json.loads(output)
            return float(data["format"]["duration"])
        except Exception:
            pass

    # Fallback: parse duration from ffmpeg -i stderr if ffprobe is unavailable
    ffmpeg_bin = get_ffmpeg_bin()
    if not ffmpeg_bin:
        raise RuntimeError("Neither ffprobe nor ffmpeg is installed.")
    res = subprocess.run(
        [ffmpeg_bin, "-i", str(file_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr or "")
    if not m:
        raise RuntimeError(f"Could not determine duration for {file_path}")
    hrs, mins, secs = float(m.group(1)), float(m.group(2)), float(m.group(3))
    return hrs * 3600.0 + mins * 60.0 + secs


def get_video_stream_duration(file_path):
    """Return video stream v:0 duration if available, else container duration."""
    ffprobe_bin = get_ffprobe_bin()
    if ffprobe_bin:
        try:
            out = run_cmd(
                [
                    ffprobe_bin,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=duration",
                    "-of",
                    "json",
                    str(file_path),
                ]
            )
            streams = json.loads(out).get("streams", [])
            if streams and streams[0].get("duration") not in (None, "N/A", ""):
                dur = float(streams[0]["duration"])
                if dur > 0:
                    return dur
        except Exception:
            pass
    return get_media_duration(file_path)


def get_audio_stream_duration(file_path):
    """Return audio duration, clamping to stream a:0 duration when available."""
    fmt_dur = get_media_duration(file_path)
    ffprobe_bin = get_ffprobe_bin()
    if ffprobe_bin:
        try:
            out = run_cmd(
                [
                    ffprobe_bin,
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
                    "-show_entries",
                    "stream=duration",
                    "-of",
                    "json",
                    str(file_path),
                ]
            )
            streams = json.loads(out).get("streams", [])
            if streams and streams[0].get("duration") not in (None, "N/A", ""):
                sdur = float(streams[0]["duration"])
                if sdur > 0:
                    return min(fmt_dur, sdur)
        except Exception:
            pass
    return fmt_dur


def get_video_info(file_path):
    """Return duration, codec, width, height, and file size for a video file."""
    p = Path(file_path)
    info = {
        "duration": 0.0,
        "codec": "unknown",
        "width": 0,
        "height": 0,
        "size_bytes": p.stat().st_size if p.exists() else 0,
    }
    try:
        info["duration"] = get_media_duration(p)
    except Exception:
        pass

    ffprobe_bin = get_ffprobe_bin()
    if ffprobe_bin and p.exists():
        try:
            out = run_cmd(
                [
                    ffprobe_bin,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=codec_name,width,height",
                    "-of",
                    "json",
                    str(p),
                ]
            )
            streams = json.loads(out).get("streams", [])
            if streams:
                info["codec"] = streams[0].get("codec_name", "unknown")
                info["width"] = int(streams[0].get("width", 0) or 0)
                info["height"] = int(streams[0].get("height", 0) or 0)
                return info
        except Exception:
            pass

    ffmpeg_bin = get_ffmpeg_bin()
    if ffmpeg_bin and p.exists():
        try:
            res = subprocess.run(
                [ffmpeg_bin, "-i", str(p)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            vm = re.search(
                r"Stream\s+#.*?:\s*Video:\s*([A-Za-z0-9_]+).*?,\s*([1-9]\d{1,4})x([1-9]\d{1,4})(?=[\s,\[]|$)",
                res.stderr or "",
            )
            if vm:
                info["codec"] = vm.group(1)
                info["width"] = int(vm.group(2))
                info["height"] = int(vm.group(3))
        except Exception:
            pass
    return info


def generate_demo_beat_track(output_path, duration=300):
    """Generate a pleasant 120 BPM musical chord & rhythm track for testing seamless transitions."""
    ffmpeg_bin = ensure_ffmpeg()
    output_path = Path(output_path)
    sample_rate = 22050
    total_samples = int(duration * sample_rate)

    # Am7 -> Fmaj7 -> Cmaj7 -> G6 chord progression (2.0s per bar at 120 BPM)
    chords = [
        [220.00, 261.63, 329.63, 392.00],  # Am7
        [174.61, 220.00, 261.63, 329.63],  # Fmaj7
        [261.63, 329.63, 392.00, 493.88],  # Cmaj7
        [196.00, 246.94, 293.66, 329.63],  # G6
    ]
    arp_order = [0, 1, 2, 3, 2, 1, 3, 2]

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_wav = Path(tmp.name)

    try:
        with wave.open(str(tmp_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)

            chunk_size = 4096
            two_pi = 2.0 * math.pi
            for start_idx in range(0, total_samples, chunk_size):
                end_idx = min(total_samples, start_idx + chunk_size)
                buf = bytearray()
                for n in range(start_idx, end_idx):
                    t = n / sample_rate
                    bar_pos = (t / 2.0) % 4.0
                    chord = chords[int(bar_pos)]
                    beat_phase = (t * 2.0) % 1.0  # 120 BPM = 2 beats/sec
                    eighth_idx = int((t * 4.0) % 8.0)
                    eighth_phase = (t * 4.0) % 1.0

                    # Soft kick pulse on every beat
                    kick_env = math.exp(-beat_phase * 14.0)
                    kick_freq = 55.0 + 95.0 * math.exp(-beat_phase * 28.0)
                    kick = 0.36 * kick_env * math.sin(two_pi * kick_freq * beat_phase / 2.0)

                    # Warm sustained pad
                    pad = sum(math.sin(two_pi * f * t) for f in chord) * 0.085

                    # Rhythmic chord arpeggio note on 8th notes so cut drift is immediately audible
                    arp_f = chord[arp_order[eighth_idx]] * 2.0
                    arp_env = math.exp(-eighth_phase * 6.5)
                    arp = 0.17 * arp_env * math.sin(two_pi * arp_f * t)

                    # Subtle shaker tick on off-beats
                    tick_env = math.exp(-eighth_phase * 30.0) if (eighth_idx % 2 == 1) else 0.0
                    tick = 0.04 * tick_env * math.sin(two_pi * 3400.0 * t)

                    val = kick + pad + arp + tick
                    sample = max(-32767, min(32767, int(val * 28000)))
                    buf.extend(struct.pack("<h", sample))
                wf.writeframes(bytes(buf))

        run_cmd(
            [
                ffmpeg_bin,
                "-y",
                "-i",
                str(tmp_wav),
                "-c:a",
                "libmp3lame",
                "-b:a",
                "192k",
                str(output_path),
            ]
        )
    finally:
        if tmp_wav.exists():
            tmp_wav.unlink()
    return output_path


def find_local_audio_files(search_dirs=None):
    """Discover audio files available in the working folder and workspace."""
    dirs = search_dirs or [Path.cwd(), SCRIPT_DIR, WORK_DIR]
    seen = set()
    found = []
    for d in dirs:
        if not d.exists():
            continue
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS:
                resolved = f.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    found.append(f)
    return found


def resolve_audio_file(safe_name: str) -> Optional[Path]:
    """Resolve an existing local audio file by strict basename."""
    for f in find_local_audio_files():
        if f.name == safe_name and f.is_file():
            return f
    return None


def normalize_cookies_from_browser(raw: Optional[str]) -> Optional[str]:
    """Validate an optional yt-dlp --cookies-from-browser token without bypassing bot checks."""
    if raw is None:
        return None
    token = str(raw).strip()
    if not token:
        return None
    if token.startswith("-") or any(c.isspace() or ord(c) < 32 for c in token):
        raise ValueError("Invalid cookies_from_browser value.")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*(?:[+:][A-Za-z0-9._:/+-]+)?", token):
        raise ValueError("Unsupported characters in cookies_from_browser.")
    return token


def validate_playlist_url(raw_url: Optional[str]) -> str:
    """Validate playlist_url is an http(s) URL without leading '-' or control/whitespace characters."""
    if not isinstance(raw_url, str):
        raise ValueError("Invalid playlist_url: expected URL string.")
    url = raw_url.strip()
    if not url:
        raise ValueError("Invalid playlist_url: URL cannot be empty.")
    if url.startswith("-") or any(ord(c) < 32 or c.isspace() for c in url):
        raise ValueError("Invalid playlist_url: forbidden leading '-' or control/whitespace characters.")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise ValueError("Invalid playlist_url: must be an http:// or https:// URL.")
    return url


def resolve_song_path(configured_path, interactive=True, log_cb=print):
    """Find the song file automatically, or open a file picker if none exists."""
    p = Path(configured_path)
    if p.exists():
        return p

    local_files = find_local_audio_files()
    # Prefer user songs over sample_song.mp3
    preferred = [f for f in local_files if f.name.lower() != "sample_song.mp3"]
    candidates = preferred or local_files
    if len(candidates) == 1:
        log_cb(f"[Audio] Auto-detected song: {candidates[0].name}")
        return candidates[0]
    if len(candidates) > 1:
        if not interactive:
            log_cb(f"[Audio] Auto-selected song: {candidates[0].name}")
            return candidates[0]
        print("\nMultiple audio files found in folder:")
        for idx, f in enumerate(candidates, 1):
            print(f"  {idx}. {f.name}")
        choice = input(f"Pick a number [1-{len(candidates)}] (default 1): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            return candidates[int(choice) - 1]
        return candidates[0]

    # Try native Windows/desktop file picker if interactive
    if interactive and (os.name == "nt" or os.environ.get("DISPLAY")):
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            picked = filedialog.askopenfilename(
                title="Select Your Full Song Audio (.mp3, .wav, .m4a)",
                filetypes=[
                    ("Audio files", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg"),
                    ("All files", "*.*"),
                ],
            )
            root.destroy()
            if picked:
                return Path(picked)
        except Exception:
            pass

    raise FileNotFoundError(
        f"Song not found at '{configured_path}'. Place an .mp3/.wav file in this folder or pass --song <path>."
    )


def list_source_videos(source_dir):
    """Return sorted list of source video clips."""
    if not Path(source_dir).exists():
        return []
    return sorted(
        [f for f in Path(source_dir).iterdir() if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS]
    )


def download_playlist_shorts(
    playlist_url,
    output_dir,
    max_downloads=10,
    skip_if_exists=False,
    log_cb=print,
    cookies_from_browser: Optional[str] = None,
):
    """Download existing Shorts from the target YouTube playlist in order."""
    playlist_url = validate_playlist_url(playlist_url)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    existing = list_source_videos(output_dir)
    if skip_if_exists and existing:
        log_cb(f"[1/4] Reusing {len(existing)} already-downloaded Shorts in {output_dir}...")
        return existing

    yt_dlp_cmd = ensure_yt_dlp(log_cb=log_cb)
    log_cb("[1/4] Downloading Shorts from playlist...")

    # Clear incomplete .part files before downloading
    for part in output_dir.glob("*.part"):
        try:
            part.unlink()
        except Exception:
            pass

    output_template = str(output_dir / "%(playlist_index)02d_%(id)s.%(ext)s")
    cmd = [
        *yt_dlp_cmd,
        "--ignore-errors",
        "--yes-playlist",
    ]
    if max_downloads and int(max_downloads) > 0:
        cmd.extend(["--playlist-end", str(int(max_downloads))])
    browser_token = normalize_cookies_from_browser(cookies_from_browser)
    if browser_token:
        cmd.extend(["--cookies-from-browser", browser_token])
    if shutil.which("node"):
        cmd.extend(["--js-runtimes", "node"])

    # Prefer H.264 MP4 when available for universal browser/device compatibility
    cmd.extend(
        [
            "-f",
            "bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]/bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
            "--merge-output-format",
            "mp4",
            "-o",
            output_template,
            "--",
            playlist_url,
        ]
    )

    res = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    files = list_source_videos(output_dir)
    log_cb(f"      Available source Shorts: {len(files)}")
    if not files:
        err_tail = (res.stderr or res.stdout or "").strip()[-600:]
        raise RuntimeError(
            "No Shorts were downloaded. Check the playlist URL or drop .mp4 files directly into "
            f"'{output_dir}'.\n{err_tail}"
        )
    return files


def process_seamless_shorts(
    short_files,
    song_path,
    output_dir,
    fade_duration=FADE_DURATION,
    browser_safe_h264=False,
    log_cb=print,
):
    """Slice the song to match each Short's exact duration and mux them together."""
    ffmpeg_bin = ensure_ffmpeg(log_cb=log_cb)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    song_path = Path(song_path)
    if not song_path.exists():
        raise FileNotFoundError(f"Master song not found at: {song_path}")

    # Remove old generated parts so previous runs don't pollute this batch
    for old_file in output_dir.glob("Part_*.mp4"):
        try:
            old_file.unlink()
        except Exception:
            pass

    song_duration = get_audio_stream_duration(song_path)
    log_cb(f"[2/4] Master song: {song_path.name} ({song_duration:.2f}s / {song_duration/60:.2f} mins)")

    current_audio_time = 0.0
    output_files = []
    parts_meta = []
    part_num = 1

    log_cb("[3/4] Slicing audio and syncing to Shorts...")
    for video_path in short_files:
        if current_audio_time >= song_duration - 0.05:
            log_cb("      Reached the end of the master song! Stopping here.")
            break

        video_duration = get_video_stream_duration(video_path)
        remaining_song = song_duration - current_audio_time
        if remaining_song <= 0.1:
            log_cb("      Reached the end of the master song! Stopping here.")
            break
        if video_duration <= 0.1:
            log_cb(f"      Skipping tiny clip ({video_duration:.3f}s): {Path(video_path).name}")
            continue
        segment_duration = min(video_duration, remaining_song)
        if segment_duration <= 0.1:
            continue

        fade_d = min(float(fade_duration), segment_duration / 3.0)
        fade_out_start = max(0.0, segment_duration - fade_d)

        output_filename = f"Part_{part_num:02d}_{video_path.stem}.mp4"
        output_path = output_dir / output_filename

        log_cb(
            f"      -> {output_filename} | Song slice: {current_audio_time:.3f}s -> "
            f"{current_audio_time + segment_duration:.3f}s ({segment_duration:.3f}s)"
        )

        fast_seek = max(0.0, current_audio_time - 2.0)
        fine_seek = current_audio_time - fast_seek
        audio_filter = (
            f"atrim=start={fine_seek:.6f}:duration={segment_duration:.6f},"
            f"asetpts=PTS-STARTPTS,"
            f"aresample=async=1:first_pts=0,"
            f"afade=t=in:st=0:d={fade_d:.4f},"
            f"afade=t=out:st={fade_out_start:.4f}:d={fade_d:.4f},"
            f"apad=whole_dur={segment_duration:.6f},"
            f"atrim=duration={segment_duration:.6f}"
        )

        if browser_safe_h264:
            video_args = [
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p",
            ]
        else:
            video_args = ["-c:v", "copy"]

        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(video_path),
            "-ss",
            f"{fast_seek:.4f}",
            "-t",
            f"{fine_seek + segment_duration + 0.5:.4f}",
            "-i",
            str(song_path),
            "-t",
            f"{segment_duration:.4f}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            *video_args,
            "-c:a",
            "aac",
            "-b:a",
            "320k",
            "-af",
            audio_filter,
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        run_cmd(cmd)

        vinfo = get_video_info(output_path)
        actual_dur = vinfo["duration"] or segment_duration
        parts_meta.append(
            {
                "part_num": part_num,
                "filename": output_filename,
                "source_filename": video_path.name,
                "duration": round(segment_duration, 3),
                "actual_duration": round(actual_dur, 3),
                "song_start": round(current_audio_time, 3),
                "song_end": round(current_audio_time + segment_duration, 3),
                "verify_start": round(current_audio_time, 3),
                "verify_end": round(current_audio_time + segment_duration, 3),
                "codec": vinfo["codec"],
                "width": vinfo["width"],
                "height": vinfo["height"],
                "size_bytes": vinfo["size_bytes"],
            }
        )

        output_files.append(output_path)
        current_audio_time += segment_duration
        part_num += 1

    return output_files, parts_meta


def verify_seamless_playback(output_files, verify_path, parts_meta=None, log_cb=print):
    """Concatenate the generated Shorts into a single test video atomically with zero cumulative drift."""
    ffmpeg_bin = ensure_ffmpeg(log_cb=log_cb)
    verify_path = Path(verify_path)
    verify_path.parent.mkdir(parents=True, exist_ok=True)
    log_cb("[4/4] Building H.264 verification playthrough file...")

    # Normalize dimensions, pixel format, SAR, and trim each stream to its planned duration before concat;
    # apply fps=30 once after concat so frame rounding does not accumulate drift across clips.
    inputs = []
    filter_parts = []
    for i, file_path in enumerate(output_files):
        inputs.extend(["-i", str(file_path)])
        seg_dur = None
        if parts_meta and i < len(parts_meta) and parts_meta[i].get("duration"):
            try:
                seg_dur = float(parts_meta[i]["duration"])
            except Exception:
                seg_dur = None
        if not seg_dur or seg_dur <= 0:
            try:
                seg_dur = get_video_stream_duration(file_path)
            except Exception:
                seg_dur = 0.0

        v_trim = f"trim=duration={seg_dur:.6f},setpts=PTS-STARTPTS," if seg_dur and seg_dur > 0 else "setpts=PTS-STARTPTS,"
        a_trim = (
            f"atrim=duration={seg_dur:.6f},asetpts=PTS-STARTPTS,"
            f"aresample=async=1:first_pts=0,apad=whole_dur={seg_dur:.6f},atrim=duration={seg_dur:.6f}"
            if seg_dur and seg_dur > 0
            else "asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0"
        )
        filter_parts.append(
            f"[{i}:v]{v_trim}scale=1080:1920:force_original_aspect_ratio=decrease,"
            f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v{i}];"
            f"[{i}:a]{a_trim}[a{i}];"
        )

    v_concat = "".join([f"[v{i}][a{i}]" for i in range(len(output_files))])
    filter_complex = (
        "".join(filter_parts)
        + f"{v_concat}concat=n={len(output_files)}:v=1:a=1[v_cat][a];"
        + "[v_cat]fps=30,format=yuv420p[v]"
    )

    tmp_verify_path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=str(verify_path.parent),
            prefix=f".{verify_path.stem}_",
            suffix=verify_path.suffix or ".mp4",
            delete=False,
        ) as tf:
            tmp_verify_path = Path(tf.name)

        cmd = [
            ffmpeg_bin,
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "26",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(tmp_verify_path),
        ]
        run_cmd(cmd)
        os.replace(tmp_verify_path, verify_path)
    finally:
        if tmp_verify_path and tmp_verify_path.exists():
            try:
                tmp_verify_path.unlink()
            except Exception:
                pass

    log_cb(f"\nDONE! Generated {len(output_files)} synced Shorts in: {OUTPUT_DIR}")
    log_cb(f"TEST FILE: '{verify_path}'")
    return verify_path


MANIFEST_LOCK = threading.Lock()


def _write_manifest_atomic(manifest_data, manifest_path=None):
    """Write manifest JSON atomically using a temporary file and os.replace."""
    target = Path(manifest_path) if manifest_path is not None else Path(MANIFEST_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=".manifest_",
            suffix=".tmp",
            delete=False,
        ) as tf:
            tmp_path = tf.name
            json.dump(manifest_data, tf, indent=2)
            tf.flush()
        os.replace(tmp_path, target)
    except Exception:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        raise


def save_manifest(manifest_data, manifest_path=None):
    """Persist run metadata atomically for the Studio UI and player."""
    with MANIFEST_LOCK:
        _write_manifest_atomic(manifest_data, manifest_path)


def _load_or_build_manifest_unlocked(allow_rebuild=True):
    """Load manifest.json or reconstruct it from existing workspace files (caller holds MANIFEST_LOCK)."""
    manifest_file = Path(MANIFEST_FILE)
    if manifest_file.exists():
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    parts = []
    if allow_rebuild and OUTPUT_DIR.exists():
        running_t = 0.0
        for idx, p in enumerate(sorted(OUTPUT_DIR.glob("Part_*.mp4")), 1):
            info = get_video_info(p)
            dur = info["duration"]
            parts.append(
                {
                    "part_num": idx,
                    "filename": p.name,
                    "source_filename": re.sub(r"^Part_\d+_", "", p.name),
                    "duration": round(dur, 3),
                    "actual_duration": round(dur, 3),
                    "song_start": round(running_t, 3),
                    "song_end": round(running_t + dur, 3),
                    "verify_start": round(running_t, 3),
                    "verify_end": round(running_t + dur, 3),
                    "codec": info["codec"],
                    "width": info["width"],
                    "height": info["height"],
                    "size_bytes": info["size_bytes"],
                }
            )
            running_t += dur

    song_file = Path(MASTER_SONG_PATH)
    is_demo = song_file.name == "sample_song.mp3"
    if song_file.exists() and Path("sample_song.mp3").exists():
        try:
            if song_file.stat().st_size == Path("sample_song.mp3").stat().st_size:
                is_demo = True
        except Exception:
            pass

    data = {
        "playlist_url": PLAYLIST_URL,
        "song_path": str(song_file) if song_file.exists() else "",
        "song_name": song_file.name if song_file.exists() else "",
        "is_demo_song": is_demo,
        "fade_ms": int(FADE_DURATION * 1000),
        "updated_at": time.time(),
        "parts": parts,
    }
    if allow_rebuild and parts:
        _write_manifest_atomic(data, manifest_file)
    return data


def load_or_build_manifest(allow_rebuild=True):
    """Load manifest.json or reconstruct it from existing workspace files."""
    with MANIFEST_LOCK:
        return _load_or_build_manifest_unlocked(allow_rebuild=allow_rebuild)


def run_pipeline(
    playlist_url=PLAYLIST_URL,
    song_path=MASTER_SONG_PATH,
    max_downloads=10,
    fade_ms=15,
    skip_download=False,
    browser_safe_h264=False,
    interactive=False,
    progress_cb=None,
    log_cb=print,
    cookies_from_browser: Optional[str] = None,
):
    """Run the full 4-step Shorts sync and verification pipeline."""
    def set_progress(step, pct):
        if progress_cb:
            progress_cb(step, pct)

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    ensure_ffmpeg(log_cb=log_cb)

    set_progress("Preparing audio track...", 5)
    resolved_song = resolve_song_path(song_path, interactive=interactive, log_cb=log_cb)

    set_progress("Downloading playlist Shorts...", 15)
    shorts = download_playlist_shorts(
        playlist_url,
        SOURCE_DIR,
        max_downloads=max_downloads,
        skip_if_exists=skip_download,
        log_cb=log_cb,
        cookies_from_browser=cookies_from_browser,
    )

    set_progress("Slicing & syncing audio to Shorts...", 45)
    repurposed, parts_meta = process_seamless_shorts(
        shorts,
        resolved_song,
        OUTPUT_DIR,
        fade_duration=max(0.0, float(fade_ms) / 1000.0),
        browser_safe_h264=browser_safe_h264,
        log_cb=log_cb,
    )

    if repurposed:
        set_progress("Stitching verification playthrough...", 75)
        verify_seamless_playback(repurposed, VERIFY_FILE, parts_meta=parts_meta, log_cb=log_cb)

    is_demo = resolved_song.name.lower() == "sample_song.mp3"
    if Path("sample_song.mp3").exists() and resolved_song.exists():
        try:
            if resolved_song.resolve() == Path("sample_song.mp3").resolve() or (
                resolved_song.stat().st_size == Path("sample_song.mp3").stat().st_size
                and resolved_song.read_bytes()[:256] == Path("sample_song.mp3").read_bytes()[:256]
            ):
                is_demo = True
        except Exception:
            pass

    manifest = {
        "playlist_url": playlist_url,
        "song_path": str(resolved_song),
        "song_name": resolved_song.name,
        "is_demo_song": is_demo,
        "fade_ms": int(fade_ms),
        "updated_at": time.time(),
        "parts": parts_meta,
    }
    save_manifest(manifest)
    set_progress("Done!", 100)
    return manifest


# ================= WEB STUDIO & PLAYER =================

class JobState:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.step = "Ready"
        self.progress = 0
        self.logs = []
        self.error = None
        self.manifest = load_or_build_manifest()

    def add_log(self, msg):
        with self.lock:
            for line in str(msg).splitlines():
                self.logs.append(line)
            if len(self.logs) > 250:
                self.logs = self.logs[-250:]

    def set_progress(self, step, pct):
        with self.lock:
            self.step = step
            self.progress = pct


JOB_STATE = JobState()


STUDIO_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Seamless Shorts Studio & Player</title>
  <style>
    :root {
      --bg: #0b1120;
      --panel: #111827;
      --card: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --accent-hover: #0ea5e9;
      --green: #22c55e;
      --green-hover: #16a34a;
      --amber: #f59e0b;
      --red: #ef4444;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.45;
    }
    .topbar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 24px;
      background: var(--panel);
      border-bottom: 1px solid var(--border);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .brand h1 {
      font-size: 1.15rem;
      margin: 0;
      color: var(--accent);
    }
    .brand span {
      font-size: 0.85rem;
      color: var(--muted);
    }
    .status-pills {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }
    .pill {
      font-size: 0.78rem;
      padding: 4px 10px;
      border-radius: 999px;
      background: var(--card);
      border: 1px solid var(--border);
      color: var(--muted);
    }
    .pill.ok { color: #86efac; border-color: #166534; background: rgba(22, 101, 52, 0.25); }
    .pill.warn { color: #fde68a; border-color: #92400e; background: rgba(146, 64, 14, 0.25); }

    .layout {
      max-width: 1280px;
      margin: 0 auto;
      padding: 20px;
      display: grid;
      grid-template-columns: 420px 1fr;
      gap: 20px;
      align-items: start;
    }
    @media (max-width: 920px) {
      .layout { grid-template-columns: 1fr; }
    }

    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 18px;
      box-shadow: 0 12px 28px rgba(0, 0, 0, 0.35);
    }
    .card h2 {
      font-size: 1rem;
      margin: 0 0 12px 0;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }

    .player-wrap {
      background: #000;
      border-radius: 12px;
      overflow: hidden;
      border: 1px solid var(--border);
      display: flex;
      justify-content: center;
      align-items: center;
      position: relative;
    }
    video {
      width: 100%;
      max-height: 62vh;
      aspect-ratio: 9 / 16;
      object-fit: contain;
      background: #000;
      display: block;
    }
    .video-caption {
      margin-top: 10px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      font-size: 0.85rem;
      color: var(--muted);
    }
    .video-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }

    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      padding: 9px 14px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--card);
      color: var(--text);
      font-size: 0.86rem;
      font-weight: 600;
      cursor: pointer;
      text-decoration: none;
      transition: 0.15s ease;
    }
    .btn:hover { border-color: var(--accent); color: var(--accent); }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .btn-primary {
      background: var(--green);
      border-color: var(--green);
      color: #052e16;
      width: 100%;
      padding: 12px 16px;
      font-size: 0.96rem;
    }
    .btn-primary:hover { background: var(--green-hover); border-color: var(--green-hover); color: #052e16; }
    .btn-accent {
      background: var(--accent);
      border-color: var(--accent);
      color: #082f49;
    }
    .btn-accent:hover { background: var(--accent-hover); border-color: var(--accent-hover); color: #082f49; }
    .btn-sm { padding: 5px 10px; font-size: 0.78rem; border-radius: 6px; }

    .banner {
      padding: 10px 12px;
      border-radius: 10px;
      font-size: 0.84rem;
      margin-bottom: 14px;
      border: 1px solid;
    }
    .banner-info {
      background: rgba(56, 189, 248, 0.1);
      border-color: rgba(56, 189, 248, 0.35);
      color: #bae6fd;
    }
    .banner-warn {
      background: rgba(245, 158, 11, 0.12);
      border-color: rgba(245, 158, 11, 0.4);
      color: #fde68a;
    }

    .field { margin-bottom: 14px; }
    .field label {
      display: block;
      font-size: 0.82rem;
      font-weight: 600;
      color: #cbd5e1;
      margin-bottom: 6px;
    }
    input[type="text"], input[type="number"], select {
      width: 100%;
      padding: 9px 12px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--card);
      color: var(--text);
      font-size: 0.88rem;
      outline: none;
    }
    input[type="text"]:focus, input[type="number"]:focus, select:focus {
      border-color: var(--accent);
    }
    .row2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }

    .dropzone {
      border: 2px dashed var(--border);
      border-radius: 10px;
      padding: 14px;
      text-align: center;
      background: rgba(30, 41, 59, 0.55);
      cursor: pointer;
      transition: 0.15s ease;
    }
    .dropzone.dragover, .dropzone:hover {
      border-color: var(--accent);
      background: rgba(56, 189, 248, 0.08);
    }
    .dropzone p { margin: 0; font-size: 0.85rem; color: var(--muted); }
    .dropzone strong { color: var(--accent); }

    .checks {
      display: flex;
      flex-direction: column;
      gap: 8px;
      margin-bottom: 14px;
      font-size: 0.84rem;
      color: #cbd5e1;
    }
    .checks label {
      display: flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
    }

    .progress-box {
      margin-top: 12px;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px;
    }
    .progress-bar-bg {
      height: 8px;
      border-radius: 999px;
      background: #0f172a;
      overflow: hidden;
      margin: 8px 0;
    }
    .progress-bar-fill {
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, var(--accent), var(--green));
      transition: width 0.25s ease;
    }
    .logbox {
      background: #020617;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.76rem;
      color: #cbd5e1;
      max-height: 160px;
      overflow-y: auto;
      white-space: pre-wrap;
    }

    .parts-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 10px;
      margin-top: 12px;
    }
    .part-card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 11px;
      transition: 0.15s ease;
    }
    .part-card.active {
      border-color: var(--accent);
      box-shadow: 0 0 0 1px var(--accent);
      background: rgba(56, 189, 248, 0.1);
    }
    .part-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-weight: 700;
      font-size: 0.88rem;
      margin-bottom: 4px;
    }
    .part-meta {
      font-size: 0.78rem;
      color: var(--muted);
      margin-bottom: 8px;
    }
    .part-btns {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand">
      <h1>Seamless Shorts Studio</h1>
      <span>One-Click Song Sync &amp; Verification Player</span>
    </div>
    <div class="status-pills" id="toolPills">
      <span class="pill">Checking tools...</span>
    </div>
  </header>

  <main class="layout">
    <!-- Left column: Video Player -->
    <section class="card">
      <h2>
        <span id="playerTitle">Full Playthrough Verification</span>
        <button class="btn btn-sm" id="backToFullBtn" style="display:none;" onclick="loadFullVerify()">Back to Full Video</button>
      </h2>

      <div id="demoBanner" class="banner banner-warn" style="display:none;">
        Currently playing with the <strong>built-in Demo Beat Track</strong> so you can hear continuous tempo across cuts. Upload your own <code>.mp3</code> / <code>.wav</code> on the right and click <strong>Run &amp; Sync</strong> to replace it!
      </div>

      <div class="player-wrap">
        <video id="videoPlayer" controls playsinline preload="metadata"></video>
      </div>

      <div class="video-caption">
        <span id="nowPlayingText">Ready</span>
        <span id="videoTimeText">0:00 / 0:00</span>
      </div>

      <div class="video-actions">
        <a class="btn btn-accent btn-sm" id="downloadVerifyBtn" href="media/verify?download=1" download="3_verification_full_playthrough.mp4">Download Playthrough (.mp4)</a>
        <a class="btn btn-sm" id="downloadAllBtn" href="download/all.zip">Download All Shorts (.zip)</a>
        <a class="btn btn-sm" href="download/toolkit.zip">Get Windows 1-Click Kit (.zip)</a>
      </div>
    </section>

    <!-- Right column: Simple 3-Step Studio + Cut-Point Navigator -->
    <div style="display:flex; flex-direction:column; gap:20px;">
      <section class="card">
        <h2>3-Step Song Sync Studio</h2>
        <div class="banner banner-info">
          <strong>Super easy:</strong> Paste any YouTube Shorts playlist, drop your song file, and click Run. If you already downloaded the Shorts, keep <em>"Reuse downloaded Shorts"</em> checked to swap songs in seconds!
        </div>

        <div class="field">
          <label for="playlistUrl">1. YouTube Playlist URL</label>
          <input id="playlistUrl" type="text" placeholder="https://www.youtube.com/playlist?list=..." />
        </div>

        <div class="field">
          <label>2. Your Full Song (.mp3, .wav, .m4a)</label>
          <div class="dropzone" id="dropzone" onclick="document.getElementById('songFileInput').click()">
            <p><strong>Click to upload a song</strong> or drag &amp; drop an audio file here</p>
            <p id="selectedSongLabel" style="margin-top:4px; color:#e2e8f0;"></p>
          </div>
          <input id="songFileInput" type="file" accept="audio/*,.mp3,.wav,.m4a,.aac,.flac,.ogg" style="display:none;" />
          <div style="margin-top:8px;">
            <select id="songSelect"></select>
          </div>
        </div>

        <div class="row2 field">
          <div>
            <label for="maxDownloads">Max Shorts (0 = all)</label>
            <input id="maxDownloads" type="number" min="0" max="100" value="10" />
          </div>
          <div>
            <label for="fadeMs">Transition Fade (ms)</label>
            <input id="fadeMs" type="number" min="0" max="200" value="15" />
          </div>
        </div>

        <div class="checks">
          <label><input type="checkbox" id="skipDownload" checked /> Reuse already-downloaded Shorts if present (Fast re-sync)</label>
          <label><input type="checkbox" id="browserSafe" /> Re-encode individual Parts to H.264 (slower, best compatibility for Safari/iPhone)</label>
        </div>

        <button class="btn btn-primary" id="runBtn" onclick="startRun()">Run &amp; Sync Shorts</button>

        <div class="progress-box">
          <div style="display:flex; justify-content:space-between; font-size:0.83rem;">
            <strong id="stepLabel">Idle</strong>
            <span id="pctLabel">0%</span>
          </div>
          <div class="progress-bar-bg">
            <div class="progress-bar-fill" id="progressFill"></div>
          </div>
          <div class="logbox" id="logBox">Ready.</div>
        </div>
      </section>

      <section class="card">
        <h2>
          <span>Synced Shorts &amp; Transition Cut Points</span>
          <span id="partsSummary" style="font-size:0.82rem; color:var(--muted); font-weight:500;"></span>
        </h2>
        <div class="parts-grid" id="partsGrid"></div>
      </section>
    </div>
  </main>

  <script>
    const player = document.getElementById('videoPlayer');
    const partsGrid = document.getElementById('partsGrid');
    const songSelect = document.getElementById('songSelect');
    const dropzone = document.getElementById('dropzone');
    const songFileInput = document.getElementById('songFileInput');

    let currentMode = 'verify'; // 'verify' | 'part'
    let currentVerifyVersion = '';
    let stateData = null;

    function fmtTime(sec) {
      sec = Math.max(0, Number(sec) || 0);
      const m = Math.floor(sec / 60);
      const s = Math.floor(sec % 60);
      return `${m}:${String(s).padStart(2, '0')}`;
    }

    function fmtPrecise(sec) {
      sec = Math.max(0, Number(sec) || 0);
      const m = Math.floor(sec / 60);
      const s = (sec % 60).toFixed(1).padStart(4, '0');
      return `${m}:${s}`;
    }

    function loadFullVerify(seekSec = null) {
      currentMode = 'verify';
      document.getElementById('playerTitle').textContent = 'Full Playthrough Verification';
      document.getElementById('backToFullBtn').style.display = 'none';
      const targetSrc = `media/verify?v=${encodeURIComponent(currentVerifyVersion || Date.now())}`;
      if (!player.getAttribute('src') || !player.getAttribute('src').startsWith('media/verify') || player.dataset.ver !== String(currentVerifyVersion)) {
        player.dataset.ver = String(currentVerifyVersion);
        player.src = targetSrc;
        player.load();
      }
      if (seekSec !== null) {
        const jump = Math.max(0, seekSec - 2.0);
        const doSeek = () => {
          player.currentTime = jump;
          player.play().catch(() => {});
        };
        if (player.readyState >= 1) doSeek();
        else player.addEventListener('loadedmetadata', doSeek, { once: true });
      }
      document.getElementById('nowPlayingText').textContent = 'Playing stitched 5-min verification video';
    }

    function playSinglePart(filename, partNum) {
      currentMode = 'part';
      document.getElementById('playerTitle').textContent = `Clip Preview: Part ${partNum}`;
      document.getElementById('backToFullBtn').style.display = 'inline-flex';
      player.src = `media/part/${encodeURIComponent(filename)}?v=${encodeURIComponent(currentVerifyVersion)}`;
      player.load();
      player.play().catch(() => {});
      document.getElementById('nowPlayingText').textContent = filename;
    }

    player.addEventListener('error', () => {
      if (currentMode === 'part') {
        document.getElementById('nowPlayingText').textContent =
          'This standalone clip uses a codec your browser blocked — click "Back to Full Video" to play the H.264 stream, or Download .mp4.';
      }
    });

    player.addEventListener('timeupdate', () => {
      const cur = player.currentTime || 0;
      const dur = player.duration || 0;
      document.getElementById('videoTimeText').textContent = `${fmtTime(cur)} / ${fmtTime(dur)}`;
      if (currentMode === 'verify' && stateData && stateData.parts) {
        document.querySelectorAll('.part-card').forEach(card => {
          const s = parseFloat(card.dataset.start);
          const e = parseFloat(card.dataset.end);
          card.classList.toggle('active', cur >= s && cur < e);
        });
      }
    });

    function escapeHtml(str) {
      return String(str == null ? '' : str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function renderParts(parts) {
      partsGrid.innerHTML = '';
      if (!parts || parts.length === 0) {
        partsGrid.innerHTML = '<p style="color:var(--muted); font-size:0.86rem;">No synced Shorts generated yet. Click "Run & Sync Shorts" above!</p>';
        document.getElementById('partsSummary').textContent = '';
        return;
      }
      const totalDur = parts.reduce((acc, p) => acc + (Number(p.duration) || 0), 0);
      document.getElementById('partsSummary').textContent = `${parts.length} clips • ${fmtTime(totalDur)} total`;

      parts.forEach((p, idx) => {
        const partNum = Number(p.part_num) || 0;
        const dur = Number(p.duration) || 0;
        const verifyStart = Number(p.verify_start) || 0;
        const verifyEnd = Number(p.verify_end) || 0;
        const rawFilename = String(p.filename || '');

        const card = document.createElement('div');
        card.className = 'part-card';
        card.dataset.start = String(verifyStart);
        card.dataset.end = String(verifyEnd);

        const cutBtnLabel = idx > 0 ? 'Hear Cut (-2s)' : 'Play from Start';
        card.innerHTML = `
          <div class="part-top">
            <span>Part ${partNum}</span>
            <span style="color:var(--accent);">${dur.toFixed(2)}s</span>
          </div>
          <div class="part-meta">
            Song: ${fmtPrecise(p.song_start)} &rarr; ${fmtPrecise(p.song_end)}<br>
            Clip: ${escapeHtml(rawFilename)}
          </div>
          <div class="part-btns">
            <button type="button" class="btn btn-accent btn-sm" data-act="cut">${cutBtnLabel}</button>
            <button type="button" class="btn btn-sm" data-act="solo">Solo Clip</button>
            <a class="btn btn-sm" data-act="dl">Download</a>
          </div>
        `;
        card.querySelector('[data-act="cut"]').addEventListener('click', () => {
          loadFullVerify(idx > 0 ? verifyStart : 0);
        });
        card.querySelector('[data-act="solo"]').addEventListener('click', () => {
          playSinglePart(rawFilename, partNum);
        });
        const dlEl = card.querySelector('[data-act="dl"]');
        dlEl.href = `media/part/${encodeURIComponent(rawFilename)}?download=1`;
        dlEl.setAttribute('download', rawFilename || 'part.mp4');
        partsGrid.appendChild(card);
      });
    }

    async function uploadSongFile(file) {
      if (!file) return;
      document.getElementById('selectedSongLabel').textContent = `Uploading ${file.name}...`;
      try {
        const resp = await fetch('api/upload-song', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/octet-stream',
            'X-Filename': encodeURIComponent(file.name),
          },
          body: file,
        });
        const res = await resp.json();
        if (!resp.ok || res.error) throw new Error(res.error || 'Upload failed');
        document.getElementById('selectedSongLabel').textContent = `Active song: ${res.song_name}`;
        await refreshState(true);
      } catch (err) {
        document.getElementById('selectedSongLabel').textContent = `Upload error: ${err.message}`;
      }
    }

    songFileInput.addEventListener('change', (e) => uploadSongFile(e.target.files[0]));
    dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
    dropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropzone.classList.remove('dragover');
      if (e.dataTransfer.files && e.dataTransfer.files[0]) {
        uploadSongFile(e.dataTransfer.files[0]);
      }
    });

    async function startRun() {
      const btn = document.getElementById('runBtn');
      btn.disabled = true;
      try {
        const payload = {
          playlist_url: document.getElementById('playlistUrl').value.trim(),
          song_path: songSelect.value,
          max_downloads: parseInt(document.getElementById('maxDownloads').value || '10', 10),
          fade_ms: parseInt(document.getElementById('fadeMs').value || '15', 10),
          skip_download: document.getElementById('skipDownload').checked,
          browser_safe_h264: document.getElementById('browserSafe').checked,
        };
        const resp = await fetch('api/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok || data.error) {
          alert(data.error || 'Could not start job');
          btn.disabled = false;
        }
      } catch (e) {
        alert(e.message);
        btn.disabled = false;
      }
    }

    let firstInit = true;
    let wasRunning = false;

    async function refreshState(forceSelectLatest = false) {
      try {
        const resp = await fetch('api/state');
        const data = await resp.json();
        stateData = data;

        // Tool pills
        const pills = [];
        pills.push(`<span class="pill ${data.tools.ffmpeg ? 'ok' : 'warn'}">FFmpeg: ${data.tools.ffmpeg ? 'Ready' : 'Missing'}</span>`);
        pills.push(`<span class="pill ${data.tools.yt_dlp ? 'ok' : 'warn'}">yt-dlp: ${data.tools.yt_dlp ? 'Ready' : 'Auto-install'}</span>`);
        pills.push(`<span class="pill ok">Source Clips: ${data.source_count}</span>`);
        document.getElementById('toolPills').innerHTML = pills.join('');

        // Playlist input init
        const plInput = document.getElementById('playlistUrl');
        if (firstInit && !plInput.value) {
          plInput.value = data.playlist_url || '';
        }

        // Audio dropdown
        const prevChoice = songSelect.value;
        songSelect.innerHTML = '';
        (data.audio_files || []).forEach(f => {
          const opt = document.createElement('option');
          opt.value = f.path;
          opt.textContent = `${f.name} (${(f.size_bytes / 1048576).toFixed(1)} MB)`;
          songSelect.appendChild(opt);
        });
        if (!firstInit && !forceSelectLatest && prevChoice && [...songSelect.options].some(o => o.value === prevChoice)) {
          songSelect.value = prevChoice;
        } else if (data.active_song_path && [...songSelect.options].some(o => o.value === data.active_song_path)) {
          songSelect.value = data.active_song_path;
        }
        if (songSelect.selectedOptions[0]) {
          document.getElementById('selectedSongLabel').textContent = `Selected: ${songSelect.selectedOptions[0].textContent}`;
        }

        document.getElementById('skipDownload').disabled = data.source_count === 0;
        if (firstInit) {
          document.getElementById('skipDownload').checked = data.source_count > 0;
        }

        // Demo banner
        document.getElementById('demoBanner').style.display = data.is_demo_song ? 'block' : 'none';

        // Job status
        document.getElementById('runBtn').disabled = data.job.running;
        document.getElementById('runBtn').textContent = data.job.running ? 'Processing... Please wait' : 'Run & Sync Shorts';
        document.getElementById('stepLabel').textContent = data.job.error ? `Error: ${data.job.error}` : data.job.step;
        document.getElementById('pctLabel').textContent = `${data.job.progress}%`;
        document.getElementById('progressFill').style.width = `${data.job.progress}%`;

        const logBox = document.getElementById('logBox');
        const logsText = (data.job.logs || []).join('\n') || 'Ready.';
        if (logBox.textContent !== logsText) {
          logBox.textContent = logsText;
          logBox.scrollTop = logBox.scrollHeight;
        }

        // Reload video if newly completed or on initial load
        const newVer = String(data.verify_mtime || '');
        if (data.verify_exists && (firstInit || (wasRunning && !data.job.running) || (newVer && newVer !== currentVerifyVersion))) {
          currentVerifyVersion = newVer;
          loadFullVerify();
        }
        if (firstInit || (wasRunning && !data.job.running) || partsGrid.children.length === 0) {
          renderParts(data.parts);
        }

        wasRunning = data.job.running;
        firstInit = false;
      } catch (err) {
        // Ignore transient poll errors
      }
    }

    refreshState();
    setInterval(() => refreshState(), 1500);
  </script>
</body>
</html>
"""


def safe_media_basename(raw_segment: str) -> Optional[str]:
    """Strictly validate a URL path segment is a safe single basename (reject traversal before path join)."""
    if not raw_segment:
        return None
    low_raw = raw_segment.lower()
    if (
        "/" in raw_segment
        or "\\" in raw_segment
        or "\x00" in raw_segment
        or ":" in raw_segment
        or "%2f" in low_raw
        or "%5c" in low_raw
        or "%00" in low_raw
        or "%3a" in low_raw
    ):
        return None
    decoded = urllib.parse.unquote(raw_segment)
    if not decoded or decoded in (".", ".."):
        return None
    if (
        "/" in decoded
        or "\\" in decoded
        or "\x00" in decoded
        or ":" in decoded
        or os.path.isabs(decoded)
        or any(ord(c) < 32 for c in decoded)
    ):
        return None
    return decoded


def media_mime_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return MEDIA_MIME_TYPES.get(ext) or mimetypes.guess_type(filename)[0] or "application/octet-stream"


def parse_int_param(val, default: int, min_val: int = 0) -> int:
    if val is None or val == "":
        return default
    if isinstance(val, (bool, float)):
        raise ValueError("Expected integer value, got boolean or float.")
    if isinstance(val, int):
        n = val
    elif isinstance(val, str):
        s = val.strip()
        if not re.fullmatch(r"[+-]?\d+", s):
            raise ValueError(f"Expected integer value, got {val!r}.")
        n = int(s)
    else:
        raise ValueError("Expected integer value.")
    if n < min_val:
        raise ValueError(f"Value must be >= {min_val}.")
    return n


def parse_bool_param(val, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        low = val.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off", ""):
            return False
    raise ValueError("Expected boolean value.")


def make_handler():
    class StudioHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            pass  # Keep console clean

        def send_response(self, code, message=None):
            self._cors_sent = False
            super().send_response(code, message)

        def send_cors_headers(self):
            if getattr(self, "_cors_sent", False):
                return
            self._cors_sent = True
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Expose-Headers", "Content-Range, Accept-Ranges, Content-Length")

        def end_headers(self):
            self.send_cors_headers()
            super().end_headers()

        def send_bytes(
            self,
            body: bytes,
            content_type: str,
            status=HTTPStatus.OK,
            head_only: bool = False,
            cache_control: str = "no-store",
            download_name: Optional[str] = None,
        ):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if cache_control:
                self.send_header("Cache-Control", cache_control)
            if download_name:
                safe_dl = download_name.replace('"', "_")
                self.send_header("Content-Disposition", f'attachment; filename="{safe_dl}"')
            self.end_headers()
            if not head_only and body:
                self.wfile.write(body)

        def send_json(self, payload, status=200, head_only=False):
            body = json.dumps(payload).encode("utf-8")
            self.send_bytes(body, "application/json; charset=utf-8", status=status, head_only=head_only)

        def send_range_not_satisfiable(self, file_size: int):
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{file_size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def send_file_range(self, file_path, content_type=None, download_name=None, head_only=False):
            file_path = Path(file_path)
            if not file_path.exists() or not file_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "File not found")
                return

            file_size = file_path.stat().st_size
            ctype = content_type or media_mime_type(file_path.name)
            range_header = self.headers.get("Range")

            start = 0
            end = file_size - 1
            status = HTTPStatus.OK

            if range_header:
                hdr = range_header.strip()
                if file_size == 0:
                    self.send_range_not_satisfiable(file_size)
                    return
                m = re.fullmatch(r"bytes=(\d*)-(\d*)", hdr, re.IGNORECASE)
                if m:
                    s_str, e_str = m.group(1), m.group(2)
                    if not s_str and not e_str:
                        self.send_range_not_satisfiable(file_size)
                        return
                    if not s_str and e_str:
                        suffix = int(e_str)
                        if suffix <= 0:
                            self.send_range_not_satisfiable(file_size)
                            return
                        start = max(0, file_size - suffix)
                        end = file_size - 1
                    else:
                        start = int(s_str)
                        end = int(e_str) if e_str else file_size - 1
                        if start >= file_size or start > end:
                            self.send_range_not_satisfiable(file_size)
                            return
                        end = min(end, file_size - 1)
                    status = HTTPStatus.PARTIAL_CONTENT
                elif hdr.lower().startswith("bytes="):
                    self.send_range_not_satisfiable(file_size)
                    return

            length = max(0, end - start + 1) if file_size > 0 else 0
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            if download_name:
                safe_dl = download_name.replace('"', "_")
                self.send_header("Content-Disposition", f'attachment; filename="{safe_dl}"')
            self.end_headers()

            if head_only or length == 0:
                return

            try:
                with open(file_path, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(262144, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def build_state_payload(self):
            audio_files = []
            for f in find_local_audio_files():
                audio_files.append(
                    {"name": f.name, "path": str(f), "size_bytes": f.stat().st_size}
                )
            source_clips = list_source_videos(SOURCE_DIR)
            source_names = [f.name for f in source_clips]
            with JOB_STATE.lock:
                job_payload = {
                    "running": JOB_STATE.running,
                    "step": JOB_STATE.step,
                    "progress": JOB_STATE.progress,
                    "logs": list(JOB_STATE.logs),
                    "error": JOB_STATE.error,
                }
                manifest = load_or_build_manifest(allow_rebuild=not job_payload["running"])
                verify_exists = VERIFY_FILE.exists() and VERIFY_FILE.stat().st_size > 0
                verify_mtime = VERIFY_FILE.stat().st_mtime if verify_exists else 0
            playlist_url = manifest.get("playlist_url", PLAYLIST_URL)
            song_path_str = manifest.get("song_path", "")
            song_name = manifest.get("song_name") or (Path(song_path_str).name if song_path_str else "")
            fade_ms = int(manifest.get("fade_ms", int(FADE_DURATION * 1000)))
            parts = manifest.get("parts", [])
            return {
                "version": STUDIO_VERSION,
                "tools": {
                    "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                    "ffmpeg": bool(get_ffmpeg_bin()),
                    "ffprobe": bool(get_ffprobe_bin()),
                    "yt_dlp": has_yt_dlp(),
                },
                "playlist_url": playlist_url,
                "active_playlist": playlist_url,
                "active_song_path": song_path_str,
                "active_song": song_path_str,
                "song_name": song_name,
                "is_demo_song": manifest.get("is_demo_song", False),
                "fade_ms": fade_ms,
                "audio_files": audio_files,
                "sources": source_names,
                "source_count": len(source_clips),
                "parts": parts,
                "manifest": manifest,
                "verify_exists": verify_exists,
                "has_verify": verify_exists,
                "verify_mtime": verify_mtime,
                "job": job_payload,
            }

        def serve_web_asset_by_name(self, raw_name: str, head_only: bool = False):
            safe_name = safe_media_basename(raw_name)
            if not safe_name or safe_name not in WEB_ASSET_WHITELIST:
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            asset_path = resolve_web_asset(safe_name)
            if not asset_path:
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            ctype = WEB_MIME_TYPES.get(asset_path.suffix.lower(), "application/octet-stream")
            self.send_bytes(asset_path.read_bytes(), ctype, head_only=head_only)

        def _serve_get_or_head(self, head_only: bool = False):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)
            download = qs.get("download", ["0"])[0] == "1"

            if path in ("/", "/index.html"):
                index_asset = resolve_web_asset("index.html")
                body = index_asset.read_bytes() if index_asset else STUDIO_HTML.encode("utf-8")
                self.send_bytes(body, "text/html; charset=utf-8", head_only=head_only)
                return

            if path.startswith("/web/"):
                self.serve_web_asset_by_name(path[len("/web/"):], head_only=head_only)
                return

            if path == "/api/health":
                self.send_json(
                    {
                        "ok": True,
                        "app": "seamless-shorts-studio",
                        "version": STUDIO_VERSION,
                        "mode": "studio",
                    },
                    head_only=head_only,
                )
                return

            if path == "/api/state":
                self.send_json(self.build_state_payload(), head_only=head_only)
                return

            if path == "/media/verify":
                dl_name = VERIFY_FILE.name if download else None
                self.send_file_range(VERIFY_FILE, "video/mp4", download_name=dl_name, head_only=head_only)
                return

            if path.startswith("/media/part/"):
                raw_name = path[len("/media/part/"):]
                safe_name = safe_media_basename(raw_name)
                if not safe_name:
                    self.send_json(
                        {"error": "Invalid media filename."},
                        status=HTTPStatus.BAD_REQUEST if raw_name else HTTPStatus.NOT_FOUND,
                        head_only=head_only,
                    )
                    return
                target = (OUTPUT_DIR / safe_name).resolve()
                if target.parent != OUTPUT_DIR.resolve():
                    self.send_json({"error": "Invalid media path."}, status=HTTPStatus.BAD_REQUEST, head_only=head_only)
                    return
                dl_name = safe_name if download else None
                self.send_file_range(target, media_mime_type(safe_name), download_name=dl_name, head_only=head_only)
                return

            if path.startswith("/media/source/"):
                raw_name = path[len("/media/source/"):]
                safe_name = safe_media_basename(raw_name)
                if not safe_name:
                    self.send_json(
                        {"error": "Invalid media filename."},
                        status=HTTPStatus.BAD_REQUEST if raw_name else HTTPStatus.NOT_FOUND,
                        head_only=head_only,
                    )
                    return
                target = (SOURCE_DIR / safe_name).resolve()
                if target.parent != SOURCE_DIR.resolve():
                    self.send_json({"error": "Invalid media path."}, status=HTTPStatus.BAD_REQUEST, head_only=head_only)
                    return
                dl_name = safe_name if download else None
                self.send_file_range(target, media_mime_type(safe_name), download_name=dl_name, head_only=head_only)
                return

            if path.startswith("/media/audio/"):
                raw_name = path[len("/media/audio/"):]
                safe_name = safe_media_basename(raw_name)
                if not safe_name:
                    self.send_json(
                        {"error": "Invalid audio filename."},
                        status=HTTPStatus.BAD_REQUEST if raw_name else HTTPStatus.NOT_FOUND,
                        head_only=head_only,
                    )
                    return
                audio_path = resolve_audio_file(safe_name)
                if not audio_path:
                    self.send_error(HTTPStatus.NOT_FOUND, "Audio file not found")
                    return
                dl_name = safe_name if download else None
                self.send_file_range(audio_path, media_mime_type(audio_path.name), download_name=dl_name, head_only=head_only)
                return

            if path == "/download/all.zip":
                tmp_zip = None
                try:
                    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tf:
                        tmp_zip = Path(tf.name)
                    with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_STORED) as zf:
                        if OUTPUT_DIR.exists():
                            for p in sorted(OUTPUT_DIR.glob("*.mp4")):
                                zf.write(p, f"repurposed_shorts/{p.name}")
                        if VERIFY_FILE.exists():
                            zf.write(VERIFY_FILE, VERIFY_FILE.name)
                    self.send_file_range(
                        tmp_zip,
                        "application/zip",
                        download_name="seamless_shorts_output.zip",
                        head_only=head_only,
                    )
                finally:
                    if tmp_zip and tmp_zip.exists():
                        try:
                            tmp_zip.unlink()
                        except Exception:
                            pass
                return

            if path == "/download/toolkit.zip":
                zip_candidates = [
                    SCRIPT_DIR / "upload" / "YT_1m_to_full_song_1x.zip",
                    SCRIPT_DIR / "YT_1m_to_full_song_1x.zip",
                    Path("YT_1m_to_full_song_1x.zip"),
                ]
                zip_path = next((p for p in zip_candidates if p.is_file()), None)
                if zip_path:
                    self.send_file_range(
                        zip_path,
                        "application/zip",
                        download_name="YT_1m_to_full_song_1x.zip",
                        head_only=head_only,
                    )
                else:
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                        zf.write(Path(__file__).resolve(), "seamless_shorts_agent.py")
                        for extra in ("START_HERE.bat", "START_HERE.command", "README.txt"):
                            extra_path = SCRIPT_DIR / extra
                            if extra_path.is_file():
                                zf.write(extra_path, extra)
                        for asset_name in sorted(WEB_ASSET_WHITELIST):
                            asset_path = resolve_web_asset(asset_name)
                            if asset_path:
                                zf.write(asset_path, asset_name)
                    payload = buf.getvalue()
                    self.send_bytes(
                        payload,
                        "application/zip",
                        head_only=head_only,
                        download_name="YT_1m_to_full_song_1x.zip",
                    )
                return

            # Flat top-level whitelisted web asset routes (/<asset>)
            if path.startswith("/") and "/" not in path[1:]:
                self.serve_web_asset_by_name(path[1:], head_only=head_only)
                return

            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_OPTIONS(self):
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_HEAD(self):
            self._serve_get_or_head(head_only=True)

        def do_GET(self):
            self._serve_get_or_head(head_only=False)

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                self.send_json({"error": "Invalid Content-Length header."}, status=400)
                return

            if path == "/api/upload-song":
                if length <= 0 or length > 200 * 1024 * 1024:
                    self.send_json({"error": "Invalid file size (max 200 MB)."}, status=400)
                    return
                raw_header = self.headers.get("X-Filename")
                if not raw_header or not raw_header.strip():
                    self.send_json({"error": "Missing X-Filename header."}, status=400)
                    return
                validated_name = safe_media_basename(raw_header.strip())
                if not validated_name:
                    self.send_json({"error": "Invalid filename."}, status=400)
                    return
                ext = Path(validated_name).suffix.lower()
                if ext not in AUDIO_EXTENSIONS:
                    self.send_json({"error": f"Unsupported audio file extension: {ext or 'none'}"}, status=400)
                    return
                stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(validated_name).stem).strip("._") or "uploaded_song"
                safe_name = f"{stem}{ext}"
                WORK_DIR.mkdir(parents=True, exist_ok=True)
                dest = (WORK_DIR / safe_name).resolve()
                if dest.parent != WORK_DIR.resolve():
                    self.send_json({"error": "Invalid upload destination."}, status=400)
                    return
                data = self.rfile.read(length)
                if len(data) != length:
                    self.send_json({"error": "Incomplete upload body."}, status=400)
                    return
                with open(dest, "wb") as f:
                    f.write(data)
                with JOB_STATE.lock:
                    running = JOB_STATE.running
                with MANIFEST_LOCK:
                    manifest = _load_or_build_manifest_unlocked(allow_rebuild=not running)
                    manifest["song_path"] = str(dest)
                    manifest["song_name"] = dest.name
                    manifest["is_demo_song"] = False
                    _write_manifest_atomic(manifest, MANIFEST_FILE)
                JOB_STATE.add_log(f"[Upload] Saved song: {dest.name} ({len(data)/1048576:.2f} MB)")
                self.send_json({"ok": True, "song_name": dest.name, "song_path": str(dest)})
                return

            if path == "/api/run":
                if length < 0 or length > 1024 * 1024:
                    self.send_json({"error": "Invalid request payload size."}, status=400)
                    return
                body = self.rfile.read(length) if length > 0 else b""
                if body.strip():
                    try:
                        params = json.loads(body.decode("utf-8"))
                    except Exception:
                        self.send_json({"error": "Invalid JSON payload."}, status=400)
                        return
                    if not isinstance(params, dict):
                        self.send_json({"error": "JSON payload must be an object."}, status=400)
                        return
                else:
                    params = {}

                try:
                    max_downloads = parse_int_param(params.get("max_downloads"), 10, min_val=0)
                    fade_ms = parse_int_param(params.get("fade_ms"), int(FADE_DURATION * 1000), min_val=0)
                    skip_download = parse_bool_param(params.get("skip_download"), False)
                    browser_safe_h264 = parse_bool_param(params.get("browser_safe_h264"), False)
                    cookies_browser = normalize_cookies_from_browser(params.get("cookies_from_browser"))
                    raw_playlist = params.get("playlist_url")
                    playlist_url = PLAYLIST_URL if raw_playlist is None else validate_playlist_url(raw_playlist)
                except ValueError as exc:
                    self.send_json({"error": str(exc)}, status=400)
                    return

                song_path = str(params.get("song_path") or MASTER_SONG_PATH).strip() or MASTER_SONG_PATH

                with JOB_STATE.lock:
                    if JOB_STATE.running:
                        self.send_json({"error": "A sync job is already running."}, status=409)
                        return
                    JOB_STATE.running = True
                    JOB_STATE.error = None
                    JOB_STATE.logs = []
                    JOB_STATE.step = "Starting..."
                    JOB_STATE.progress = 2

                def worker():
                    try:
                        manifest = run_pipeline(
                            playlist_url=playlist_url,
                            song_path=song_path,
                            max_downloads=max_downloads,
                            fade_ms=fade_ms,
                            skip_download=skip_download,
                            browser_safe_h264=browser_safe_h264,
                            interactive=False,
                            progress_cb=JOB_STATE.set_progress,
                            log_cb=JOB_STATE.add_log,
                            cookies_from_browser=cookies_browser,
                        )
                        with JOB_STATE.lock:
                            JOB_STATE.manifest = manifest
                    except Exception as exc:
                        JOB_STATE.add_log(f"[ERROR] {exc}")
                        with JOB_STATE.lock:
                            JOB_STATE.error = str(exc)
                            JOB_STATE.step = "Failed"
                    finally:
                        with JOB_STATE.lock:
                            JOB_STATE.running = False

                threading.Thread(target=worker, daemon=True).start()
                self.send_json({"ok": True})
                return

            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    return StudioHandler


def find_free_port(preferred_port=DEFAULT_PORT, host="127.0.0.1"):
    bind_host = host or "127.0.0.1"
    if isinstance(preferred_port, (bool, float)):
        raise ValueError("Port must be an integer between 0 and 65535.")
    pref = int(preferred_port)
    if pref < 0 or pref > 65535:
        raise ValueError(f"Port must be between 0 and 65535, got {pref}.")
    if pref == 0:
        return 0
    for port in range(pref, min(65536, pref + 20)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((bind_host, port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((bind_host, 0))
        return s.getsockname()[1]


class StudioHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_ui_server(host="127.0.0.1", port=DEFAULT_PORT, open_browser=True):
    actual_port = find_free_port(port, host=host)
    server = StudioHTTPServer((host, actual_port), make_handler())
    bound_host, bound_port = server.server_address[:2]
    display_host = "127.0.0.1" if bound_host in ("0.0.0.0", "", "::") else bound_host
    url = f"http://{display_host}:{bound_port}"
    print("\n====================================================", flush=True)
    print(" Seamless Shorts Studio & Video Player is running!", flush=True)
    print(f" Open in browser: {url}", flush=True)
    print(" Press Ctrl+C in this window to stop when finished.", flush=True)
    print("====================================================\n", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Studio...")
        server.server_close()


def main():
    default_host = os.environ.get("HOST", "127.0.0.1").strip() or "127.0.0.1"
    env_port = os.environ.get("PORT", "").strip()
    try:
        default_port = int(env_port) if env_port else DEFAULT_PORT
    except ValueError:
        default_port = DEFAULT_PORT

    parser = argparse.ArgumentParser(description="Seamless YouTube Shorts Music Sync & Studio")
    parser.add_argument("--ui", "--server", action="store_true", help="Launch the visual Web Studio & Video Player")
    parser.add_argument("--cli", action="store_true", help="Run directly in command-line mode")
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open browser when starting --ui")
    parser.add_argument("--host", default=default_host, help="Bind host for --ui server")
    parser.add_argument("--port", type=int, default=default_port, help="Port for --ui server")
    parser.add_argument("--playlist", default=PLAYLIST_URL, help="YouTube playlist URL")
    parser.add_argument("--song", default=MASTER_SONG_PATH, help="Path to full song (.mp3/.wav/.m4a)")
    parser.add_argument("--max-shorts", type=int, default=10, help="Max Shorts to download (0 = all)")
    parser.add_argument("--fade-ms", type=int, default=int(FADE_DURATION * 1000), help="Audio fade in/out ms")
    parser.add_argument("--skip-download", action="store_true", help="Reuse existing downloaded Shorts")
    parser.add_argument("--browser-safe", action="store_true", help="Re-encode individual Parts to H.264")
    parser.add_argument(
        "--cookies-from-browser",
        default=None,
        help="Optional browser name for yt-dlp --cookies-from-browser (e.g. chrome, edge, firefox)",
    )
    parser.add_argument("--make-demo-song", action="store_true", help="Generate sample_song.mp3 demo beat track")
    args = parser.parse_args()

    if args.make_demo_song:
        out = generate_demo_beat_track(args.song if args.song != MASTER_SONG_PATH else "sample_song.mp3")
        print(f"Generated demo beat track: {out}")
        return

    if args.ui:
        start_ui_server(host=args.host, port=args.port, open_browser=not args.no_browser)
        return

    # CLI mode
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    run_pipeline(
        playlist_url=args.playlist,
        song_path=args.song,
        max_downloads=args.max_shorts,
        fade_ms=args.fade_ms,
        skip_download=args.skip_download,
        browser_safe_h264=args.browser_safe,
        interactive=sys.stdin.isatty() and not args.cli,
        log_cb=print,
        cookies_from_browser=args.cookies_from_browser,
    )


if __name__ == "__main__":
    main()
