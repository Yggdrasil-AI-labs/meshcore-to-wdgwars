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
