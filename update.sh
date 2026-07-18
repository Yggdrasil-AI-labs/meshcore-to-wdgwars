#!/usr/bin/env bash
# Double-click (or run) this file to update Heimdall (refreshes deps + script).
# Refresh order matters: requirements.txt may have grown a new dep since
# the local copy was downloaded. Pull it first, install deps, THEN invoke
# heimdall so it can import all of them cleanly.
#
# Uses the project-local .venv/ created by setup.sh. If the venv is missing
# (older install before the PEP 668 fix), we recreate it.

set -e
cd "$(dirname "$0")"

if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
    echo "Heimdall requires Python 3.10 or newer. Your current python3 is:"
    python3 --version 2>/dev/null || echo "  (not found on PATH)"
    echo
    echo "Install Python 3.10+ from your package manager or https://python.org/downloads/ and re-run."
    exit 1
fi

VENV_DIR=".venv"
if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "No $VENV_DIR/ found — creating it (running setup.sh once is the usual way)..."
    if ! python3 -m venv "$VENV_DIR" 2>/dev/null; then
        echo
        echo "Failed to create venv. On Debian/Ubuntu/Raspberry Pi OS install:"
        echo "  sudo apt install -y python3-venv python3-full"
        echo "Then re-run ./update.sh."
        exit 1
    fi
fi
VENV_PY="$VENV_DIR/bin/python"

echo "[1/3] Refreshing requirements.txt from GitHub..."
"$VENV_PY" -c "import urllib.request as u; u.urlretrieve('https://raw.githubusercontent.com/Yggdrasil-AI-labs/meshcore-to-wdgwars/main/requirements.txt', 'requirements.txt')"

echo
echo "[2/3] Installing/refreshing dependencies..."
"$VENV_PY" -m pip install --upgrade -r requirements.txt

echo
echo "[3/3] Updating heimdall.py..."
"$VENV_PY" heimdall.py --update

echo
if [ -t 0 ]; then
    read -n 1 -s -r -p "Press any key to close..."
    echo
fi
