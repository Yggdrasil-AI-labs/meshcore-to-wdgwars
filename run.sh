#!/usr/bin/env bash
# Double-click (or run) to upload a MeshMapper CSV to WDGoWars.
# Usage: ./run.sh path/to/your_export.csv
cd "$(dirname "$0")"
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
else
    PY="python3"
fi
if [ -z "$1" ]; then
  echo "Usage: $(basename "$0") path/to/your_export.csv"
  "$PY" heimdall.py --help
else
  "$PY" heimdall.py "$@"
fi
echo
if [ -t 0 ]; then
    read -n 1 -s -r -p "Press any key to close..."
    echo
fi
