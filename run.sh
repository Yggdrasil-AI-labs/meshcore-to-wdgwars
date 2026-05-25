#!/usr/bin/env bash
# Double-click (or run) to upload a MeshMapper CSV to WDGoWars.
# Usage: ./run.sh path/to/your_export.csv
cd "$(dirname "$0")"
if [ -z "$1" ]; then
  echo "Usage: $(basename "$0") path/to/your_export.csv"
  python3 heimdall.py --help
else
  python3 heimdall.py "$@"
fi
echo
read -n 1 -s -r -p "Press any key to close..."
echo
