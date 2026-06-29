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
import datetime
import hashlib
import hmac
import json
import re
import secrets
import sys
import urllib.request
from pathlib import Path
from typing import Any

__version__ = "0.4.0"

DEFAULT_ENDPOINT = "https://wdgwars.pl/api/upload/"
BATCH_SIZE = 1000
TARGET_FIELDS = ("timestamp", "node_id", "type", "name", "lat", "lon", "rssi", "snr")


MESHMAPPER_RX_HEADERS = (
    "timestamp", "repeater_id", "snr", "rssi", "path_length",
    "header", "latitude", "longitude", "path_hops",
)

DEFAULT_NODE_TYPE = "repeater"

# Multi-section MeshMapper export support — see the root heimdall.py for the
# full format notes. This copy is the browser (Pyodide) parser; the parsing
# core must stay byte-for-byte in step with the root module.
_SECTION_RE = re.compile(r"^---\s*(.+?)\s+Log\s*---\s*$", re.IGNORECASE)
_PACKED_NODE_COLUMNS = ("nodes", "events", "heard_repeats")
_NODE_TOKEN_RE = re.compile(
    r"^([0-9A-Fa-f]{2,})"
    r"(?:\(([A-Za-z])\))?"
    r"\(([-+]?\d+(?:\.\d+)?)\)$"
)
_NODE_TYPE_MARKERS = {"R": "repeater"}


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _node_token_to_record(token: str, timestamp: str,
                          lat: float, lon: float) -> dict[str, Any] | None:
    """Parse one ID(snr) / ID(R)(snr) token into a meshcore record.
    rssi is None: packed tokens carry SNR but no per-node RSSI."""
    token = token.strip()
    if not token:
        return None
    m = _NODE_TOKEN_RE.match(token)
    if not m:
        return None
    node_id, marker, snr_s = m.group(1), m.group(2), m.group(3)
    node_type = (_NODE_TYPE_MARKERS.get(marker.upper(), DEFAULT_NODE_TYPE)
                 if marker else DEFAULT_NODE_TYPE)
    return {
        "timestamp": timestamp,
        "node_id": node_id,
        "type": node_type,
        "name": "",
        "lat": lat,
        "lon": lon,
        "rssi": None,
        "snr": float(snr_s),
    }


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


def _split_sections(lines: list[str]) -> list[tuple[str | None, list[str]]]:
    """Split into (section_name, content_lines) blocks. A flat file with no
    markers yields one (None, all_non_blank_lines) block."""
    sections: list[tuple[str | None, list[str]]] = []
    name: str | None = None
    block: list[str] = []
    for line in lines:
        m = _SECTION_RE.match(line.strip())
        if m:
            if block:
                sections.append((name, block))
            name = m.group(1).strip().upper()
            block = []
            continue
        if line.strip():
            block.append(line)
    if block:
        sections.append((name, block))
    return sections


def _parse_packed_section(cols: list[str], packed_idx: int,
                          data_lines: list[str]) -> list[dict[str, Any]]:
    lower = [c.lower() for c in cols]
    ts_i = lower.index("timestamp") if "timestamp" in lower else 0
    lat_i = lower.index("latitude") if "latitude" in lower else None
    lon_i = lower.index("longitude") if "longitude" in lower else None
    out: list[dict[str, Any]] = []
    for raw in csv.reader(data_lines):
        if not raw or ts_i >= len(raw) or not raw[ts_i].strip():
            continue
        ts = raw[ts_i].strip()
        lat = _safe_float(raw[lat_i]) if lat_i is not None and lat_i < len(raw) else 0.0
        lon = _safe_float(raw[lon_i]) if lon_i is not None and lon_i < len(raw) else 0.0
        for tok in raw[packed_idx:]:
            rec = _node_token_to_record(tok, ts, lat, lon)
            if rec is not None:
                out.append(rec)
    return out


def _parse_section(block: list[str]) -> list[dict[str, Any]]:
    if not block:
        return []
    header = next(csv.reader([block[0]]))
    cols = [c.strip() for c in header]
    lower = [c.lower() for c in cols]
    for key in _PACKED_NODE_COLUMNS:
        if key in lower:
            return _parse_packed_section(cols, lower.index(key), block[1:])
    out: list[dict[str, Any]] = []
    for row in csv.DictReader(block):
        norm = _normalise_meshmapper_row(row)
        if norm is not None:
            out.append(norm)
    return out


def parse_meshmapper_text(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for _name, block in _split_sections(text.splitlines()):
        records.extend(_parse_section(block))
    return records


def parse_meshmapper_csv(path: Path) -> list[dict[str, Any]]:
    return parse_meshmapper_text(path.read_text(encoding="utf-8"))


def _epoch_to_iso(ts: Any) -> str:
    if ts is None:
        return ""
    if isinstance(ts, str):
        return ts
    try:
        return datetime.datetime.fromtimestamp(
            int(ts), datetime.timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return str(ts)


def _ping_to_records(ping: dict[str, Any]) -> list[dict[str, Any]]:
    ptype = str(ping.get("type") or "").upper()
    ts = _epoch_to_iso(ping.get("timestamp"))
    lat = _safe_float(ping.get("lat"))
    lon = _safe_float(ping.get("lon"))
    if ptype == "DISC":
        node_id = ping.get("repeater_id")
        if not node_id:
            return []
        node_type = str(ping.get("node_type") or DEFAULT_NODE_TYPE).lower()
        snr = ping.get("local_snr")
        rssi = ping.get("local_rssi")
        return [{
            "timestamp": ts,
            "node_id": node_id,
            "type": node_type,
            "name": "",
            "lat": lat,
            "lon": lon,
            "rssi": _safe_float(rssi) if rssi is not None else None,
            "snr": _safe_float(snr) if snr is not None else None,
        }]
    if ptype == "RX":
        out: list[dict[str, Any]] = []
        for tok in str(ping.get("heard_repeats") or "").split(","):
            rec = _node_token_to_record(tok, ts, lat, lon)
            if rec is not None:
                out.append(rec)
        return out
    return []


def parse_offline_json_obj(data: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for ping in data.get("pings", []):
        if isinstance(ping, dict):
            records.extend(_ping_to_records(ping))
    return records


def parse_offline_json(path: Path) -> list[dict[str, Any]]:
    return parse_offline_json_obj(json.loads(path.read_text(encoding="utf-8")))


def parse_file(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Detect the capture format and parse it. Returns (records, format_id)."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        return parse_offline_json(path), "meshcore-offline-json"
    if suffix in (".csv", ".txt"):
        return parse_meshmapper_csv(path), "meshmapper-csv"
    head = path.read_text(encoding="utf-8", errors="replace").lstrip()[:1]
    if head == "{":
        return parse_offline_json(path), "meshcore-offline-json"
    return parse_meshmapper_csv(path), "meshmapper-csv"


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
                "User-Agent": f"heimdall/{__version__}",
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

    nodes, fmt = parse_file(args.csv)
    print(f"parsed {len(nodes)} meshcore nodes from {args.csv.name} ({fmt})")
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
