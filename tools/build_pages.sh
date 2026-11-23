#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_DIR="${1:-${SITE_DIR:-$REPO_ROOT/dist/site}}"

if [ -z "$TARGET_DIR" ] || [ "$TARGET_DIR" = "/" ]; then
  echo "Refusing unsafe target directory: '$TARGET_DIR'" >&2
  exit 1
fi

cd "$REPO_ROOT"
python3 tools/build_standalone.py

rm -rf "$TARGET_DIR"
mkdir -p "$TARGET_DIR"

cp -f \
  "$REPO_ROOT/web/index.html" \
  "$REPO_ROOT/web/styles.css" \
  "$REPO_ROOT/web/app.js" \
  "$REPO_ROOT/web/jszip.min.js" \
  "$REPO_ROOT/web/Seamless_Shorts_Player.html" \
  "$TARGET_DIR/"

: > "$TARGET_DIR/.nojekyll"
echo "Built static Pages site in: $TARGET_DIR"
