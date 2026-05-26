<p align="center">
  <img src="assets/banner.png" alt="Heimdall — Odin's watcher for the WDGoWars mesh" width="100%"/>
</p>

<p align="center">
  <a href="https://github.com/HiroAlleyCat/meshcore-to-wdgwars/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/HiroAlleyCat/meshcore-to-wdgwars?color=b08850&label=release"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-b08850.svg"></a>
  <a href="https://github.com/HiroAlleyCat/meshcore-to-wdgwars/blob/main/SECURITY.md"><img alt="Security" src="https://img.shields.io/badge/security-threat%20model-b08850.svg"></a>
</p>

# Heimdall

Convert **MeshMapper** "Logs → Copy CSV" exports (and other Meshcore LoRa capture formats, over time) to WDGoWars-compatible JSON and optionally upload them. Sibling tool to [adsb-to-wdgwars](https://github.com/HiroAlleyCat/adsb-to-wdgwars) (Muninn); same HMAC envelope, same `/api/upload/` endpoint, different payload slot. Muninn fills `aircraft`; Heimdall fills `meshcore_nodes`.

**Scope:** Heimdall is for **Meshcore LoRa observations from your own captures**. The WDGoWars mesh channel is specifically for Meshcore (LoRa, sub-GHz). Other 802.15.4 traffic — Zigbee, Thread, generic mesh-sounding protocols — does not belong here and will not be accepted upstream. If your data came from a real Meshcore receiver (MeshMapper app, T-Beam running Meshcore Companion, Cardputer ADV + LoRa cap, Heltec V3, etc.), you're in the right place.

---

## Pick your path

Heimdall ships in **two flavours** that share the same parsing core. Both are live.

| | **Web (browser)** | **CLI (terminal)** |
|---|---|---|
| **For** | One-off uploads, anyone without Python | Headless boxes, mesh gateways, cron, scripted feeds |
| **Install** | None, open a URL | Clone repo, run `python3 heimdall.py` |
| **Where parsing happens** | In your browser (Pyodide / WASM) | Locally with stdlib Python |
| **Internet required** | Yes (initial page load) | No (only uploads) |
| **Runs without a display** | No | **Yes**, headless-safe |
| **Status** | **Shipped** | **Shipped (alpha)** |

If you're on a Raspberry Pi, a server, or anything without a desktop, **use the CLI**. Scroll down to [CLI install](#cli-install).

The web version lives at [hiroalleycat.github.io/meshcore-to-wdgwars](https://hiroalleycat.github.io/meshcore-to-wdgwars/).

---

## CLI install

Heimdall is pure stdlib Python (no `pip install` step). You have two ways to grab it.

### Option A: git clone (recommended if you have git)

```bash
git clone https://github.com/HiroAlleyCat/meshcore-to-wdgwars
cd meshcore-to-wdgwars
python3 heimdall.py examples/sample.csv --preview
```

`git clone` makes the one-click **Update** workflow (below) seamless.

### Option B: ZIP download (no git required)

1. On the [GitHub repo page](https://github.com/HiroAlleyCat/meshcore-to-wdgwars), click the green **Code** button, then **Download ZIP**.
2. Unzip somewhere convenient.
3. Open a terminal in the unzipped folder and run `python3 heimdall.py examples/sample.csv --preview`.

Both install paths work the same way for daily use. `--update` is smart enough to use `git pull` for a clone, or fall back to fetching the latest `heimdall.py` from raw GitHub for a ZIP install.

### One-time API-key setup

```bash
python3 heimdall.py --setup
```

Walks you through pasting your WDGoWars API key, validates it against `/api/me`, and saves it to:

| OS | Saved location |
|---|---|
| Linux / macOS | `~/.config/heimdall/api.key` (mode `0600`) |
| Windows | `%APPDATA%\heimdall\api.key` |

After setup, you can run uploads with no key flags at all. Windows users can double-click `setup.bat`; Linux/macOS users can run `./setup.sh`.

### The day-to-day workflow

1. In MeshMapper (or your Meshcore capture tool), export the RX log to CSV (in MeshMapper that's **Logs → Copy CSV**).
2. Save it as a `.csv` file on disk.
3. Run `python3 heimdall.py path/to/your_export.csv --preview` to see how Heimdall normalises the rows.
4. When the preview looks right, upload (see below).

### Or just preview

```bash
python3 heimdall.py path/to/your_export.csv --preview
```

Prints the first six normalised rows to stdout as JSON, then exits. No upload, no envelope. Useful for sanity-checking a fresh export.

---

## Uploading to WDGoWars

```bash
# Easiest: after `--setup`, no flags needed.
python3 heimdall.py path/to/your_export.csv

# Or pass the key on the command line each time
python3 heimdall.py path/to/your_export.csv --api-key YOUR_KEY

# Or set it in the environment
export WDGWARS_API_KEY=YOUR_KEY
python3 heimdall.py path/to/your_export.csv

# Build the envelope but don't POST (verify everything before going live)
python3 heimdall.py path/to/your_export.csv --dry-run
```

`--dry-run` builds the full HMAC-signed request (same signature the live upload would send) but does not POST. Useful for confirming the envelope is well-formed before pointing it at the live API.

Records batch in chunks of **1000** per request.

Want to confirm your saved key is good before running a real upload? `python3 heimdall.py --whoami` hits `/api/me` and prints your username + node counts.

---

## Updating

```bash
python3 heimdall.py --update
```

- If you cloned the repo, this runs `git pull --ff-only` in the install directory.
- If you used the ZIP download, this fetches the latest `heimdall.py` from raw GitHub, validates that it parses as Python, and replaces the local file atomically.

Helper scripts for double-click users:

| Helper | What it does |
|---|---|
| `setup.sh` / `setup.bat` | One-time API-key setup (calls `--setup`) |
| `run.sh path/to/your.csv` / `run.bat path\to\your.csv` | Upload a single CSV |
| `update.sh` / `update.bat` | Self-update to the latest Heimdall |

Heimdall also does a quiet daily check against the GitHub releases API. If a newer version is published, the next run prints a one-line note suggesting `--update`. The check is cached for 24h in your config dir, runs with a 3-second timeout, and can be disabled with `--no-version-check` (or globally suppressed with `--quiet`).

---

## Supported input formats

| Format | Detection | Source |
|---|---|---|
| **MeshMapper "Copy CSV"** | Header row `timestamp,repeater_id,snr,rssi,...` | MeshMapper app, RX log export |
| _Meshcore Companion serial dump_ | _Planned_ | T-Beam / Heltec / Wio Tracker via USB serial |
| _Raw MQTT capture_ | _Planned_ | `mosquitto_sub` against a Meshcore broker |
| _Cardputer ADV LoRa cap log_ | _Planned_ | M5Stack Cardputer Advanced with LoRa module |

Italicised rows are not yet implemented — they are on the roadmap once sample data lands. **Have a real capture you can share? See the pinned ["Wanted: real-world Meshcore capture samples"](https://github.com/HiroAlleyCat/meshcore-to-wdgwars/issues/1) issue for what we're looking for and how to scrub before sending.**

---

## All command-line flags

```
python3 heimdall.py [csv] [options]
```

| Flag | Purpose | Default |
|---|---|---|
| `csv` (positional) | Path to the MeshMapper CSV export. Optional with `--setup`, `--save-key`, `--whoami`, `--update`. | (none) |
| `--setup` | Interactive first-time setup. Prompts for your WDGoWars API key, validates it against `/api/me`, and saves it to your user config dir. | off |
| `--save-key KEY` | Non-interactive: save the given API key to the user config dir. Prefer `--setup` for first-time install. | off |
| `--whoami` | Validate your stored API key by hitting `/api/me` and printing username + node counts. | off |
| `--api-key KEY` | WDGoWars API key. Overrides the `WDGWARS_API_KEY` env var and the saved key. | env / saved |
| `--preview` | Parse the file, print the first six normalised rows as JSON, then exit. No envelope build, no upload. | off |
| `--dry-run` | Build the full HMAC-signed request envelope (same bytes the live upload would send), print a short summary per chunk, but do **not** POST. | off |
| `--endpoint URL` | Override the WDGoWars upload endpoint. | `https://wdgwars.pl/api/upload/` |
| `--update` | Self-update via `git pull` (clone) or raw-GitHub fetch (ZIP install). | off |
| `--no-version-check` | Skip the daily GitHub release check for this run. | off |
| `-q`, `--quiet` | Suppress informational banners. Errors still print. | off |
| `--version` | Print version and exit. | (none) |
| `-h`, `--help` | Print help and exit. | (none) |

### Examples

```bash
# One-time setup (saves your API key)
python3 heimdall.py --setup

# Sanity-check a fresh export
python3 heimdall.py my-capture.csv --preview

# Build envelope but don't POST (verify HMAC + payload shape)
python3 heimdall.py my-capture.csv --dry-run

# Real upload, key already saved by --setup
python3 heimdall.py my-capture.csv

# Real upload, key on command line
python3 heimdall.py my-capture.csv --api-key YOUR_KEY

# Real upload, key in env (keeps the key out of shell history)
export WDGWARS_API_KEY=YOUR_KEY
python3 heimdall.py my-capture.csv

# Confirm the saved key is valid
python3 heimdall.py --whoami

# Self-update to latest
python3 heimdall.py --update

# Point at a self-hosted proxy (see web/serve.py)
python3 heimdall.py my-capture.csv \
  --endpoint http://127.0.0.1:8765/api/upload/
```

Records batch in chunks of **1000** per request.

---

## Architecture

```
                ┌────────────────────────────────────────┐
                │              heimdall.py                │
                │  parse_meshmapper_csv → _normalise_*    │
                │  build_envelope (HMAC + base64 + nonce) │
                │  upload() with 1000-row chunking        │
                └────────────┬───────────────┬────────────┘
                             │               │
                   ┌─────────▼──────┐ ┌──────▼──────────────┐
                   │ CLI (argparse) │ │ Pyodide web (later) │
                   └────────────────┘ └─────────────────────┘
```

Upload is an HMAC-signed envelope, byte-identical to Muninn's, with the `meshcore_nodes` slot filled:

```python
payload   = {"networks": [], "aircraft": [], "meshcore_nodes": chunk}
body_json = json.dumps(payload, separators=(",", ":"))
data_b64  = base64.b64encode(body_json.encode()).decode()
nonce     = secrets.token_hex(8)
sig       = hmac.new(api_key.encode(),
                     (nonce + data_b64).encode(),
                     hashlib.sha256).hexdigest()
envelope  = {"data": data_b64, "nonce": nonce, "sig": sig}
# POST → https://wdgwars.pl/api/upload/ with X-API-Key: <key>
```

The target per-record schema is `timestamp, node_id, type, name, lat, lon, rssi, snr`. Field aliases for MeshMapper inputs are in `_normalise_meshmapper_row`.

---

## Privacy & data flow

- Capture files **never leave your machine** until you explicitly run an upload command without `--dry-run`. Parsing, normalising, and envelope-building all happen locally.
- The API key is read from `--api-key`, then `$WDGWARS_API_KEY`, then the saved key file. When `--setup` (or `--save-key`) writes the file, it's `chmod 0600` on Unix and lives under the per-user `%APPDATA%` on Windows.
- The bundled `examples/sample.csv` is a **scrubbed** export with `lat=0, lon=0` for every row, so it cannot accidentally produce a real upload (the upstream ingest rejects `0,0` GPS).
- The daily version check hits `https://api.github.com/repos/HiroAlleyCat/meshcore-to-wdgwars/releases/latest` with a `heimdall/<version>` User-Agent, caches the answer for 24h, and sends nothing about you or your data. Disable per-run with `--no-version-check`, or silence globally with `--quiet`.
- No telemetry, no analytics. The only outbound traffic is to the WDGoWars upload endpoint (when you explicitly invoke an upload) and the GitHub release check (cached daily, opt-out via `--no-version-check`).

---

## Credits

- **Muninn** ([adsb-to-wdgwars](https://github.com/HiroAlleyCat/adsb-to-wdgwars)) — parent pattern. HMAC envelope, three-deploy-mode design, Pyodide web flavour all originate there.
- **FusedStamen** — surfaced the WDGoWars mesh ingest target schema and suggested the MeshMapper CSV bridge angle.
- **Wild!Radio** — supplied the MeshMapper RX-log sample used to wire the field map.
- **MeshMapper** ([wiki](https://wiki.meshmapper.net/)) — upstream Meshcore visualisation platform whose CSV export is Heimdall's first supported input.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Related

- [adsb-to-wdgwars](https://github.com/HiroAlleyCat/adsb-to-wdgwars) — Muninn, the aircraft sibling.
- [WDGoWars](https://wdgwars.pl) — the wardriving game these tools feed.
