# Changelog

All notable changes to Heimdall are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.2.0] — extract transport to gungnir

Structural refactor. Wire-protocol unchanged. Heimdall becomes a thin
MeshMapper-specific layer (CSV parse + record normalization) over the
shared [gungnir](https://github.com/HiroAlleyCat/gungnir) transport
library — the same library Muninn v2.0 sits on. A fix in gungnir lands
in both feeders without per-tool work.

### Changed

- **`upload()` return type:** v0.1.x returned `list[tuple[int, str]]`
  (per-chunk status + body). v0.2 returns a shell exit code (0 ok,
  1 fail) — matches the gungnir convention and what cron jobs expect.
  Existing callers should switch to checking the int return.
- **`BATCH_SIZE` default lowered from 1000 to 500** per wdgwars.pl's
  preferred 100-500 per request range.
- **Removed `chunked()` helper** — gungnir handles chunking.

### Added

- New `--whoami` flag — validates the API key against `/api/me`
  without parsing a CSV. Mirrors Muninn's same-named flag.
- New `--batch-size` flag — defaults to 500, override for testing.

### Improved (free wins from gungnir 0.1.x)

- **Retry 5xx + network errors** with exponential backoff (3 attempts).
- **429 stops the whole batch** and persists a cooldown the next cron
  tick respects.
- **Silent-drop pattern** (HTTP 200 ok:true with every counter zero)
  returns rc=1. Heimdall previously had no detection at all.
- **1s inter-chunk cooldown** (was: back-to-back POSTs).
- **User-Agent** now `heimdall/0.2.0 (+https://github.com/HiroAlleyCat/meshcore-to-wdgwars)`
  per RFC bot-UA convention.
- **API-key redaction** in log lines for any non-empty match.

### Removed

- `build_envelope()` body — kept as a thin shim over gungnir for
  backward compat with tests and any external scripts that imported it.
- `chunked()` — gungnir's `send()` chunks internally.
- `web/heimdall.py` — was a byte-identical copy of the root
  `heimdall.py` with no callers (verified `web/serve.py` does not
  import it). Removed to eliminate drift risk.

### Migration

- `pip install -r requirements.txt` — pulls gungnir from the pinned
  GitHub tag.
- If you called `upload()` directly from a script, update to check the
  int return instead of the per-chunk tuple list.
- No config-file changes. API key resolution unchanged.

## [0.1.0] — initial alpha

### Added
- **CLI** (`heimdall.py`) that parses a MeshMapper "Logs → Copy CSV"
  export, normalises each row to the WDGoWars meshcore schema
  (`timestamp,node_id,type,name,lat,lon,rssi,snr`), and uploads via the
  same HMAC envelope and `/api/upload/` endpoint Muninn uses for
  aircraft.
- **Three modes:** `--preview` (print the first six normalised rows),
  `--dry-run` (build the signed envelope without POSTing), default
  (real upload, in batches of 1000).
- **API key resolution:** `--api-key` flag or `$WDGWARS_API_KEY` env var.
- **Scrubbed sample** at `examples/sample.csv` so the parser can be
  exercised without exposing real GPS data. All `lat,lon` zeroed; the
  upstream ingest rejects `0,0` so the file cannot accidentally cause
  a real upload.

### Known limitations
- No web / Pyodide frontend yet. Coming in v0.2.0.
- Only one input format supported (MeshMapper CSV). Meshcore Companion
  serial, MQTT, and Cardputer ADV log support are planned once sample
  data lands.
- The `type` field defaults to `"repeater"` and the `name` field
  defaults to empty string. These are best guesses pending confirmation
  with the WDGoWars maintainers; revisit once we have a verdict.
- No `--save-key` persistence yet. Pass the key every invocation or use
  the env var.
- No version-check, no telemetry, no analytics. May add an opt-in
  update check later, mirroring Muninn's daily HEAD-to-releases pattern.
