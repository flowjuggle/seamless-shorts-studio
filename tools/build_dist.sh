#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_ZIP="$REPO_ROOT/upload/YT_1m_to_full_song_1x.zip"
SYMLINK_PATH="$REPO_ROOT/YT_1m_to_full_song_1x.zip"

cd "$REPO_ROOT"
python3 tools/build_standalone.py

mkdir -p "$REPO_ROOT/upload"
rm -f "$OUT_ZIP"

zip -j "$OUT_ZIP" \
  seamless_shorts_agent.py \
  START_HERE.bat \
  START_HERE.command \
  README.txt \
  web/index.html \
  web/styles.css \
  web/app.js \
  web/jszip.min.js \
  web/Seamless_Shorts_Player.html

ln -sfn upload/YT_1m_to_full_song_1x.zip "$SYMLINK_PATH"
unzip -t "$OUT_ZIP"
echo "Built distribution package: $OUT_ZIP"
