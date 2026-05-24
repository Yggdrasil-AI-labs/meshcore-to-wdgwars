# Changelog

All notable changes to Heimdall are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
[Semantic Versioning](https://semver.org/).

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
