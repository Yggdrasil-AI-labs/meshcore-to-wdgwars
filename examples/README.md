# Examples

## `sample.csv`

A representative MeshMapper "Logs → Copy CSV" export, scrubbed: all
`latitude` / `longitude` values replaced with `0.0, 0.0` to remove
receiver-location PII. Timestamps, node IDs, RSSI / SNR, and path
data are retained because they don't pin a receiver's physical
location (node IDs are public on the Meshcore mesh — anyone in range
sees the same IDs).

This file demonstrates the parser's field mapping and the envelope
build. It is intentionally **not** valid live data — WDGoWars's
ingest path rejects `lat=0, lon=0` as `no_gps`, so a real upload
attempt with this CSV would bounce harmlessly. Safe to commit and
ship as a fixture.

Do **not** commit CSVs containing real GPS history.

## `meshmapper-sections.csv`

A real multi-section MeshMapper export (`--- TX Log ---` + `--- DISC Log ---`
blocks), contributed via [issue #1](https://github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars/issues/1)
and scrubbed to `0.0, 0.0`. This is the layout `sample.csv` only approximated:
each section has its own header, and the heard nodes are packed into a trailing
`events` / `nodes` column as `ID(snr)` / `ID(R)(snr)` tokens. Demonstrates the
section-aware parser.

## `offline-pings.json`

A real MeshCore "offline" ping-log JSON, same contribution, scrubbed: lat/lon
zeroed and the contributor's own `device_name` / `device_public_key` replaced
with placeholders (the parser ignores those fields anyway). Per-ping
`public_key` values are retained — they're public mesh node IDs. `DISC` pings
carry real `local_rssi` + `local_snr`; `RX` pings carry a `heard_repeats` SNR
token. This is the richest of the three fixtures and the only one with a real
RSSI.
