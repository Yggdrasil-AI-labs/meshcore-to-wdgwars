#!/usr/bin/env bash
# Double-click (or run) this once to install dependencies and save your WDGoWars API key.
# Heimdall has no third-party deps today, but we refresh requirements.txt
# from GitHub anyway so the bootstrap stays consistent if a dep is added
# in a future release.
#
# Installs into a project-local .venv/ so this works on PEP 668 distros
# (Raspberry Pi OS Bookworm, Debian 12+, Ubuntu 23.04+, Homebrew Python)
# without --break-system-packages or polluting the system Python.

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
    echo "[1/4] Creating virtual environment in $VENV_DIR/..."
    if ! python3 -m venv "$VENV_DIR" 2>/dev/null; then
        echo
        echo "Failed to create venv. On Debian/Ubuntu/Raspberry Pi OS install the venv module:"
        echo "  sudo apt install -y python3-venv python3-full"
        echo "Then re-run ./setup.sh."
        exit 1
    fi
else
    echo "[1/4] Reusing existing $VENV_DIR/."
fi
VENV_PY="$VENV_DIR/bin/python"

echo
echo "[2/4] Refreshing requirements.txt from GitHub..."
"$VENV_PY" -c "import urllib.request as u; u.urlretrieve('https://raw.githubusercontent.com/HiroAlleyCat/meshcore-to-wdgwars/main/requirements.txt', 'requirements.txt')"

echo
echo "[3/4] Installing dependencies..."
"$VENV_PY" -m pip install --upgrade pip >/dev/null
"$VENV_PY" -m pip install --upgrade -r requirements.txt

echo
echo "[4/4] Saving your WDGoWars API key..."
"$VENV_PY" heimdall.py --setup

echo
read -n 1 -s -r -p "Press any key to close..."
echo
