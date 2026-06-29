#!/usr/bin/env python3
"""
heimdall.py — MeshMapper CSV to WDGoWars meshcore_nodes uplink.

Sibling of Muninn (adsb-to-wdgwars). Same HMAC envelope, same /api/upload/
endpoint, different payload slot. Muninn fills `aircraft`; Heimdall fills
`meshcore_nodes`.

Target schema (per the WDGoWars mesh ingest, shared by FusedStamen):

    timestamp, node_id, type, name, lat, lon, rssi, snr

License: MIT
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


__version__ = "0.4.0"
GITHUB_REPO = "HiroAlleyCat/meshcore-to-wdgwars"

DEFAULT_ENDPOINT = "https://wdgwars.pl/api/upload/"
ME_API_URL = "https://wdgwars.pl/api/me"
BATCH_SIZE = 1000

# ── Scheduler constants ─────────────────────────────────────────────────────
SCHEDULE_MARKER = "managed-by-heimdall"
SYSTEMD_UNIT_NAME = "heimdall"  # .service + .timer share this stem
WINDOWS_TASK_NAME = "Heimdall"
DEFAULT_SCHEDULE_TIME = "03:00"
TARGET_FIELDS = ("timestamp", "node_id", "type", "name", "lat", "lon", "rssi", "snr")

MESHMAPPER_RX_HEADERS = (
    "timestamp", "repeater_id", "snr", "rssi", "path_length",
    "header", "latitude", "longitude", "path_hops",
)

DEFAULT_NODE_TYPE = "repeater"

# A real MeshMapper export is a multi-section file. Each block starts with a
# marker line like "--- DISC Log ---" and carries its own header row:
#
#   --- TX Log ---
#   timestamp,latitude,longitude,power,events
#   2026-06-27T11:37:20.859937,0.0,0.0,0.6,0CE8(-0.25)
#
#   --- DISC Log ---
#   timestamp,latitude,longitude,noisefloor,node_count,nodes
#   2026-06-27T11:36:48.792735,0.0,0.0,-99,2,910E(R)(-6.00),0CE8(R)(1.25)
#
# A flat single-section file (the legacy "Logs -> Copy CSV" RX export) has no
# markers; we treat the whole file as one unnamed section so those keep
# parsing exactly as before.
_SECTION_RE = re.compile(r"^---\s*(.+?)\s+Log\s*---\s*$", re.IGNORECASE)

# Columns that pack a list of heard nodes into a single (trailing) field:
# DISC uses "nodes", TX uses "events". The MeshCore offline-JSON RX pings use
# "heard_repeats" with the same token grammar.
_PACKED_NODE_COLUMNS = ("nodes", "events", "heard_repeats")

# One packed node token:
#   DISC: 910E(R)(-6.00)  -> id=910E, marker=R, snr=-6.00
#   TX:   0CE8(-0.25)     -> id=0CE8, marker=None, snr=-0.25
_NODE_TOKEN_RE = re.compile(
    r"^([0-9A-Fa-f]{2,})"          # node id (variable-width hex)
    r"(?:\(([A-Za-z])\))?"          # optional single-letter type marker, e.g. (R)
    r"\(([-+]?\d+(?:\.\d+)?)\)$"     # (snr) in parentheses
)

# Node-type markers seen in real exports. Only "R" (repeater) is confirmed
# from the 2026-06-27 baseline; other markers (companion/client, room server)
# are normalised to the default until a real sample pins their letters down.
# Do not guess at letters we have not actually seen.
_NODE_TYPE_MARKERS = {"R": "repeater"}


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _node_token_to_record(token: str, timestamp: str,
                          lat: float, lon: float) -> dict[str, Any] | None:
    """Parse one ID(snr) / ID(R)(snr) token into a meshcore record.

    rssi is None: TX/DISC/RX packed tokens carry SNR but no per-node RSSI
    (the section only logs the receiver's noise floor, not a signal level).
    Returns None for tokens that don't match the grammar.
    """
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

# Explicit SSL context. urllib defaults to system trust + full cert verification
# since Python 3.4.3 (PEP 476); being explicit just makes that obvious in review.
_SSL_CTX = ssl.create_default_context()


# ---------------------------------------------------------------------------
# Color tags
# ---------------------------------------------------------------------------

_USE_COLOR = sys.stderr.isatty() and os.environ.get("NO_COLOR") is None


def _tag(label: str, code: str) -> str:
    if _USE_COLOR:
        return f"\033[{code}m{label}\033[0m"
    return label


def _OK() -> str:   return _tag("[OK]", "1;32")
def _FAIL() -> str: return _tag("[FAIL]", "1;31")
def _INFO() -> str: return _tag("[..]", "1;36")


# ---------------------------------------------------------------------------
# Config dir + persistent API key
# ---------------------------------------------------------------------------

def _config_dir() -> Path:
    """Persistent config location. %APPDATA%\\heimdall on Windows, XDG on Unix."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "heimdall"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "heimdall"


def _key_path() -> Path:
    return _config_dir() / "api.key"


def load_key(cli_key: str | None) -> str:
    """Resolve API key in priority order:
    1. --api-key CLI flag
    2. $WDGWARS_API_KEY env var
    3. saved api.key under the user config dir
    """
    if cli_key:
        return cli_key.strip()
    env = os.environ.get("WDGWARS_API_KEY", "").strip()
    if env:
        return env
    p = _key_path()
    if p.exists():
        try:
            return p.read_text().strip()
        except Exception as e:
            print(f"warn: could not read {p}: {e}", file=sys.stderr)
    return ""


def save_key(key: str) -> None:
    """Save the API key to user config. Refuses to write through a symlink
    so a hostile redirect cannot trick us into overwriting unrelated files."""
    p = _key_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_symlink():
        sys.exit(f"refusing to write through symlink: {p} -> {os.readlink(p)}\n"
                 f"remove the symlink and re-run --save-key")
    # Open with restrictive mode BEFORE writing so the secret is never
    # world-readable, even briefly.
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (key.strip() + "\n").encode())
    finally:
        os.close(fd)
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass
    print(f"[heimdall] saved API key to {p}", file=sys.stderr)
    print(f"[heimdall] (file mode 600 on Unix, only your user can read it)", file=sys.stderr)
    print(f"[heimdall] you can now run uploads without --api-key or env var",
          file=sys.stderr)


def _scrub(text: str, key: str) -> str:
    """If the API key ever leaks into a server error or trace, redact before printing."""
    if key and len(key) > 8 and key in text:
        return text.replace(key, key[:4] + "..." + key[-4:])
    return text


# ---------------------------------------------------------------------------
# /api/me whoami check
# ---------------------------------------------------------------------------

def check_whoami(key: str) -> int:
    """Hit /api/me to validate the key. Prints username + counts on success.
    Never echoes the API key in any output, even on failure."""
    req = urllib.request.Request(
        ME_API_URL,
        headers={"X-API-Key": key,
                 "User-Agent": f"heimdall/{__version__}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            data = json.loads(resp.read().decode())
            if not data.get("ok"):
                err = data.get("error", "unknown")
                print(f"[heimdall] key rejected: {_scrub(err, key)}",
                      file=sys.stderr)
                return 1
            print(f"[heimdall] key OK, user={data.get('username')}",
                  file=sys.stderr)
            # Print whatever counters the server gives us. Heimdall's interest
            # is meshcore, but show the full picture so users can sanity-check.
            parts = []
            for label in ("wifi", "ble", "aircraft", "meshcore", "total"):
                if label in data:
                    parts.append(f"{label}={data.get(label, 0)}")
            if parts:
                print(f"[heimdall]   " + " ".join(parts), file=sys.stderr)
            return 0
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        print(f"[heimdall] HTTP {e.code}: {_scrub(body, key)}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[heimdall] whoami failed: {_scrub(str(e), key)}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Interactive setup wizard
# ---------------------------------------------------------------------------

def _prompt_yes_no(question: str, default: bool = True) -> bool:
    """Ask a y/n question on stderr. Returns the default on EOF or Ctrl+C
    so non-interactive runs do not hang.

    Always emits a newline after the answer when stdin is piped — interactive
    TTY input gets its newline from the terminal, piped input doesn't, which
    would otherwise glue the next section header onto the prompt line.
    """
    suffix = " [Y/n] " if default else " [y/N] "
    piped = not sys.stdin.isatty()
    while True:
        try:
            print(question + suffix, end="", flush=True, file=sys.stderr)
            line = sys.stdin.readline()
            if not line:
                print("", file=sys.stderr)
                return default
            ans = line.strip().lower()
            if piped:
                print("", file=sys.stderr)
        except (KeyboardInterrupt, EOFError):
            print("", file=sys.stderr)
            return default
        if ans == "":
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print(" (please answer y or n)", file=sys.stderr)


def interactive_setup() -> int:
    """First-run setup. Asks yes/no whether to configure an API key, prompts
    for it, validates against /api/me, and saves it on success.
    Returns 0 on success or skip, 1 on cancel."""
    print("", file=sys.stderr)
    print("-" * 60, file=sys.stderr)
    print(" heimdall, API key setup", file=sys.stderr)
    print("-" * 60, file=sys.stderr)
    print("", file=sys.stderr)
    print(" An API key is ONLY needed if you want to upload to WDGoWars.", file=sys.stderr)
    print(" Local CSV parsing and --preview work without one.", file=sys.stderr)
    print("", file=sys.stderr)
    print(" Get your key from: https://wdgwars.pl/  ->  profile  ->  API Key", file=sys.stderr)
    print(f" It will be saved to: {_key_path()}", file=sys.stderr)
    print("", file=sys.stderr)

    if not _prompt_yes_no(" Set up your WDGoWars API key now?", default=True):
        print("", file=sys.stderr)
        print(" Skipped. You can run setup later with:", file=sys.stderr)
        print("   python3 heimdall.py --setup", file=sys.stderr)
        print("", file=sys.stderr)
        return 0

    while True:
        try:
            if sys.stdin.isatty():
                import getpass
                key = getpass.getpass(" Paste your WDGoWars API key (hidden): ").strip()
            else:
                print(" Paste your WDGoWars API key: ", end="", flush=True,
                      file=sys.stderr)
                key = sys.stdin.readline().strip()
        except (KeyboardInterrupt, EOFError):
            print("\n[heimdall] setup cancelled, no key saved", file=sys.stderr)
            return 1

        if not key:
            print(" (empty input, try again or Ctrl+C to cancel)\n",
                  file=sys.stderr)
            continue

        print(" Validating key against wdgwars.pl/api/me ...", file=sys.stderr)
        rc = check_whoami(key)
        if rc != 0:
            print(" That key was rejected. Try again, or Ctrl+C to cancel.\n",
                  file=sys.stderr)
            continue

        save_key(key)
        print("", file=sys.stderr)
        print(" Setup complete. You can now run uploads without --api-key:",
              file=sys.stderr)
        print("   python3 heimdall.py path/to/your_export.csv",
              file=sys.stderr)
        print("", file=sys.stderr)
        return 0


# ---------------------------------------------------------------------------
# MeshMapper CSV parsing
# ---------------------------------------------------------------------------

def _normalise_meshmapper_row(row: dict[str, str]) -> dict[str, Any] | None:
    """
    Map one MeshMapper RX-log CSV row to the WDGoWars meshcore schema.

    MeshMapper "Copy CSV" header:
        timestamp,repeater_id,snr,rssi,path_length,header,latitude,longitude,path_hops

    Target schema:
        timestamp,node_id,type,name,lat,lon,rssi,snr

    Returns None for rows missing required fields or with unparseable numerics.
    Paste-damaged rows skip silently this way.
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
    """Split a MeshMapper export into (section_name, content_lines) blocks.

    A flat file with no "--- X Log ---" markers yields a single
    (None, all_non_blank_lines) block, so legacy exports parse unchanged.
    Section names are upper-cased ("TX", "RX", "DISC").
    """
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
    """Parse a TX/DISC/RX section whose heard nodes are packed into a trailing
    column. Everything from `packed_idx` onward is treated as node tokens,
    because that column holds a comma-separated, unquoted token list that
    csv.reader splits into multiple trailing fields."""
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
    """Parse one section (header line + data lines) to meshcore records."""
    if not block:
        return []
    header = next(csv.reader([block[0]]))
    cols = [c.strip() for c in header]
    lower = [c.lower() for c in cols]
    for key in _PACKED_NODE_COLUMNS:
        if key in lower:
            return _parse_packed_section(cols, lower.index(key), block[1:])
    # Flat RX section (legacy "Copy CSV" shape): map via the row normaliser.
    out: list[dict[str, Any]] = []
    for row in csv.DictReader(block):
        norm = _normalise_meshmapper_row(row)
        if norm is not None:
            out.append(norm)
    return out


def parse_meshmapper_text(text: str) -> list[dict[str, Any]]:
    """Parse MeshMapper CSV text (flat or multi-section) to meshcore records."""
    records: list[dict[str, Any]] = []
    for _name, block in _split_sections(text.splitlines()):
        records.extend(_parse_section(block))
    return records


def parse_meshmapper_csv(path: Path) -> list[dict[str, Any]]:
    return parse_meshmapper_text(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# MeshCore offline ping-log JSON parsing
# ---------------------------------------------------------------------------

def _epoch_to_iso(ts: Any) -> str:
    """Render a ping timestamp as ISO-8601. Offline-JSON pings use epoch
    seconds; pass through strings unchanged (already ISO in practice)."""
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
    """Map one offline-JSON ping to meshcore records.

    DISC pings name a single repeater with full telemetry (real local_rssi +
    local_snr + node_type). RX pings carry a `heard_repeats` token list with
    SNR only, same grammar as the CSV packed columns (so rssi stays None).
    """
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
    """Parse a MeshCore 'offline' ping-log JSON export (DISC + RX pings)."""
    return parse_offline_json_obj(json.loads(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Format dispatch
# ---------------------------------------------------------------------------

def parse_file(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Detect the capture format and parse it. Returns (records, format_id).

    Dispatch by extension first; for unknown/missing extensions, sniff the
    first non-space byte ('{' -> JSON, else CSV).
    """
    suffix = path.suffix.lower()
    if suffix == ".json":
        return parse_offline_json(path), "meshcore-offline-json"
    if suffix in (".csv", ".txt"):
        return parse_meshmapper_csv(path), "meshmapper-csv"
    head = path.read_text(encoding="utf-8", errors="replace").lstrip()[:1]
    if head == "{":
        return parse_offline_json(path), "meshcore-offline-json"
    return parse_meshmapper_csv(path), "meshmapper-csv"


# ---------------------------------------------------------------------------
# Upload envelope + POST
# ---------------------------------------------------------------------------

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
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
                results.append((resp.status, resp.read().decode(errors="replace")))
        except urllib.error.HTTPError as e:
            results.append((e.code, e.read().decode(errors="replace")))
    return results


# ---------------------------------------------------------------------------
# Daily version check + self-update
# ---------------------------------------------------------------------------

def _check_for_update() -> str | None:
    """Quick non-blocking version check against the GitHub releases API.
    Cached for 24h in the user's config dir so we do not hammer the API.
    Returns the latest tag if newer than __version__, else None."""
    cache = _config_dir() / "version-check.json"
    try:
        if cache.exists():
            blob = json.loads(cache.read_text())
            if time.time() - blob.get("checked_at", 0) < 86400:
                latest = blob.get("latest")
                return latest if latest and latest != __version__ else None
    except Exception:
        pass
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            headers={"User-Agent": f"heimdall/{__version__}"})
        with urllib.request.urlopen(req, timeout=3, context=_SSL_CTX) as r:
            data = json.loads(r.read())
            latest = (data.get("tag_name") or "").lstrip("v")
    except Exception:
        return None
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"checked_at": time.time(), "latest": latest}))
    except Exception:
        pass
    return latest if latest and latest != __version__ else None


def _run_update() -> int:
    """Try to update heimdall in place. Uses `git pull` if we are in a git
    checkout; otherwise falls back to fetching heimdall.py from raw GitHub.
    Either path also refreshes requirements.txt and runs pip install, so a
    future release that adds or bumps a dep doesn't leave the user with an
    updated heimdall.py importing a module they don't have."""
    import subprocess
    script_dir = Path(__file__).resolve().parent
    git_dir = script_dir / ".git"
    if git_dir.exists():
        print(f"[heimdall] updating via git pull in {script_dir}", file=sys.stderr)
        try:
            r = subprocess.run(["git", "-C", str(script_dir), "pull", "--ff-only"],
                               capture_output=True, text=True, timeout=30)
            print(r.stdout.strip(), file=sys.stderr)
            if r.returncode != 0:
                print(r.stderr.strip(), file=sys.stderr)
                return r.returncode
            _pip_install_requirements(script_dir)
            print(f"[heimdall] now on heimdall v{__version__} (re-run with "
                  f"--version to confirm latest)", file=sys.stderr)
            return 0
        except FileNotFoundError:
            print("[heimdall] git not found in PATH. Install git, or download "
                  "heimdall.py manually.", file=sys.stderr)
            return 1
    else:
        return _update_from_raw(script_dir)


def _fetch_raw(path: str, dest: Path) -> bool:
    """Fetch a file from the repo's main branch to dest atomically.
    Returns True on success, False on failure (logs the reason)."""
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{path}"
    print(f"[heimdall] fetching {path} from {raw_url}", file=sys.stderr)
    try:
        req = urllib.request.Request(raw_url, headers={
            "User-Agent": f"heimdall/{__version__}"})
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as r:
            body = r.read()
    except Exception as e:
        print(f"[heimdall] download of {path} failed: {e}", file=sys.stderr)
        return False
    tmp = dest.with_suffix(dest.suffix + ".new")
    try:
        tmp.write_bytes(body)
        os.replace(tmp, dest)
    except OSError as e:
        print(f"[heimdall] couldn't write {dest}: {e}", file=sys.stderr)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    return True


def _pip_install_requirements(script_dir: Path) -> None:
    """Best-effort `python -m pip install -r requirements.txt` against the
    interpreter currently running heimdall. Never fails the caller — prints
    a clear hint if pip is missing or the install errors out, so the update
    return code still reflects the heimdall.py update itself.

    Heimdall has no third-party deps today (requirements.txt is a
    comment-only placeholder), so this is a no-op in practice. The helper
    is here so future releases that add a dep self-heal without needing
    another wrapper-script revision."""
    import subprocess
    req = script_dir / "requirements.txt"
    if not req.exists():
        return
    # Skip entirely if requirements.txt has no actual install lines —
    # avoids printing a misleading "installing deps" banner when there's
    # nothing to install.
    has_deps = any(
        line.strip() and not line.lstrip().startswith("#")
        for line in req.read_text(encoding="utf-8", errors="replace").splitlines()
    )
    if not has_deps:
        return
    print(f"[heimdall] installing/refreshing deps from {req.name} "
          f"(python -m pip install --upgrade -r requirements.txt)", file=sys.stderr)
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade",
                            "-r", str(req)], timeout=300)
    except FileNotFoundError:
        print("[heimdall] python not found to invoke pip; run "
              "`python -m pip install -r requirements.txt` manually.",
              file=sys.stderr)
        return
    except subprocess.TimeoutExpired:
        print("[heimdall] pip install timed out; run "
              "`python -m pip install -r requirements.txt` manually.",
              file=sys.stderr)
        return
    if r.returncode != 0:
        print(f"[heimdall] pip install exited {r.returncode}; if the import "
              f"errors below mention a missing module, run "
              f"`python -m pip install -r requirements.txt` manually.",
              file=sys.stderr)


def _update_from_raw(script_dir: Path) -> int:
    """Non-git fallback for --update: fetch heimdall.py + requirements.txt
    from raw GitHub and replace the local files atomically, then refresh
    deps. Works for ZIP-downloaded installs."""
    target = script_dir / "heimdall.py"
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/heimdall.py"
    print(f"[heimdall] not a git checkout. Fetching latest heimdall.py from "
          f"{raw_url}", file=sys.stderr)
    try:
        req = urllib.request.Request(raw_url, headers={
            "User-Agent": f"heimdall/{__version__}"})
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as r:
            new_text = r.read().decode("utf-8")
    except Exception as e:
        print(f"[heimdall] download failed: {e}", file=sys.stderr)
        print(f"[heimdall] manual download: "
              f"https://github.com/{GITHUB_REPO}/releases/latest", file=sys.stderr)
        return 1
    try:
        import ast
        ast.parse(new_text)
    except SyntaxError as e:
        print(f"[heimdall] downloaded file failed to parse, aborting: {e}",
              file=sys.stderr)
        return 1
    import re as _re
    m = _re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']',
                   new_text, _re.MULTILINE)
    new_version = m.group(1) if m else "?"
    if new_version == __version__:
        print(f"[heimdall] already on the latest (v{__version__}). Refreshing "
              f"requirements.txt in case a pinned dep moved.", file=sys.stderr)
        _fetch_raw("requirements.txt", script_dir / "requirements.txt")
        _pip_install_requirements(script_dir)
        return 0
    tmp = target.with_suffix(".py.new")
    try:
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, target)
    except OSError as e:
        print(f"[heimdall] couldn't write {target}: {e}", file=sys.stderr)
        try:
            tmp.unlink()
        except OSError:
            pass
        return 1
    print(f"[heimdall] updated v{__version__} to v{new_version}", file=sys.stderr)
    _fetch_raw("requirements.txt", script_dir / "requirements.txt")
    _pip_install_requirements(script_dir)
    print(f"[heimdall] re-run heimdall to pick up the new code "
          f"(the current process is still running the old version).",
          file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# Scheduler: install/remove a daily timer on systemd / cron / schtasks
# ---------------------------------------------------------------------------
#
# Mechanism per OS, matching wigle-to-wdgwars exactly:
#   Linux with systemd  → user systemd units in ~/.config/systemd/user/
#                         (timer + service, OnCalendar daily at HH:MM)
#   Linux without systemd, macOS → user crontab
#   Windows             → schtasks /Create /SC DAILY /ST HH:MM
#
# Heimdall, unlike wigle, has no pull-from-source flavour. The schedule
# must point at a CSV file the user keeps refreshing (e.g. their nightly
# MeshMapper export). --schedule-csv is the required input for the
# headless install; the wizard variant prompts for it.
#
# A "# managed-by-heimdall" marker comment goes into every artifact so
# --unschedule can find and remove them cleanly without touching the
# user's own crontab/systemd unit dir entries.

def _python_exe() -> str:
    return sys.executable


def _script_path() -> Path:
    return Path(__file__).resolve()


def _systemd_user_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user"


def _has_systemd() -> bool:
    """True only on a Linux host that actually runs systemd as PID 1 and
    has systemctl on PATH. Avoids the WSL false-positive where systemctl
    is installed but `/run/systemd/system` is absent."""
    if not sys.platform.startswith("linux"):
        return False
    if shutil.which("systemctl") is None:
        return False
    return Path("/run/systemd/system").exists()


def _schedule_mechanism() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux") and _has_systemd():
        return "systemd"
    return "cron"


def _validate_hhmm(s: str) -> str:
    """Parse 'HH:MM' (24h). Returns canonical 'HH:MM' or raises ValueError."""
    parts = s.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"time must be HH:MM, got {s!r}")
    hh = int(parts[0])
    mm = int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"time out of range: {s!r}")
    return f"{hh:02d}:{mm:02d}"


def _shell_quote(s: str) -> str:
    """Minimal POSIX shell quoting for systemd ExecStart and cron lines."""
    if not s:
        return "''"
    safe = ("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789@%_-+=:,./")
    if all(c in safe for c in s):
        return s
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _schedule_argv(csv_path: Path) -> list[str]:
    """Build the heimdall argv that the scheduler will run.

    Always reads the saved key from disk (no --key on the command line — that
    would leak the secret into the unit file / crontab / schtasks output,
    all of which are readable by other processes on the box).
    """
    return [_python_exe(), str(_script_path()), str(csv_path)]


# ── Pure renderers (no side effects, easy to unit-test) ─────────────────────

def render_systemd_units(time_hhmm: str, csv_path: Path,
                         python_exe: str, script_path: Path,
                         dry_run: bool = False) -> dict[str, str]:
    """Render (service, timer) unit text. Pure — does not touch disk."""
    time_hhmm = _validate_hhmm(time_hhmm)
    argv = [python_exe, str(script_path), str(csv_path)]
    if dry_run:
        argv.append("--dry-run")
    exec_start = " ".join(_shell_quote(a) for a in argv)
    desc_suffix = " [DRY-RUN]" if dry_run else ""
    service = (
        "[Unit]\n"
        f"Description=Heimdall daily MeshCore push{desc_suffix}\n"
        f"# {SCHEDULE_MARKER}\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={exec_start}\n"
    )
    timer = (
        "[Unit]\n"
        f"Description=Run heimdall daily at {time_hhmm}\n"
        f"# {SCHEDULE_MARKER}\n"
        "\n"
        "[Timer]\n"
        f"OnCalendar=*-*-* {time_hhmm}:00\n"
        "Persistent=true\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return {"service": service, "timer": timer}


def render_cron_line(time_hhmm: str, csv_path: Path,
                     python_exe: str, script_path: Path,
                     dry_run: bool = False) -> str:
    """Render the cron line for the daily run. Pure."""
    time_hhmm = _validate_hhmm(time_hhmm)
    hh, mm = time_hhmm.split(":")
    argv = [python_exe, str(script_path), str(csv_path)]
    if dry_run:
        argv.append("--dry-run")
    cmd = " ".join(_shell_quote(a) for a in argv)
    log = "$HOME/.heimdall-cron.log"
    return (f"{int(mm)} {int(hh)} * * * {cmd} "
            f">> {log} 2>&1  # {SCHEDULE_MARKER}\n")


def render_schtasks_create(time_hhmm: str, csv_path: Path,
                           python_exe: str, script_path: Path,
                           dry_run: bool = False) -> list[str]:
    """Render the `schtasks /Create` argv for Windows. Pure.

    No `cmd /c "... >> log 2>&1"` wrap: schtasks /TR hard-caps the action
    string at 261 characters and the wrap form blows past that once the
    venv-python + script + CSV paths are included. Users see daily-run
    outcome via Task Scheduler's "Last Result" column instead, or by
    firing the same command from PowerShell to inspect stderr.
    """
    time_hhmm = _validate_hhmm(time_hhmm)
    argv = [python_exe, str(script_path), str(csv_path)]
    if dry_run:
        argv.append("--dry-run")
    action = " ".join(f'"{a}"' if " " in a else a for a in argv)
    return ["schtasks", "/Create", "/TN", WINDOWS_TASK_NAME,
            "/TR", action, "/SC", "DAILY", "/ST", time_hhmm,
            "/RL", "LIMITED", "/F"]


# ── Installers ──────────────────────────────────────────────────────────────

def install_systemd_user(time_hhmm: str, csv_path: Path,
                         dry_run: bool = False) -> int:
    units = render_systemd_units(time_hhmm, csv_path,
                                 _python_exe(), _script_path(),
                                 dry_run=dry_run)
    unit_dir = _systemd_user_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    service_path = unit_dir / f"{SYSTEMD_UNIT_NAME}.service"
    timer_path = unit_dir / f"{SYSTEMD_UNIT_NAME}.timer"
    service_path.write_text(units["service"])
    print(f"[schedule] wrote {service_path}", file=sys.stderr)
    timer_path.write_text(units["timer"])
    print(f"[schedule] wrote {timer_path}", file=sys.stderr)
    target = f"{SYSTEMD_UNIT_NAME}.timer"
    for cmd in (["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", "--now", target]):
        rc = subprocess.call(cmd)
        if rc != 0:
            print(f"[schedule] '{' '.join(cmd)}' returned {rc}",
                  file=sys.stderr)
            return rc
    print(f"[schedule] enabled and started {target}", file=sys.stderr)
    print(f"[schedule] status:  systemctl --user status {target}",
          file=sys.stderr)
    print(f"[schedule] logs:    journalctl --user -u {target} -f",
          file=sys.stderr)
    return 0


def uninstall_systemd_user() -> int:
    unit_dir = _systemd_user_dir()
    found = False
    for name in (f"{SYSTEMD_UNIT_NAME}.timer",
                 f"{SYSTEMD_UNIT_NAME}.service"):
        unit = unit_dir / name
        if unit.exists():
            found = True
            subprocess.call(["systemctl", "--user", "stop", name],
                            stderr=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL)
            subprocess.call(["systemctl", "--user", "disable", name],
                            stderr=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL)
            unit.unlink()
            print(f"[schedule] removed {unit}", file=sys.stderr)
    if found:
        subprocess.call(["systemctl", "--user", "daemon-reload"])
    else:
        print("[schedule] no heimdall systemd units found", file=sys.stderr)
    return 0


def install_cron(time_hhmm: str, csv_path: Path,
                 dry_run: bool = False) -> int:
    if shutil.which("crontab") is None:
        print("[schedule] crontab not found on PATH", file=sys.stderr)
        return 1
    new_line = render_cron_line(time_hhmm, csv_path,
                                _python_exe(), _script_path(),
                                dry_run=dry_run)
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        current = r.stdout if r.returncode == 0 else ""
    except FileNotFoundError:
        return 1
    cleaned = "\n".join(l for l in current.splitlines()
                        if SCHEDULE_MARKER not in l)
    combined = (cleaned.rstrip() + "\n" + new_line) if cleaned.strip() else new_line
    proc = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
    proc.communicate(combined)
    if proc.returncode != 0:
        print(f"[schedule] crontab write failed (rc={proc.returncode})",
              file=sys.stderr)
        return proc.returncode
    print(f"[schedule] added cron entry (marker: {SCHEDULE_MARKER})",
          file=sys.stderr)
    print(f"[schedule] view: crontab -l", file=sys.stderr)
    print(f"[schedule] log:  tail -f ~/.heimdall-cron.log", file=sys.stderr)
    return 0


def uninstall_cron() -> int:
    if shutil.which("crontab") is None:
        return 0
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if r.returncode != 0:
            return 0
        current = r.stdout
    except FileNotFoundError:
        return 0
    cleaned = "\n".join(l for l in current.splitlines()
                        if SCHEDULE_MARKER not in l)
    if cleaned == current.rstrip("\n"):
        print("[schedule] no heimdall cron entries found", file=sys.stderr)
        return 0
    proc = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
    proc.communicate(cleaned)
    print("[schedule] removed heimdall cron entries", file=sys.stderr)
    return 0


def install_windows_task(time_hhmm: str, csv_path: Path,
                         dry_run: bool = False) -> int:
    cmd = render_schtasks_create(time_hhmm, csv_path,
                                 _python_exe(), _script_path(),
                                 dry_run=dry_run)
    rc = subprocess.call(cmd)
    if rc != 0:
        return rc
    print(f"[schedule] created task: {WINDOWS_TASK_NAME}", file=sys.stderr)
    print(f"[schedule] view:    schtasks /Query /TN {WINDOWS_TASK_NAME}",
          file=sys.stderr)
    print(f"[schedule] run now: schtasks /Run /TN {WINDOWS_TASK_NAME}",
          file=sys.stderr)
    print(f"[schedule] (Task Scheduler doesn't capture stdout — to see "
          f"what a run did, fire it from PowerShell directly.)",
          file=sys.stderr)
    return 0


def uninstall_windows_task() -> int:
    rc = subprocess.call(["schtasks", "/Delete", "/TN", WINDOWS_TASK_NAME,
                          "/F"], stderr=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL)
    if rc == 0:
        print(f"[schedule] removed scheduled task: {WINDOWS_TASK_NAME}",
              file=sys.stderr)
    else:
        print(f"[schedule] no scheduled task named {WINDOWS_TASK_NAME} found",
              file=sys.stderr)
    return 0


def cmd_schedule_headless(args) -> int:
    """Headless --schedule path. Reads time + CSV path + dry-run from args."""
    if not args.schedule_csv:
        sys.exit("--schedule needs --schedule-csv PATH (the CSV file to "
                 "upload daily). Heimdall has no pull-from-source flavour, "
                 "so the scheduler must point at a file you keep refreshing.")
    csv_path = Path(args.schedule_csv).resolve()
    time_hhmm = _validate_hhmm(args.schedule_time or DEFAULT_SCHEDULE_TIME)
    dry_run = bool(args.schedule_dry_run)
    if not _key_path().exists() and not os.environ.get("WDGWARS_API_KEY"):
        sys.exit("--schedule needs a saved WDGoWars API key (run --setup "
                 "first), or set WDGWARS_API_KEY in the environment the "
                 "scheduler will run under.")
    mech = _schedule_mechanism()
    if mech == "systemd":
        return install_systemd_user(time_hhmm, csv_path, dry_run=dry_run)
    if mech == "cron":
        return install_cron(time_hhmm, csv_path, dry_run=dry_run)
    if mech == "windows":
        return install_windows_task(time_hhmm, csv_path, dry_run=dry_run)
    sys.exit(f"unsupported platform for --schedule: {sys.platform}")


def cmd_unschedule() -> int:
    """Remove every heimdall-managed schedule entry on this platform."""
    rcs = []
    if sys.platform == "win32":
        rcs.append(uninstall_windows_task())
    else:
        if _has_systemd():
            rcs.append(uninstall_systemd_user())
        rcs.append(uninstall_cron())
    return 0 if all(rc == 0 for rc in rcs) else 1


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=f"Heimdall v{__version__}, MeshMapper CSV to WDGoWars "
                    f"meshcore_nodes uplink.",
    )
    p.add_argument("--version", action="version",
                   version=f"heimdall {__version__}")
    p.add_argument("--update", action="store_true",
                   help="pull the latest version of heimdall (uses git pull if "
                        "you cloned the repo, otherwise downloads heimdall.py "
                        "from GitHub)")
    p.add_argument("csv", nargs="?", type=Path, metavar="capture",
                   help="MeshMapper CSV export (flat or multi-section TX/RX/"
                        "DISC) or a MeshCore offline ping-log JSON. Format is "
                        "auto-detected. Not required for --setup, --save-key, "
                        "--whoami, or --update.")
    p.add_argument("--setup", action="store_true",
                   help="interactive first-time setup, prompts for your "
                        "WDGoWars API key, validates it, and saves it locally.")
    p.add_argument("--save-key", metavar="KEY",
                   help="non-interactive: save the given API key to the user "
                        "config dir. Prefer --setup for first-time install.")
    p.add_argument("--whoami", action="store_true",
                   help="validate your stored API key by hitting /api/me and "
                        "showing account stats; exits after.")
    # --key is the canonical name (matches Muninn + wigle-to-wdgwars).
    # --api-key is the legacy name; kept as a deprecated alias for one
    # release. Drop in v0.4 unless the operator extends the deprecation
    # window. The actual value lands on args.key after the merge below.
    p.add_argument("--key", help="WDGoWars API key (or set WDGWARS_API_KEY, "
                                 "or run --setup once to save it)")
    p.add_argument("--api-key", dest="api_key_legacy",
                   help=argparse.SUPPRESS)  # deprecated alias for --key
    # --api-url is the canonical name (matches Muninn). --endpoint is the
    # legacy name; same deprecation treatment as --api-key.
    p.add_argument("--api-url", default=None,
                   help=f"override upload URL (default: {DEFAULT_ENDPOINT})")
    p.add_argument("--endpoint", dest="endpoint_legacy", default=None,
                   help=argparse.SUPPRESS)  # deprecated alias for --api-url
    p.add_argument("--dry-run", action="store_true",
                   help="build the HMAC-signed envelope but do not POST")
    p.add_argument("--preview", action="store_true",
                   help="print first 6 normalised rows as JSON and exit")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="suppress informational banners (errors still print)")
    p.add_argument("--no-version-check", action="store_true",
                   help="skip the daily GitHub release check entirely")
    # ── Scheduler flags ──
    p.add_argument("--schedule", action="store_true",
                   help="install a daily scheduled upload (systemd / cron / "
                        "schtasks per OS). Pairs with --schedule-csv. "
                        "Headless with --schedule-time / --schedule-dry-run.")
    p.add_argument("--unschedule", action="store_true",
                   help="remove every heimdall-managed scheduled task "
                        "on this host.")
    p.add_argument("--schedule-csv", metavar="PATH",
                   help="path to the CSV that should be uploaded daily. "
                        "Required for --schedule.")
    p.add_argument("--schedule-time", metavar="HH:MM",
                   help=f"24-hour daily run time for --schedule "
                        f"(default: {DEFAULT_SCHEDULE_TIME})")
    p.add_argument("--schedule-dry-run", action="store_true",
                   help="install the schedule with --dry-run baked in. "
                        "Parses + signs but never POSTs. Re-run --schedule "
                        "without this flag to go live.")
    args = p.parse_args(argv)

    # ── Deprecated-flag back-compat ──
    # If the user passed --api-key, hoist it onto args.key (with a warning).
    # Same for --endpoint -> --api-url. Both old names disappear in v0.4.
    if args.api_key_legacy is not None:
        if args.key is None:
            args.key = args.api_key_legacy
        elif args.key != args.api_key_legacy:
            sys.exit("--key and --api-key both given with different values; "
                     "use --key only (--api-key is deprecated).")
        if not args.quiet:
            print("[heimdall] note: --api-key is deprecated, use --key. "
                  "The old name still works for now but will be removed.",
                  file=sys.stderr)
    if args.endpoint_legacy is not None:
        if args.api_url is None:
            args.api_url = args.endpoint_legacy
        elif args.api_url != args.endpoint_legacy:
            sys.exit("--api-url and --endpoint both given with different "
                     "values; use --api-url only (--endpoint is deprecated).")
        if not args.quiet:
            print("[heimdall] note: --endpoint is deprecated, use --api-url. "
                  "The old name still works for now but will be removed.",
                  file=sys.stderr)
    if args.api_url is None:
        args.api_url = DEFAULT_ENDPOINT

    # --update is a top-level mode, run before anything that needs a key/file.
    if args.update:
        return _run_update()

    # Schedule mutation modes — don't need a key in process but the
    # installed unit will need one at run-time. cmd_schedule_headless
    # checks for a saved key and exits early if absent.
    if args.unschedule:
        return cmd_unschedule()
    if args.schedule:
        return cmd_schedule_headless(args)

    # Soft nudge: if a newer release is out, mention it (non-blocking, daily-cached).
    if not args.quiet and not args.no_version_check:
        newer = _check_for_update()
        if newer:
            print(f"[heimdall] note: v{newer} is available "
                  f"(you're on v{__version__}). Run `--update` to upgrade.",
                  file=sys.stderr)

    # Key management modes, handled before requiring an input file.
    if args.setup:
        return interactive_setup()
    if args.save_key:
        save_key(args.save_key)
        return 0
    if args.whoami:
        key = load_key(args.key)
        if not key:
            print("no API key found, run `python3 heimdall.py --setup` "
                  "for first-time setup", file=sys.stderr)
            return 2
        return check_whoami(key)

    # From here on we need a CSV.
    if args.csv is None:
        p.error("the following arguments are required: csv "
                "(or use --setup / --save-key / --whoami / --update / "
                "--schedule / --unschedule)")

    if not args.csv.exists():
        print(f"file not found: {args.csv}", file=sys.stderr)
        return 2

    try:
        nodes, fmt = parse_file(args.csv)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"could not parse {args.csv.name}: {e}", file=sys.stderr)
        return 1
    print(f"parsed {len(nodes)} meshcore nodes from {args.csv.name} ({fmt})")
    if not nodes:
        print("nothing to upload", file=sys.stderr)
        return 1

    if args.preview:
        for row in nodes[:6]:
            print(json.dumps(row))
        return 0

    key = load_key(args.key)
    if not key:
        print("missing API key: pass --key, set WDGWARS_API_KEY, or run "
              "`python3 heimdall.py --setup` once to save it", file=sys.stderr)
        return 2

    rc = 0
    for status, body in upload(nodes, key, endpoint=args.api_url, dry_run=args.dry_run):
        if status == 0:
            print(f"{_INFO()} {body}", file=sys.stderr)
            continue
        if 200 <= status < 300:
            try:
                data = json.loads(body)
                imp = data.get("meshcore_imported", 0)
                seen = data.get("meshcore_already_seen", 0)
                badges = data.get("new_badges") or []
                print(f"{_OK()} accepted by wdgwars.pl. "
                      f"{imp} new meshcore nodes, {seen} already on your account.",
                      file=sys.stderr)
                if badges:
                    print(f"  new badges: {badges}", file=sys.stderr)
            except Exception:
                print(f"{_OK()} accepted by wdgwars.pl (HTTP {status}): "
                      f"{_scrub(body[:200], key)}", file=sys.stderr)
        else:
            data: dict = {}
            try:
                data = json.loads(body)
            except Exception:
                pass
            if status == 413 and isinstance(data, dict) and data.get("error") == "payload-too-large":
                max_b = data.get("max_bytes")
                recv = data.get("received")
                print(
                    f"{_FAIL()} 413 payload-too-large from wdgwars.pl "
                    f"(max_bytes={max_b} received={recv}). LOCOSP added a "
                    f"15 MB upload cap on 2026-06-05; mesh-node payloads are "
                    f"normally well under it, so this is unexpected. Drop the "
                    f"batch size or wait for the next cycle.",
                    file=sys.stderr,
                )
            else:
                print(f"{_FAIL()} rejected by wdgwars.pl (HTTP {status}): "
                      f"{_scrub(body[:200], key)}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
