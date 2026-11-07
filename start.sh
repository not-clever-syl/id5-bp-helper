#!/usr/bin/env bash
# IVL 2026 Fall - Survivor BP Helper / launcher for Linux and macOS
#
# Usage:
#   ./start.sh                    start on port 8766, open the browser
#   ./start.sh 8080               use another port
#   ./start.sh 8766 --no-browser  do not open the browser
#
# On most systems the interpreter is called python3, not python.
# If this file is not executable yet, run:
#   chmod +x start.sh

set -u

cd "$(dirname "$0")" || exit 1

PORT=8766
NO_BROWSER=""
for arg in "$@"; do
    case "$arg" in
        --no-browser) NO_BROWSER="--no-browser" ;;
        [0-9]*) PORT="$arg" ;;
    esac
done

echo
echo "  ============================================"
echo "    IVL 2026 Fall - Survivor BP Helper"
echo "  ============================================"
echo

# Pick an interpreter: python3 first (Linux/macOS), python as fallback.
PY=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c 'import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)' 2>/dev/null; then
            PY="$cand"
            break
        fi
    fi
done

if [ -z "$PY" ]; then
    echo "  [X] Python 3.8 or newer was not found"
    echo
    echo "  How to install:"
    echo "    macOS         : brew install python3"
    echo "                    or download from https://www.python.org/downloads/"
    echo "    Debian/Ubuntu : sudo apt install python3"
    echo "    Fedora/RHEL   : sudo dnf install python3"
    echo
    exit 1
fi

echo "  Using Python: $($PY --version 2>&1)"
echo

echo "  Starting the server, the browser will open automatically..."
echo "  Press Ctrl+C to stop"
echo

if [ -n "$NO_BROWSER" ]; then
    exec "$PY" scripts/serve.py "$PORT" --no-browser
else
    exec "$PY" scripts/serve.py "$PORT"
fi
