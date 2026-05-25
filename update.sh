#!/usr/bin/env bash
# Double-click (or run) this file to update Heimdall to the latest version.
cd "$(dirname "$0")"
python3 heimdall.py --update
echo
read -n 1 -s -r -p "Press any key to close..."
echo
