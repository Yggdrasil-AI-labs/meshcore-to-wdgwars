# Examples

## `sample.csv`

A representative MeshMapper "Logs → Copy CSV" export, scrubbed: all
`latitude` / `longitude` values replaced with `0.0, 0.0` to remove
receiver-location PII. Timestamps, node IDs, RSSI / SNR, and path
data are retained because they don't pin a receiver's physical
location (node IDs are public on the Meshcore mesh, anyone in range
sees the same IDs).

This file demonstrates the parser's field mapping and the envelope
build. It is intentionally **not** valid live data. WDGWars's
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
`public_key` values are retained, they're public mesh node IDs. `DISC` pings
carry real `local_rssi` + `local_snr`; `RX` pings carry a `heard_repeats` SNR
token. This is the richest of the three fixtures and the only one with a real
RSSI.

Since v0.5.0 it is also the fixture that exercises `node_id` derivation: the
per-ping `public_key` is where the 16-hex `node_id` comes from, so 15 of the 16
unique nodes in this file clear wdgwars.pl's `node_id` gate and carry a verified
`public_key` on the wire (they still bounce as `no_gps`, by design for a
scrubbed fixture). The sixteenth is an `RX` token for a node this capture never
logged a key for.

## `meshcore-app.db`

A **synthetic** MeshCore app database, matching the real app's schema
(`discovered_contacts` + `contacts`, same column set) but containing no real
node. Every other fixture here is a real capture with coordinates zeroed;
this one could not be, for two reasons. The database parser drops rows with
no GPS fix rather than passing them through, so a zeroed copy of a real
database would parse to zero records and demonstrate nothing. And a real
database carries other operators' node positions in a column the parser
actually reads, rather than in one it ignores.

So the coordinates are repeated digits (`1.111111`, `2.222222`, ...) that are
obviously not a place, and the public keys are repeated bytes. Nine rows in,
seven records out. Each row exists to exercise one documented behaviour:

| Row | What it demonstrates |
|---|---|
| Example Repeater A | 1 byte per hop: `advert_path_len` 5, blob 5 bytes, 5 hops |
| Example Repeater B | 2 bytes per hop: `advert_path_len` 70 (`0x46`), blob 12 bytes, 6 hops |
| Example Companion | 3 bytes per hop: `advert_path_len` 134 (`0x86`), blob 18 bytes, 6 hops |
| Example Room Server | `type` 3, and a row with no `advert_path` at all (no hop count) |
| Example Sensor | `type` 4, the role that never appeared in the reference dump |
| Example No Fix | **Dropped.** Both coordinates zero; the server gates on a real fix |
| Example Bad Clock | **Dropped.** `last_advert` in 2083, dropped rather than clamped |
| Example Bad Path | Uploads with **no** hop count: `advert_path_len` 70 against a 7-byte blob fails the `hops * bytes_per_hop` identity, so no count is claimed |
| Example Both Tables | Present in both tables; the union keeps the newer `last_advert`, and the `custom_name` on the `contacts` row must never reach the wire |

Unlike the other fixtures, these records **clear** the server's gates
(`predict_server_rejects` returns nothing), because the fix is non-zero and
the 16-hex `node_id` derives from a full key. Do not upload it: it would put
seven fictional nodes on the live map. Use `--preview` or `--dry-run`.

```bash
python3 heimdall.py examples/meshcore-app.db --preview
```

Timestamps are fixed rather than generated relative to now, so the file is
byte-stable if regenerated. That does mean `--since-days` will eventually
filter the whole fixture out as it ages, which is correct behaviour rather
than a broken fixture.

Do **not** commit a real MeshCore app database. It holds every node the
device ever heard, with coordinates, and unlike the CSV fixtures there is no
zeroing pass that leaves it still useful.

## MeshMapper debug log: no sample shipped

There is deliberately no `meshmapper-debug-*.txt` here. A real one cannot be
scrubbed the way the CSVs are: it carries the phone's own position at every
connect, the app's session credentials, and decrypted channel messages, and
the nodes in it parse only *because* their advertised positions are real, so
zeroing those leaves nothing that parses. A synthetic one with made-up keys
and positions would parse, and uploading it would put fictional nodes on the
live map. The format is covered by synthetic packets in
`tests/test_heimdall.py` (`MeshmapperDebugLogTests`) instead.
