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
import hashlib
import hmac
import json
import os
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


__version__ = "0.2.2"
GITHUB_REPO = "HiroAlleyCat/meshcore-to-wdgwars"

DEFAULT_ENDPOINT = "https://wdgwars.pl/api/upload/"
ME_API_URL = "https://wdgwars.pl/api/me"
BATCH_SIZE = 1000
TARGET_FIELDS = ("timestamp", "node_id", "type", "name", "lat", "lon", "rssi", "snr")

MESHMAPPER_RX_HEADERS = (
    "timestamp", "repeater_id", "snr", "rssi", "path_length",
    "header", "latitude", "longitude", "path_hops",
)

DEFAULT_NODE_TYPE = "repeater"

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
    so non-interactive runs do not hang."""
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        try:
            print(question + suffix, end="", flush=True, file=sys.stderr)
            line = sys.stdin.readline()
            if not line:
                print("", file=sys.stderr)
                return default
            ans = line.strip().lower()
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


def parse_meshmapper_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        records = []
        for row in reader:
            norm = _normalise_meshmapper_row(row)
            if norm is not None:
                records.append(norm)
        return records


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
    p.add_argument("csv", nargs="?", type=Path,
                   help="MeshMapper CSV export. Not required for --setup, "
                        "--save-key, --whoami, or --update.")
    p.add_argument("--setup", action="store_true",
                   help="interactive first-time setup, prompts for your "
                        "WDGoWars API key, validates it, and saves it locally.")
    p.add_argument("--save-key", metavar="KEY",
                   help="non-interactive: save the given API key to the user "
                        "config dir. Prefer --setup for first-time install.")
    p.add_argument("--whoami", action="store_true",
                   help="validate your stored API key by hitting /api/me and "
                        "showing account stats; exits after.")
    p.add_argument("--api-key", help="WDGoWars API key (or set WDGWARS_API_KEY, "
                                     "or run --setup once to save it)")
    p.add_argument("--endpoint", default=DEFAULT_ENDPOINT,
                   help=f"override upload endpoint (default: {DEFAULT_ENDPOINT})")
    p.add_argument("--dry-run", action="store_true",
                   help="build the HMAC-signed envelope but do not POST")
    p.add_argument("--preview", action="store_true",
                   help="print first 6 normalised rows as JSON and exit")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="suppress informational banners (errors still print)")
    p.add_argument("--no-version-check", action="store_true",
                   help="skip the daily GitHub release check entirely")
    args = p.parse_args(argv)

    # --update is a top-level mode, run before anything that needs a key/file.
    if args.update:
        return _run_update()

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
        key = load_key(args.api_key)
        if not key:
            print("no API key found, run `python3 heimdall.py --setup` "
                  "for first-time setup", file=sys.stderr)
            return 2
        return check_whoami(key)

    # From here on we need a CSV.
    if args.csv is None:
        p.error("the following arguments are required: csv "
                "(or use --setup / --save-key / --whoami / --update)")

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

    key = load_key(args.api_key)
    if not key:
        print("missing API key: pass --api-key, set WDGWARS_API_KEY, or run "
              "`python3 heimdall.py --setup` once to save it", file=sys.stderr)
        return 2

    rc = 0
    for status, body in upload(nodes, key, endpoint=args.endpoint, dry_run=args.dry_run):
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
            print(f"{_FAIL()} rejected by wdgwars.pl (HTTP {status}): "
                  f"{_scrub(body[:200], key)}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
