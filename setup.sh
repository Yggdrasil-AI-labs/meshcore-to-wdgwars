#!/usr/bin/env bash
# Double-click (or run) this once to install dependencies and save your WDGoWars API key.
# Heimdall has no third-party deps today, but we refresh requirements.txt
# from GitHub anyway so the bootstrap stays consistent if a dep is added
# in a future release.

set -e
cd "$(dirname "$0")"

if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
    echo "Heimdall requires Python 3.10 or newer. Your current python3 is:"
    python3 --version 2>/dev/null || echo "  (not found on PATH)"
    echo
    echo "Install Python 3.10+ from your package manager or https://python.org/downloads/ and re-run."
    exit 1
fi

echo "[1/3] Refreshing requirements.txt from GitHub..."
python3 -c "import urllib.request as u; u.urlretrieve('https://raw.githubusercontent.com/HiroAlleyCat/meshcore-to-wdgwars/main/requirements.txt', 'requirements.txt')"

echo
echo "[2/3] Installing dependencies..."
python3 -m pip install --upgrade -r requirements.txt

echo
echo "[3/3] Saving your WDGoWars API key..."
python3 heimdall.py --setup

echo
read -n 1 -s -r -p "Press any key to close..."
echo
