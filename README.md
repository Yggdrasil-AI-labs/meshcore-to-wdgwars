<p align="center">
  <img src="assets/banner.png" alt="Heimdall — Odin's watcher for the WDGoWars mesh" width="100%"/>
</p>

<p align="center">
  <a href="https://github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars/actions/workflows/ci-quality-gates.yml"><img alt="CI" src="https://github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars/actions/workflows/ci-quality-gates.yml/badge.svg"></a>
  <a href="https://sonarcloud.io/dashboard?id=Yggdrasil-AI-labs_meshcore-to-wdgwars"><img alt="Quality gate" src="https://sonarcloud.io/api/project_badges/measure?project=Yggdrasil-AI-labs_meshcore-to-wdgwars&metric=alert_status"></a>
  <a href="https://github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/Yggdrasil-AI-labs/meshcore-to-wdgwars?color=b08850&label=release"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-b08850.svg"></a>
  <a href="https://github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars/blob/main/SECURITY.md"><img alt="Security" src="https://img.shields.io/badge/security-threat%20model-b08850.svg"></a>
</p>

# Heimdall

Convert **MeshMapper** "Logs → Copy CSV" exports (and other Meshcore LoRa capture formats, over time) to WDGoWars-compatible JSON and optionally upload them. Sibling tool to [adsb-to-wdgwars](https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars) (Muninn); same HMAC envelope, same `/api/upload/` endpoint, different payload slot. Muninn fills `aircraft`; Heimdall fills `meshcore_nodes`.

**Scope:** Heimdall is for **Meshcore LoRa observations from your own captures**. The WDGoWars mesh channel is specifically for Meshcore (LoRa, sub-GHz). Other 802.15.4 traffic — Zigbee, Thread, generic mesh-sounding protocols — does not belong here and will not be accepted upstream. If your data came from a real Meshcore receiver (MeshMapper app, T-Beam running Meshcore Companion, Cardputer ADV + LoRa cap, Heltec V3, etc.), you're in the right place.

## Family

Sibling repos in the WDGoWars feeder family:

- [Muninn](https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars) — ADS-B feeder
- [wigle-to-wdgwars](https://github.com/Yggdrasil-AI-labs/wigle-to-wdgwars) — WiGLE Wi-Fi/BLE feeder
- [gungnir](https://github.com/Yggdrasil-AI-labs/gungnir) — shared HMAC transport library
- [wdgwars-api-tester](https://github.com/Yggdrasil-AI-labs/wdgwars-api-tester) — API surface probe

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
git clone https://github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars
cd meshcore-to-wdgwars
# direct invocation: Heimdall is pure stdlib, runs without a venv
python3 heimdall.py examples/sample.csv --preview
```

`git clone` makes the one-click **Update** workflow (below) seamless.

### Option B: ZIP download (no git required)

1. On the [GitHub repo page](https://github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars), click the green **Code** button, then **Download ZIP**.
2. Unzip somewhere convenient.
3. Open a terminal in the unzipped folder and run `python3 heimdall.py examples/sample.csv --preview`.

Both install paths work the same way for daily use. `--update` is smart enough to use `git pull` for a clone, or fall back to fetching the latest `heimdall.py` from raw GitHub for a ZIP install.

### One-time API-key setup

```bash
# direct invocation: Heimdall is pure stdlib, runs without a venv
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
./run.sh path/to/your_export.csv --preview
```

Prints the first six normalised rows to stdout as JSON, then exits. No upload, no envelope. Useful for sanity-checking a fresh export.

---

## Uploading to WDGoWars

```bash
# Easiest: after `--setup`, no flags needed.
./run.sh path/to/your_export.csv

# Or pass the key on the command line each time
./run.sh path/to/your_export.csv --key YOUR_KEY

# Or set it in the environment
export WDGWARS_API_KEY=YOUR_KEY
./run.sh path/to/your_export.csv

# Build the envelope but don't POST (verify everything before going live)
./run.sh path/to/your_export.csv --dry-run
```

`--dry-run` builds the full HMAC-signed request (same signature the live upload would send) but does not POST. Useful for confirming the envelope is well-formed before pointing it at the live API.

Records batch in chunks of **1000** per request.

Want to confirm your saved key is good before running a real upload? `./run.sh --whoami` hits `/api/me` and prints your username + node counts.

---

## Running on a schedule

If you keep a refreshed MeshMapper export at a known path (e.g. a nightly RX-log copy), let Heimdall install a daily timer that uploads it for you:

```bash
# Interactive — picks the right mechanism for your OS (systemd / cron / schtasks)
./run.sh --schedule --schedule-csv /data/mesh/nightly.csv

# Default time is 03:00 local; override with --schedule-time HH:MM
./run.sh --schedule --schedule-csv /data/mesh/nightly.csv --schedule-time 04:30

# First install dry-run — parses + signs but never POSTs. Re-run without
# --schedule-dry-run to go live once you trust the daily cycle.
./run.sh --schedule --schedule-csv /data/mesh/nightly.csv --schedule-dry-run
```

Mechanism per OS:

| OS | Mechanism | Where it lives |
|---|---|---|
| Linux with systemd | user systemd timer | `~/.config/systemd/user/heimdall.service` + `.timer` |
| Linux without systemd, macOS | user crontab | `crontab -l` |
| Windows | scheduled task | `schtasks /Query /TN Heimdall` |

Every artifact carries a `# managed-by-heimdall` marker comment so the uninstaller can find and remove it without touching the rest of your crontab / systemd unit dir / task scheduler.

To remove every Heimdall-managed scheduled task on the host:

```bash
./run.sh --unschedule
```

The API key is **never** baked into the unit file / cron line / schtasks action — the saved-on-disk key file is read at run-time instead. Inspecting the installed entry (`systemctl --user cat heimdall.service` or `crontab -l` or `schtasks /Query /TN Heimdall /V`) will never expose your credential.

---

## Updating

```bash
./run.sh --update
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
| **MeshMapper flat "Copy CSV"** | Single header row `timestamp,repeater_id,snr,rssi,...` | MeshMapper app, RX log export |
| **MeshMapper multi-section CSV** | `--- TX/RX/DISC Log ---` marker blocks, each with its own header | MeshMapper full log export |
| **MeshCore offline ping-log JSON** | `.json` with a top-level `pings[]` array (`DISC` / `RX` pings) | meshcore-ha / MeshCore offline capture |
| _Meshcore Companion serial dump_ | _Planned_ | T-Beam / Heltec / Wio Tracker via USB serial |
| _Raw MQTT capture_ | _Planned_ | `mosquitto_sub` against a Meshcore broker |
| _Cardputer ADV LoRa cap log_ | _Planned_ | M5Stack Cardputer Advanced with LoRa module |

Format is auto-detected (by extension, then by content sniff). One `DISC`/`RX`/`TX` observation becomes one node record. **Note:** the CSV `TX`/`RX`/`DISC` sections log SNR and the receiver's noise floor but no per-node RSSI, so those records carry `rssi: null`; the offline-JSON `DISC` pings include real `local_rssi`. See [`examples/`](examples/) for a scrubbed sample of each format.

Italicised rows are not yet implemented — they are on the roadmap once sample data lands. **Have a real capture you can share? See the pinned ["Wanted: real-world Meshcore capture samples"](https://github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars/issues/1) issue for what we're looking for and how to scrub before sending.**

---

## All command-line flags

```
./run.sh [csv] [options]
```

| Flag | Purpose | Default |
|---|---|---|
| `csv` (positional) | Path to the MeshMapper CSV export. Optional with `--setup`, `--save-key`, `--whoami`, `--update`, `--schedule`, `--unschedule`. | (none) |
| `--setup` | Interactive first-time setup. Prompts for your WDGoWars API key, validates it against `/api/me`, and saves it to your user config dir. | off |
| `--save-key KEY` | Non-interactive: save the given API key to the user config dir. Prefer `--setup` for first-time install. | off |
| `--whoami` | Validate your stored API key by hitting `/api/me` and printing username + node counts. | off |
| `--key KEY` | WDGoWars API key. Overrides the `WDGWARS_API_KEY` env var and the saved key. Matches Muninn + wigle-to-wdgwars. | env / saved |
| `--preview` | Parse the file, print the first six normalised rows as JSON, then exit. No envelope build, no upload. | off |
| `--dry-run` | Build the full HMAC-signed request envelope (same bytes the live upload would send), print a short summary per chunk, but do **not** POST. | off |
| `--api-url URL` | Override the WDGoWars upload URL. Matches Muninn. | `https://wdgwars.pl/api/upload/` |
| `--schedule` | Install a daily scheduled upload. Pairs with `--schedule-csv PATH`. | off |
| `--unschedule` | Remove every Heimdall-managed scheduled task on this host. | off |
| `--schedule-csv PATH` | CSV file to upload daily. **Required** with `--schedule`. | (none) |
| `--schedule-time HH:MM` | 24-hour daily run time for `--schedule`. | `03:00` |
| `--schedule-dry-run` | Install the schedule with `--dry-run` baked in. | off |
| `--update` | Self-update via `git pull` (clone) or raw-GitHub fetch (ZIP install). | off |
| `--no-version-check` | Skip the daily GitHub release check for this run. | off |
| `-q`, `--quiet` | Suppress informational banners. Errors still print. | off |
| `--version` | Print version and exit. | (none) |
| `-h`, `--help` | Print help and exit. | (none) |

**Deprecated aliases:** `--api-key` (use `--key`) and `--endpoint` (use `--api-url`) still work for now but will be removed in `v0.4`. Both emit a one-line deprecation note on stderr when used.

### Examples

```bash
# One-time setup (saves your API key)
./run.sh --setup

# Sanity-check a fresh export
./run.sh my-capture.csv --preview

# Build envelope but don't POST (verify HMAC + payload shape)
./run.sh my-capture.csv --dry-run

# Real upload, key already saved by --setup
./run.sh my-capture.csv

# Real upload, key on command line
./run.sh my-capture.csv --key YOUR_KEY

# Real upload, key in env (keeps the key out of shell history)
export WDGWARS_API_KEY=YOUR_KEY
./run.sh my-capture.csv

# Confirm the saved key is valid
./run.sh --whoami

# Self-update to latest
./run.sh --update

# Point at a self-hosted proxy (see web/serve.py)
./run.sh my-capture.csv \
  --api-url http://127.0.0.1:8765/api/upload/
```

Records batch in chunks of **1000** per request.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'gungnir'` | Ran `python3 heimdall.py ...` directly instead of `./run.sh` — system Python doesn't see the venv. | Use `./run.sh ...` (it picks `.venv/bin/python` automatically) or activate the venv yourself: `source .venv/bin/activate`. |
| `[heimdall] note: --api-key is deprecated, use --key.` | You're on a fresh `v0.3.0+` with old shell history / docs. | Rename the flag — both still work today, but `--api-key` disappears in `v0.4`. |
| `--schedule needs --schedule-csv PATH` | The scheduler needs a fixed CSV file path to upload daily. | Pick a path you keep refreshing (your nightly MeshMapper export) and pass it: `./run.sh --schedule --schedule-csv /path/to/file.csv`. |
| `--schedule needs a saved WDGoWars API key` | The installed timer reads the saved key at run-time; you haven't saved one yet. | Run `./run.sh --setup` first, then re-run `--schedule`. |
| Daily upload runs but nothing appears on WDGoWars | The schedule was installed with `--schedule-dry-run`. | Re-run `--schedule` without `--schedule-dry-run` to go live. |
| Want to inspect the installed daily job | Per-OS check: | `systemctl --user cat heimdall.service` (systemd) / `crontab -l` (cron) / `schtasks /Query /TN Heimdall /V` (Windows). |
| `schtasks: Value for '/TR' option cannot be more than 261 character(s)` | Your install path + CSV path exceed the schtasks /TR limit. | Move the install to a shorter path (e.g. `C:\heimdall\` instead of nested user paths). |
| Dry-run says HEALTHY but real upload returns `401` | Saved key was rotated or revoked. | Re-run `./run.sh --setup` to save the current key, then `./run.sh --whoami` to verify. |

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

The target per-record schema is `node_id, node_type, name, lat, lon, rssi, snr, first_seen, type`. `type` is a constant (`"MESHCORE"`) marking the record as part of this envelope family; the node's own role (repeater/client/...) goes in `node_type`. Field aliases for MeshMapper inputs are in `_normalise_meshmapper_row`.

---

## Privacy & data flow

- Capture files **never leave your machine** until you explicitly run an upload command without `--dry-run`. Parsing, normalising, and envelope-building all happen locally.
- The API key is read from `--api-key`, then `$WDGWARS_API_KEY`, then the saved key file. When `--setup` (or `--save-key`) writes the file, it's `chmod 0600` on Unix and lives under the per-user `%APPDATA%` on Windows.
- The bundled `examples/sample.csv` is a **scrubbed** export with `lat=0, lon=0` for every row, so it cannot accidentally produce a real upload (the upstream ingest rejects `0,0` GPS).
- The daily version check hits `https://api.github.com/repos/Yggdrasil-AI-labs/meshcore-to-wdgwars/releases/latest` with a `heimdall/<version>` User-Agent, caches the answer for 24h, and sends nothing about you or your data. Disable per-run with `--no-version-check`, or silence globally with `--quiet`.
- No telemetry, no analytics. The only outbound traffic is to the WDGoWars upload endpoint (when you explicitly invoke an upload) and the GitHub release check (cached daily, opt-out via `--no-version-check`).

---

## Credits

- **Muninn** ([adsb-to-wdgwars](https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars)) — parent pattern. HMAC envelope, three-deploy-mode design, Pyodide web flavour all originate there.
- **FusedStamen** — surfaced the WDGoWars mesh ingest target schema and suggested the MeshMapper CSV bridge angle.
- **Wild!Radio** — supplied the MeshMapper RX-log sample used to wire the field map.
- **[@nicolasrata](https://github.com/nicolasrata)** — contributed the first real-world baseline ([issue #1](https://github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars/issues/1)): a multi-section MeshMapper export and a MeshCore offline ping-log JSON, which is what the v0.4.0 section-aware CSV and JSON parsers were built and tested against.
- **MeshMapper** ([wiki](https://wiki.meshmapper.net/)) — upstream Meshcore visualisation platform whose CSV export is Heimdall's first supported input.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Related

- [adsb-to-wdgwars](https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars) — Muninn, the aircraft sibling.
- [WDGoWars](https://wdgwars.pl) — the wardriving game these tools feed.
