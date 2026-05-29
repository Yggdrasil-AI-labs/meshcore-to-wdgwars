#!/usr/bin/env python3
"""
heimdall — MeshMapper CSV → WDGoWars meshcore_nodes uplink.

Sibling of Muninn (adsb-to-wdgwars). Both sit on the shared `gungnir`
transport library; this file is the MeshMapper-specific parser and CLI.

Target schema (per the WDGoWars mesh ingest, shared by FusedStamen):

    timestamp, node_id, type, name, lat, lon, rssi, snr

"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import gungnir

__version__ = "0.2.0"
GITHUB_REPO = "HiroAlleyCat/meshcore-to-wdgwars"
GITHUB_URL = f"https://github.com/{GITHUB_REPO}"

# CLI tool — configure logging so the cron-style "line per event" output
# users saw in 0.1.x still appears. Library consumers that wire their own
# root logger override this.
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

# Single Client for the process lifetime. Bundles per-tool identity so
# we don't thread it through every call.
_client = gungnir.Client(
    tool="heimdall",
    version=__version__,
    user_agent_extra=GITHUB_URL,
)

DEFAULT_ENDPOINT = gungnir.DEFAULT_API_URL  # backward-compat alias
TARGET_FIELDS = ("timestamp", "node_id", "type", "name", "lat", "lon", "rssi", "snr")

MESHMAPPER_RX_HEADERS = (
    "timestamp", "repeater_id", "snr", "rssi", "path_length",
    "header", "latitude", "longitude", "path_hops",
)

DEFAULT_NODE_TYPE = "repeater"


def _normalise_meshmapper_row(row: dict[str, str]) -> dict[str, Any] | None:
    """
    Map one MeshMapper RX-log CSV row to the WDGoWars meshcore schema.

    MeshMapper "Copy CSV" header (sample courtesy of Wild!Radio):
        timestamp,repeater_id,snr,rssi,path_length,header,latitude,longitude,path_hops

    Target schema (per the WDGoWars mesh ingest, shared by FusedStamen):
        timestamp,node_id,type,name,lat,lon,rssi,snr

    Returns None for rows missing required fields or with unparseable numerics.
    Paste-damaged or malformed rows skip silently this way.
    """
    if not row.get("timestamp") or not row.get("repeater_id"):
        return None
    try:
        lat = float(row["latitude"])
        lon = float(row["longitude"])
        rssi = float(row["rssi"])
        snr = float(row["snr"])
    except (TypeError, ValueError, KeyError):
        return None

    return {
        "timestamp": row["timestamp"],
        "node_id": row["repeater_id"],
        "type": DEFAULT_NODE_TYPE,
        "name": "",
        "lat": lat,
        "lon": lon,
        "rssi": rssi,
        "snr": snr,
    }


def parse_meshmapper_csv(path: Path) -> list[dict[str, Any]]:
    """Read a MeshMapper RX-log CSV and return the normalised node records."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        records = []
        for row in reader:
            norm = _normalise_meshmapper_row(row)
            if norm is not None:
                records.append(norm)
        return records


def build_envelope(nodes: list[dict[str, Any]], api_key: str) -> dict[str, str]:
    """Build the HMAC envelope for a meshcore_nodes payload.

    Kept as a thin shim over :func:`gungnir.envelope.build_envelope` so
    test_heimdall.py and any external scripts that imported this name
    continue to work. New code should call gungnir directly.
    """
    payload = gungnir.build_payload(meshcore_nodes=nodes)
    return gungnir.build_envelope(payload, api_key)


def upload(
    nodes: list[dict[str, Any]],
    api_key: str,
    endpoint: str = DEFAULT_ENDPOINT,
    dry_run: bool = False,
    batch_size: int = 500,
) -> int:
    """POST ``nodes`` to wdgwars.pl's signed-JSON endpoint.

    Behavior (inherited from gungnir):

    - Retries 5xx and network errors with exponential backoff.
    - 429 stops the whole batch and persists a cooldown the next cron
      tick respects.
    - Silent-drop pattern (HTTP 200 ok:true with every counter zero)
      exits non-zero.

    Returns the shell exit code (0 ok, 1 fail). Signature differs from
    0.1.x — the previous version returned a list of (status, body)
    tuples; the new version follows the muninn/gungnir convention.
    """
    return gungnir.transport.send(
        "heimdall", __version__, endpoint, api_key,
        meshcore_nodes=nodes,
        batch_size=batch_size,
        dry_run=dry_run,
        user_agent_extra=GITHUB_URL,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="MeshMapper CSV → WDGoWars meshcore uplink",
    )
    p.add_argument("csv", type=Path, nargs="?",
                   help="MeshMapper CSV export (omit with --whoami)")
    p.add_argument("--api-key", help="WDGoWars API key (or set WDGWARS_API_KEY)")
    p.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    p.add_argument("--batch-size", type=int, default=500,
                   help="records per signed POST (default: 500)")
    p.add_argument("--dry-run", action="store_true", help="Build envelope but do not POST")
    p.add_argument("--preview", action="store_true",
                   help="Print first 6 normalised rows and exit")
    p.add_argument("--whoami", action="store_true",
                   help="Validate the API key against /api/me and exit")
    args = p.parse_args(argv)

    # whoami uses the api key but no CSV
    if args.whoami:
        key = args.api_key or _client.load_key() or os.environ.get("WDGWARS_API_KEY")
        if not key:
            print("missing API key: pass --api-key or set WDGWARS_API_KEY", file=sys.stderr)
            return 2
        return _client.whoami(key)

    if not args.csv.exists():
        print(f"file not found: {args.csv}", file=sys.stderr)
        return 2

    nodes = parse_meshmapper_csv(args.csv)
    print(f"parsed {len(nodes)} meshcore nodes from {args.csv.name}")
    if not nodes:
        print("nothing to upload", file=sys.stderr)
        return 1

    if args.preview:
        for row in nodes[:6]:
            print(json.dumps(row))
        return 0

    key = args.api_key or _client.load_key() or os.environ.get("WDGWARS_API_KEY")
    if not key:
        print("missing API key: pass --api-key or set WDGWARS_API_KEY", file=sys.stderr)
        return 2

    return upload(nodes, key, endpoint=args.endpoint,
                  dry_run=args.dry_run, batch_size=args.batch_size)


if __name__ == "__main__":
    raise SystemExit(main())
