#!/usr/bin/env bash
# Double-click (or run) this once to save your WDGoWars API key.
cd "$(dirname "$0")"
python3 heimdall.py --setup
echo
read -n 1 -s -r -p "Press any key to close..."
echo
