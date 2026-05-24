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

Heimdall will ship in **two flavours** that share the same parsing core. Today, only the CLI exists; the web flavour is in progress.

| | **Web (browser)** | **CLI (terminal)** |
|---|---|---|
| **For** | One-off uploads, anyone without Python | Headless boxes, mesh gateways, cron, scripted feeds |
| **Install** | None — open a URL | Clone repo, run `python3 heimdall.py` |
| **Where parsing happens** | In your browser (Pyodide / WASM) | Locally with stdlib Python |
| **Internet required** | Yes (initial page load) | No (only uploads) |
| **Runs without a display** | No | **Yes** — headless-safe |
| **Status** | Planned | **Shipped (alpha)** |

If you're on a Raspberry Pi, a server, or anything without a desktop, **use the CLI**. Scroll down to [CLI install](#cli-install).

The web version will live at [hiroalleycat.github.io/meshcore-to-wdgwars](https://hiroalleycat.github.io/meshcore-to-wdgwars/) once it ships.

---

## CLI install

```bash
git clone https://github.com/HiroAlleyCat/meshcore-to-wdgwars
cd meshcore-to-wdgwars
python3 heimdall.py examples/sample.csv --preview
```

Heimdall is pure stdlib Python — no `pip install` step.

### The day-to-day workflow

1. In MeshMapper (or your Meshcore capture tool), export the RX log to CSV (in MeshMapper that's **Logs → Copy CSV**).
2. Save it as a `.csv` file on disk.
3. Run `python3 heimdall.py path/to/your_export.csv --preview` to see how Heimdall normalises the rows.
4. When the preview looks right, upload — see below.

### Or just preview

```bash
python3 heimdall.py path/to/your_export.csv --preview
```

Prints the first six normalised rows to stdout as JSON, then exits. No upload, no envelope. Useful for sanity-checking a fresh export.

---

## Uploading to WDGoWars

```bash
# Pass the key on the command line
python3 heimdall.py path/to/your_export.csv --api-key YOUR_KEY

# Or set it in the environment
export WDGWARS_API_KEY=YOUR_KEY
python3 heimdall.py path/to/your_export.csv

# Build the envelope but don't POST (verify everything before going live)
python3 heimdall.py path/to/your_export.csv --api-key YOUR_KEY --dry-run
```

`--dry-run` builds the full HMAC-signed request — same signature the live upload would send — but does not POST. Useful for confirming the envelope is well-formed before pointing it at the live API.

Records batch in chunks of **1000** per request.

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
python3 heimdall.py CSV [options]
```

| Flag | Purpose | Default |
|---|---|---|
| `csv` (positional) | Path to the MeshMapper CSV export. Required. | — |
| `--preview` | Parse the file, print the first six normalised rows as JSON, then exit. No envelope build, no upload. Use to sanity-check a fresh export. | off |
| `--dry-run` | Build the full HMAC-signed request envelope (same bytes the live upload would send), print a short summary per chunk, but do **not** POST. Use to verify the envelope before pointing it at the live API. | off |
| `--api-key KEY` | WDGoWars API key. Overrides the `WDGWARS_API_KEY` env var. | env var |
| `--endpoint URL` | Override the WDGoWars upload endpoint. | `https://wdgwars.pl/api/upload/` |
| `-h`, `--help` | Print help and exit. | — |

### Examples

```bash
# Sanity-check a fresh export
python3 heimdall.py my-capture.csv --preview

# Build envelope but don't POST (use to verify HMAC + payload shape)
WDGWARS_API_KEY=YOUR_KEY python3 heimdall.py my-capture.csv --dry-run

# Real upload, key on command line
python3 heimdall.py my-capture.csv --api-key YOUR_KEY

# Real upload, key in env (preferred — keeps the key out of shell history)
export WDGWARS_API_KEY=YOUR_KEY
python3 heimdall.py my-capture.csv

# Point at a self-hosted proxy (see web/serve.py)
python3 heimdall.py my-capture.csv --api-key YOUR_KEY \
  --endpoint http://127.0.0.1:8765/api/upload/
```

Records batch in chunks of **1000** per request, hardcoded in v0.1.0.

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
- The API key is read from `--api-key` or `$WDGWARS_API_KEY`. Heimdall does not persist it to a config file in this initial alpha — that's coming when the CLI grows a `--save-key` flag.
- The bundled `examples/sample.csv` is a **scrubbed** export with `lat=0, lon=0` for every row, so it cannot accidentally produce a real upload (the upstream ingest rejects `0,0` GPS).
- No telemetry, no analytics, no version check (yet). The only outbound traffic is to the WDGoWars upload endpoint, and only when you explicitly invoke an upload.

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
