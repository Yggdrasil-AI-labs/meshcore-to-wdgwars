<p align="center">
  <img src="assets/banner.png" alt="Heimdall. Odin's watcher for the WDGWars mesh" width="100%"/>
</p>

<p align="center">
  <a href="https://github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars/actions/workflows/ci-quality-gates.yml"><img alt="CI" src="https://github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars/actions/workflows/ci-quality-gates.yml/badge.svg"></a>
  <a href="https://sonarcloud.io/dashboard?id=Yggdrasil-AI-labs_meshcore-to-wdgwars"><img alt="Quality gate" src="https://sonarcloud.io/api/project_badges/measure?project=Yggdrasil-AI-labs_meshcore-to-wdgwars&metric=alert_status"></a>
  <a href="https://github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/Yggdrasil-AI-labs/meshcore-to-wdgwars?color=b08850&label=release"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-b08850.svg"></a>
  <a href="https://github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars/blob/main/SECURITY.md"><img alt="Security" src="https://img.shields.io/badge/security-threat%20model-b08850.svg"></a>
</p>

# Heimdall

Convert **MeshMapper** "Logs → Copy CSV" exports (and other Meshcore LoRa capture formats, over time) to WDGWars-compatible JSON and optionally upload them. Sibling tool to [adsb-to-wdgwars](https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars) (Muninn); same HMAC envelope, same `/endpoint/upload/` endpoint, different payload slot. Muninn fills `aircraft`; Heimdall fills `meshcore_nodes`.

**Scope:** Heimdall is for **Meshcore LoRa observations from your own captures**. WDGWars' mesh slot itself now takes both Meshcore and Meshtastic, told apart by an explicit `network` field rather than by guessing from role-name casing (LOCOSP, 2026-08-12). Heimdall's own parsers only read Meshcore capture formats, so what belongs in *this* tool is a Meshcore capture; Meshtastic support would be a separate feeder. If your data came from a real Meshcore receiver (MeshMapper app, T-Beam running Meshcore Companion, Cardputer ADV + LoRa cap, Heltec V3, etc.), you're in the right place. A sighting that hopped through other repeaters is still worth sending, tell WDGWars the hop count if your capture has one and it will never be rejected for being hopped, it will just be trusted less for that node's position than a sighting that arrived direct.

## Family

Sibling repos in the WDGWars feeder family:

- [Muninn](https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars). ADS-B feeder
- [wigle-to-wdgwars](https://github.com/Yggdrasil-AI-labs/wigle-to-wdgwars). WiGLE Wi-Fi/BLE feeder
- [gungnir](https://github.com/Yggdrasil-AI-labs/gungnir), shared HMAC transport library
- [wdgwars-api-tester](https://github.com/Yggdrasil-AI-labs/wdgwars-api-tester). API surface probe

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

The web version lives at [yggdrasil-ai-labs.github.io/meshcore-to-wdgwars](https://yggdrasil-ai-labs.github.io/meshcore-to-wdgwars/).

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

Generate a key just for Heimdall and give it a name, rather than reusing one you handed another tool. Keys are switched off one at a time from the same profile page, so revoking this one later costs you nothing else.

By configuring a key you're authorising Heimdall to upload the captures you give it to WDGWars under your own account. It won't ask again per upload. Use `--preview` or `--dry-run` to see exactly what would be sent before you commit to it.

Walks you through pasting your WDGWars API key, validates it against `/endpoint/me`, and saves it to:

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

## Uploading to WDGWars

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

Want to confirm your saved key is good before running a real upload? `./run.sh --whoami` hits `/endpoint/me` and prints your username + node counts.

---

## Running on a schedule

If you keep a refreshed MeshMapper export at a known path (e.g. a nightly RX-log copy), let Heimdall install a daily timer that uploads it for you:

```bash
# Interactive - picks the right mechanism for your OS (systemd / cron / schtasks)
./run.sh --schedule --schedule-csv /data/mesh/nightly.csv

# Default time is 03:00 local; override with --schedule-time HH:MM
./run.sh --schedule --schedule-csv /data/mesh/nightly.csv --schedule-time 04:30

# First install dry-run - parses + signs but never POSTs. Re-run without
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

The API key is **never** baked into the unit file / cron line / schtasks action. The saved-on-disk key file is read at run-time instead. Inspecting the installed entry (`systemctl --user cat heimdall.service` or `crontab -l` or `schtasks /Query /TN Heimdall /V`) will never expose your credential.

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

Heimdall never phones home on its own. If you want to know whether a newer release exists, run `--check-version` and it asks GitHub once and tells you. Nothing else on an ordinary run contacts anybody except the WDGWars upload endpoint, and only when you invoke an upload.

---

## Supported input formats

| Format | Detection | Source |
|---|---|---|
| **MeshMapper flat "Copy CSV"** | Single header row `timestamp,repeater_id,snr,rssi,...` | MeshMapper app, RX log export |
| **MeshMapper multi-section CSV** | `--- TX/RX/DISC Log ---` marker blocks, each with its own header | MeshMapper full log export |
| **MeshCore offline ping-log JSON** | `.json` with a top-level `pings[]` array (`DISC` / `RX` / any key-bearing ping) | meshcore-ha / MeshCore offline capture |
| **MeshCore app database (SQLite)** | `SQLite format 3` file magic, any filename, with a `discovered_contacts` or `contacts` table | A database file exported out of the MeshCore app (see the note below) |
| _Meshcore Companion serial dump_ | _Planned_ | T-Beam / Heltec / Wio Tracker via USB serial |
| _Raw MQTT capture_ | _Planned_ | `mosquitto_sub` against a Meshcore broker |
| _Cardputer ADV LoRa cap log_ | _Planned_ | M5Stack Cardputer Advanced with LoRa module |

Format is auto-detected (SQLite magic first, then extension, then a content sniff). One `DISC`/`RX`/`TX` observation becomes one node record. **Note:** the CSV `TX`/`RX`/`DISC` sections log SNR and the receiver's noise floor but no per-node RSSI, so those records carry `rssi: null`; the offline-JSON `DISC` pings include real `local_rssi`. See [`examples/`](examples/) for a scrubbed sample of each format.

**If you have the app database, use it.** It is a strict superset of the app's own JSON export, and a large one: checked against a real capture, everything in the export was also in the database, while a large majority of the database's nodes carrying both a fix and a key never reached the export at all. It carries a full public key on every node, which is what gets you a server-legal 16-hex `node_id`, and a `last_advert` timestamp so `--since-days N` can hold back a stale back catalogue instead of you trimming the file by hand.

**Getting the file is a step you do in the app, not something Heimdall does.** Heimdall has no device path at all: no USB, no serial, no Bluetooth, no adb. It only ever opens a database file that is already sitting on the machine you run it on, the same as it opens a CSV. Exporting or sharing that file out of the MeshCore app is an app-side step, and the exact menu path is **not documented here because it has not been verified** rather than because it is obvious. What is known is that the app can produce one, since this parser was built against a file an operator exported that way. If you work out the current steps on your build, an issue or PR adding them would be welcome.

Note this also means you are normally pointing Heimdall at a *copy*, not at the app's live store, which on Android is not reachable without root. The database is still opened read-only through a `file:...?mode=ro` URI, so nothing Heimdall does can write to, journal, or schema-upgrade whichever file you hand it.

Two things the database does not have. There is no RSSI or SNR anywhere in it, since it records that a node was heard and where it claimed to be, not how strongly, so those records carry `rssi: null`. And its `type` column is an integer rather than a role name (`1` companion, `2` repeater, `3` room server, `4` sensor), so this is the one input where the role is translated rather than passed through verbatim: there is no name in there to pass through. An unrecognised type integer drops the row rather than defaulting it, so a role this tool has not confirmed never gets uploaded under a guessed label.

### Node IDs

MeshCore names a node on the air by the leading bytes of its public key, so the ID a capture prints (`0CE8`) is a 2-6 hex prefix of a 64-hex key. wdgwars.pl's ingest wants 8-16 lowercase hex, which is why early uploads came back `bad_node_id`.

Since v0.5.0, when a capture logs a node's full `public_key`, Heimdall takes the `node_id` from the **first 16 hex chars (8 bytes) of that key** and keeps the short ID as the record's `name`. Same node, more digits of the same number. 8 bytes is the canonical form confirmed by wdgwars.pl (2026-08-10): shorter prefixes collide in the live corpus, and a node_id collision there means two repeaters overwriting each other's position.

The full key rides along as an optional `public_key` field, which the server verifies (64 hex, `node_id` must be its prefix) but never requires. Heimdall only sends a key it can tie to that node, and omits the field entirely rather than sending null.

Two guard rails:

- The key is only used when it actually starts with the short ID the capture heard, so a mispaired key never renames a node into someone else's identity.
- Sightings that name a node by short ID alone (offline-JSON `RX` tokens) resolve against the keys found elsewhere in the *same* capture, and only when exactly one node matches that prefix.

A MeshMapper CSV export carries no keys at all, so its nodes keep their short IDs and Heimdall warns that the server will reject them. This approach was contributed by [@nicolasrata](https://github.com/nicolasrata) in [issue #1](https://github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars/issues/1).

---

Italicised rows are not yet implemented. They are on the roadmap once sample data lands. **Have a real capture you can share? See the pinned ["Wanted: real-world Meshcore capture samples"](https://github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars/issues/1) issue for what we're looking for and how to scrub before sending.**

---

## All command-line flags

```
./run.sh [csv] [options]
```

| Flag | Purpose | Default |
|---|---|---|
| `csv` (positional) | Path to the MeshMapper CSV export. Optional with `--setup`, `--save-key`, `--whoami`, `--update`, `--schedule`, `--unschedule`. | (none) |
| `--setup` | Interactive first-time setup. Prompts for your WDGWars API key, validates it against `/endpoint/me`, and saves it to your user config dir. | off |
| `--save-key KEY` | Non-interactive: save the given API key to the user config dir. Prefer `--setup` for first-time install. | off |
| `--whoami` | Validate your stored API key by hitting `/endpoint/me` and printing username + node counts. | off |
| `--key KEY` | WDGWars API key. Overrides the `WDGWARS_API_KEY` env var and the saved key. Matches Muninn + wigle-to-wdgwars. | env / saved |
| `--preview` | Parse the file, print the first six normalised rows as JSON, then exit. No envelope build, no upload. | off |
| `--since-days N` | MeshCore app database only: skip nodes not heard in the last N days. The database is all-time, so without this an upload carries the whole back catalogue. Ignored (with a note) for other formats. | (all) |
| `--dry-run` | Build the full HMAC-signed request envelope (same bytes the live upload would send), print a short summary per chunk, but do **not** POST. | off |
| `--api-url URL` | Override the WDGWars upload URL. Matches Muninn. | `https://wdgwars.pl/endpoint/upload/` |
| `--schedule` | Install a daily scheduled upload. Pairs with `--schedule-csv PATH`. | off |
| `--unschedule` | Remove every Heimdall-managed scheduled task on this host. | off |
| `--schedule-csv PATH` | CSV file to upload daily. **Required** with `--schedule`. | (none) |
| `--schedule-time HH:MM` | 24-hour daily run time for `--schedule`. | `03:00` |
| `--schedule-dry-run` | Install the schedule with `--dry-run` baked in. | off |
| `--update` | Self-update via `git pull` (clone) or raw-GitHub fetch (ZIP install). | off |
| `--check-version` | Ask GitHub whether a newer release exists, then exit. The only thing here that contacts GitHub by itself. | off |
| `-q`, `--quiet` | Suppress informational banners. Errors still print. | off |
| `--version` | Print version and exit. | (none) |
| `-h`, `--help` | Print help and exit. | (none) |

**Deprecated aliases:** `--api-key` (use `--key`) and `--endpoint` (use `--api-url`) still work for now but will be removed in a future release. Both emit a one-line deprecation note on stderr when used.

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

# Upload from the MeshCore app database (any filename; detected by file magic)
./run.sh meshcore.db

# Same, but only nodes heard in the last 20 days
./run.sh meshcore.db --since-days 20

# Confirm the saved key is valid
./run.sh --whoami

# Self-update to latest
./run.sh --update

# Point at a self-hosted proxy (see web/serve.py)
./run.sh my-capture.csv \
  --api-url http://127.0.0.1:8765/endpoint/upload/
```

Records batch in chunks of **1000** per request.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `[heimdall] note: --api-key is deprecated, use --key.` | You're on a fresh `v0.3.0+` with old shell history / docs. | Rename the flag, both still work today, but `--api-key` will be dropped in a future release. |
| `--schedule needs --schedule-csv PATH` | The scheduler needs a fixed CSV file path to upload daily. | Pick a path you keep refreshing (your nightly MeshMapper export) and pass it: `./run.sh --schedule --schedule-csv /path/to/file.csv`. |
| `--schedule needs a saved WDGWars API key` | The installed timer reads the saved key at run-time; you haven't saved one yet. | Run `./run.sh --setup` first, then re-run `--schedule`. |
| Daily upload runs but nothing appears on WDGWars | The schedule was installed with `--schedule-dry-run`. | Re-run `--schedule` without `--schedule-dry-run` to go live. |
| Want to inspect the installed daily job | Per-OS check: | `systemctl --user cat heimdall.service` (systemd) / `crontab -l` (cron) / `schtasks /Query /TN Heimdall /V` (Windows). |
| `schtasks: Value for '/TR' option cannot be more than 261 character(s)` | Your install path + CSV path exceed the schtasks /TR limit. | Move the install to a shorter path (e.g. `C:\heimdall\` instead of nested user paths). |
| Dry-run says HEALTHY but real upload returns `401` | Saved key was rotated or revoked. | Re-run `./run.sh --setup` to save the current key, then `./run.sh --whoami` to verify. |
| `N rejected: {'bad_node_id': N}` | wdgwars.pl wants a `node_id` of 8-16 lowercase hex, and the short ID a capture prints is only the leading 1-3 bytes of the node's public key. | Fixed in v0.5.0 for captures that log the full key, which is where the `node_id` now comes from (see [Node IDs](#node-ids)). A MeshMapper CSV export carries no keys, so those nodes still miss the floor; prefer MeshCore's offline ping-log JSON export where you have the choice. Heimdall warns before uploading either way. |
| `N rejected: {'bad_public_key': N}` or `{'key_prefix_mismatch': N}` | The optional `public_key` field must be 64 hex with `node_id` as its prefix. | Heimdall never sends a key that fails either check, so this points at a modified parser or a hand-built payload. |
| `N rejected: {'no_gps': N}` | Records with lat/lon `0,0` are dropped by the server (it needs a map position). MeshMapper writes `0.0,0.0` when it has no GPS fix. | Capture with GPS enabled / a fix acquired. v0.4.7+ warns about this before uploading. |
| Upload says `accepted ... 0 new` and rejected counts vanish on a re-run | The server's counters don't itemise every dropped record; observed live on a repeat submission of an already-rejected payload. Heimdall keeps no state between runs. | v0.4.7+ prints how many submitted nodes got no verdict so the silence is visible. |

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
# POST → https://wdgwars.pl/endpoint/upload/ with X-API-Key: <key>
```

The target per-record schema is `node_id, node_type, name, lat, lon, rssi, first_seen, type, network`. `type` is a constant (`"MESHCORE"`) marking the record as part of this envelope family; `network` is a constant `"meshcore"`, the authoritative field WDGWars uses (since LOCOSP's 2026-08-12 contract) to tell Meshcore and Meshtastic apart, rather than inferring it from role-name casing. The node's own role (repeater/client/...) goes in `node_type`, sent exactly as the capture gave it, WDGWars keeps it verbatim and maps it onto its own internal set rather than asking feeders to translate. `name` carries the short on-air ID (`0CE8`) once `node_id` is derived from the node's public key, and otherwise falls back to `node_id` itself, since no MeshMapper format ever gives a node a real name. Three optional fields are added only when the capture actually supplied them, and omitted rather than sent as null otherwise: `public_key` (the node's full key), and `path_hops` / `path_length` (how many repeaters a sighting passed through). A hopped sighting is never rejected for being hopped, it can just never move a node's position ahead of one that arrived at least as directly. Field aliases for MeshMapper inputs are in `_normalise_meshmapper_row`; `node_id` derivation is in `derive_node_id`.

---

## Privacy & data flow

- Capture files **never leave your machine** until you explicitly run an upload command without `--dry-run`. Parsing, normalising, and envelope-building all happen locally.
- The API key is read from `--key` (or the deprecated `--api-key` alias), then `$WDGWARS_API_KEY`, then the saved key file. When `--setup` (or `--save-key`) writes the file, it's `chmod 0600` on Unix and lives under the per-user `%APPDATA%` on Windows.
- The bundled `examples/sample.csv` is a **scrubbed** export with `lat=0, lon=0` for every row, so it cannot accidentally produce a real upload (the upstream ingest rejects `0,0` GPS).
- `--check-version` hits `https://api.github.com/repos/Yggdrasil-AI-labs/meshcore-to-wdgwars/releases/latest` with a `heimdall/<version>` User-Agent. It sends nothing about you or your captures, but GitHub is a third party and the request itself shows them four things: your source IP, the version you're on, which tool you're running, and when you ran it. That's why it's a command you type. Earlier releases ran this once a day on almost every invocation with `--no-version-check` as the opt-out. That default is gone; the flag is still accepted and ignored so existing scheduled runs don't break.
- No telemetry, no analytics, nothing on a timer. On an ordinary run the only outbound traffic is to the WDGWars upload endpoint, and only when you explicitly invoke an upload.

---

## Credits

- **Muninn** ([adsb-to-wdgwars](https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars)), parent pattern. HMAC envelope, three-deploy-mode design, Pyodide web flavour all originate there.
- **FusedStamen**: surfaced the WDGWars mesh ingest target schema and suggested the MeshMapper CSV bridge angle.
- **Wild!Radio**: supplied the MeshMapper RX-log sample used to wire the field map.
- **[@nicolasrata](https://github.com/nicolasrata)**: contributed the first real-world baseline ([issue #1](https://github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars/issues/1)): a multi-section MeshMapper export and a MeshCore offline ping-log JSON, which is what the v0.4.0 section-aware CSV and JSON parsers were built and tested against.
- **MeshMapper** ([wiki](https://wiki.meshmapper.net/)), upstream Meshcore visualisation platform whose CSV export is Heimdall's first supported input.

---

## License

MIT, see [LICENSE](LICENSE).

---

## Related

- [adsb-to-wdgwars](https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars). Muninn, the aircraft sibling.
- [WDGWars](https://wdgwars.pl), the wardriving game these tools feed.
