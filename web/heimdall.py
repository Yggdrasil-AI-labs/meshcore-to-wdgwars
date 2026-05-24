#!/usr/bin/env python3
"""
heimdall — MeshMapper CSV → WDGoWars meshcore_nodes uplink.

Sibling of Muninn (adsb-to-wdgwars). Same HMAC envelope, same /api/upload/
endpoint, different payload slot. Muninn fills `aircraft`; Heimdall fills
`meshcore_nodes`.

Target schema (per the WDGoWars mesh ingest, shared by FusedStamen):

    timestamp, node_id, type, name, lat, lon, rssi, snr

"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import hmac
import json
import secrets
import sys
import urllib.request
from pathlib import Path
from typing import Any

__version__ = "0.1.0"

DEFAULT_ENDPOINT = "https://wdgwars.pl/api/upload/"
BATCH_SIZE = 1000
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
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        records = []
        for row in reader:
            norm = _normalise_meshmapper_row(row)
            if norm is not None:
                records.append(norm)
        return records


def build_envelope(nodes: list[dict[str, Any]], api_key: str) -> dict[str, str]:
    payload = {"networks": [], "aircraft": [], "meshcore_nodes": nodes}
    body_json = json.dumps(payload, separators=(",", ":"))
    data_b64 = base64.b64encode(body_json.encode()).decode()
    nonce = secrets.token_hex(8)
    sig = hmac.new(
        api_key.encode(),
        (nonce + data_b64).encode(),
        hashlib.sha256,
    ).hexdigest()
    return {"data": data_b64, "nonce": nonce, "sig": sig}


def chunked(seq: list[Any], size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def upload(
    nodes: list[dict[str, Any]],
    api_key: str,
    endpoint: str = DEFAULT_ENDPOINT,
    dry_run: bool = False,
) -> list[tuple[int, str]]:
    results = []
    for chunk in chunked(nodes, BATCH_SIZE):
        envelope = build_envelope(chunk, api_key)
        body = json.dumps(envelope).encode()
        if dry_run:
            results.append((0, f"dry-run: {len(chunk)} nodes, sig={envelope['sig'][:12]}..."))
            continue
        req = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-API-Key": api_key,
                "Accept": "application/json",
                "User-Agent": "heimdall/0.1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                results.append((resp.status, resp.read().decode(errors="replace")))
        except urllib.error.HTTPError as e:
            results.append((e.code, e.read().decode(errors="replace")))
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="MeshMapper CSV → WDGoWars meshcore uplink")
    p.add_argument("csv", type=Path, help="MeshMapper CSV export")
    p.add_argument("--api-key", help="WDGoWars API key (or set WDGWARS_API_KEY)")
    p.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    p.add_argument("--dry-run", action="store_true", help="Build envelope but do not POST")
    p.add_argument("--preview", action="store_true", help="Print first 6 normalised rows and exit")
    args = p.parse_args(argv)

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

    import os

    key = args.api_key or os.environ.get("WDGWARS_API_KEY")
    if not key:
        print("missing API key: pass --api-key or set WDGWARS_API_KEY", file=sys.stderr)
        return 2

    for status, body in upload(nodes, key, endpoint=args.endpoint, dry_run=args.dry_run):
        print(f"[{status}] {body[:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
