#!/bin/bash
cd "$(dirname "$0")" || exit 1

for d in "$(pwd)/bin" "$HOME/.local/bin" "/opt/homebrew/bin" "/usr/local/bin"; do
  if [ -d "$d" ]; then
    case ":$PATH:" in
      *":$d:"*) ;;
      *) PATH="$d:$PATH" ;;
    esac
  fi
done
export PATH

echo "Launching Seamless Shorts Studio in your browser..."
PY_BIN=""
if command -v python3 >/dev/null 2>&1; then
  PY_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PY_BIN="python"
fi

if [ -z "$PY_BIN" ]; then
  echo "Python 3 not found. Please install Python 3 first (or open Seamless_Shorts_Player.html directly in your browser)."
  read -r -p "Press Enter to exit..."
  exit 1
fi

"$PY_BIN" seamless_shorts_agent.py --ui "$@"
rc=$?
if [ "$rc" -ne 0 ]; then
  echo ""
  echo "[!] Seamless Shorts Studio exited with status $rc. See details above."
  read -r -p "Press Enter to exit..."
  exit "$rc"
fi
