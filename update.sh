#!/usr/bin/env bash
# Double-click (or run) this file to update Heimdall (refreshes deps + script).
# Refresh order matters: requirements.txt may have grown a new dep since
# the local copy was downloaded. Pull it first, install deps, THEN invoke
# heimdall so it can import all of them cleanly.

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
echo "[2/3] Installing/refreshing dependencies..."
python3 -m pip install --upgrade -r requirements.txt

echo
echo "[3/3] Updating heimdall.py..."
python3 heimdall.py --update

echo
read -n 1 -s -r -p "Press any key to close..."
echo
