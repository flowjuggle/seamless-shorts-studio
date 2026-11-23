#!/usr/bin/env python3
"""Build self-contained offline single-file web/Seamless_Shorts_Player.html (F15)."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "web"

CSS_LINK_RE = re.compile(
    r'<link\b[^>]*\bhref\s*=\s*["\']styles\.css["\'][^>]*>',
    re.IGNORECASE,
)
JSZIP_SCRIPT_RE = re.compile(
    r'<script\b[^>]*\bsrc\s*=\s*["\']jszip\.min\.js["\'][^>]*>\s*</script>',
    re.IGNORECASE,
)
APP_SCRIPT_RE = re.compile(
    r'<script\b[^>]*\bsrc\s*=\s*["\']app\.js["\'][^>]*>\s*</script>',
    re.IGNORECASE,
)


def escape_script_body(js_text: str) -> str:
    """Prevent any literal </script inside JS payloads from closing the HTML <script> element."""
    return re.sub(
        r"</script",
        lambda m: "<\\/" + m.group(0)[2:],
        js_text,
        flags=re.IGNORECASE,
    )


def escape_style_body(css_text: str) -> str:
    """Prevent any literal </style inside CSS payloads from closing the HTML <style> element."""
    return re.sub(
        r"</style",
        lambda m: "<\\/" + m.group(0)[2:],
        css_text,
        flags=re.IGNORECASE,
    )


def build_standalone_html(web_dir: Path = WEB_DIR) -> str:
    index_path = web_dir / "index.html"
    css_path = web_dir / "styles.css"
    jszip_path = web_dir / "jszip.min.js"
    app_path = web_dir / "app.js"

    for p in (index_path, css_path, jszip_path, app_path):
        if not p.is_file():
            raise FileNotFoundError(f"Missing required web asset: {p}")

    html = index_path.read_text(encoding="utf-8")
    css = escape_style_body(css_path.read_text(encoding="utf-8"))
    jszip_js = escape_script_body(jszip_path.read_text(encoding="utf-8"))
    app_js = escape_script_body(app_path.read_text(encoding="utf-8"))

    html, css_n = CSS_LINK_RE.subn(
        lambda _: f"<style>\n{css}\n</style>",
        html,
        count=1,
    )
    if css_n != 1:
        raise ValueError("Could not find <link rel='stylesheet' href='styles.css'> in web/index.html")

    html, jszip_n = JSZIP_SCRIPT_RE.subn(
        lambda _: f"<script>\n{jszip_js}\n</script>",
        html,
        count=1,
    )
    if jszip_n != 1:
        raise ValueError("Could not find <script src='jszip.min.js'></script> in web/index.html")

    html, app_n = APP_SCRIPT_RE.subn(
        lambda _: f"<script>\n{app_js}\n</script>",
        html,
        count=1,
    )
    if app_n != 1:
        raise ValueError("Could not find <script src='app.js'></script> in web/index.html")

    # Sanity check: zero external <script src="http..."> or <link href="http...">
    if re.search(r'<(?:script|link)\b[^>]*(?:src|href)\s*=\s*["\']https?://', html, re.IGNORECASE):
        raise ValueError("Standalone HTML must not contain external http(s) script or stylesheet URLs.")

    return html


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build standalone single-file Seamless_Shorts_Player.html")
    parser.add_argument(
        "--web-dir",
        type=Path,
        default=WEB_DIR,
        help="Directory containing index.html, styles.css, jszip.min.js, and app.js",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output HTML path (defaults to <web-dir>/Seamless_Shorts_Player.html)",
    )
    args = parser.parse_args(argv)

    out_path = args.output or (args.web_dir / "Seamless_Shorts_Player.html")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = build_standalone_html(args.web_dir)
    out_path.write_text(rendered, encoding="utf-8")
    print(f"Built standalone player: {out_path} ({len(rendered.encode('utf-8')):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
