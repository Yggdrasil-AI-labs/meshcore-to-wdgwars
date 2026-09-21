# Changelog

All notable changes to Heimdall are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.9.1] - 2026-09-21 - Make the hand-porting debt announce itself

### Added

- **`GUNGNIR_RECONCILED_AT` and a drift test.** Heimdall inlines its own
  transport and does not depend on gungnir, so a transport fix landing
  there reaches this file only when a human carries it over. Nothing said
  when that was overdue: `check_deliberate_skip` shipped in gungnir v0.1.4,
  Heimdall never got it, and gungnir's README listed Heimdall as a
  consumer, so the gap looked closed from both sides.

  `tests/test_gungnir_drift.py` now fails once gungnir moves past the
  recorded marker, with instructions to read the changelog and port or
  dismiss each change rather than just raising the number. It is skipped
  where gungnir is not installed, which includes CI. That is the honest
  weakness of it: it only fires on a machine that has the library.

- **The server's deliberate-skip reply is recognised**, ported by hand from
  `gungnir.diagnostics` as part of reconciling against v0.4.1. The server
  answers a payload it has already taken with 200, ok:true, every counter
  zero and an explanation in the clear. Heimdall never had gungnir's bug of
  calling that a failed upload, having no silent-drop detector, but it did
  print its "gave no verdict for the other N nodes" note, which tells the
  operator the server refused to account for their nodes when it had
  accounted for them on an earlier push. It now says what actually
  happened. A test asserts the marker list matches gungnir's, since drift
  there would silently stop the recognition working.

## [0.9.0] - 2026-09-21 - Skip nodes the server already has (optional)

### Added

- **An already-sent gate, active only when gungnir is installed.** A
  capture pushed on a timer carries the same nodes every run, and the
  server counts those as syncs that carried nothing new. Heimdall now
  holds back nodes it has already uploaded, and skips the request entirely
  when every node in the capture is one the server confirmed it holds.

  Hold length follows the server's own answer: a day when an upload
  imported nothing (it already held them all), an hour when it imported
  something, and an hour when the total could not be established. `None`
  is not zero here, deliberately: a total we failed to read must not earn
  a day-long hold on a payload nobody confirmed.

  `--dry-run` neither consults nor records holds, so a dry run keeps
  reporting what *would* be sent. A failed upload records nothing, so it
  retries.

- **`gungnir.holds` is used without taking a dependency on gungnir.** The
  import is lazy, inside the two functions that need it, never at module
  scope, and never in the browser. Three reasons, all load-bearing:

  1. The 2026-06-03 family audit deliberately kept Heimdall's transport
     inlined so this one file ships as both a CLI and a Pyodide page with
     zero runtime dependencies. That decision stands.
  2. gungnir imports `ssl` at module scope and builds an SSL context at
     import time. Pyodide unvendors `ssl`, and v0.8.0 already took the
     live Pages deploy down with an import of that shape.
  3. Anything going wrong with the import leaves the gate off rather than
     failing an upload.

  `holds_available()` reports whether the gate is active, so an operator
  never has to guess.

### Fixed

- **The test suite could write holds into the real config directory.** The
  live-key guard stopped tests posting to the real account; nothing
  stopped them writing state into it. One test's upload recorded its
  fixture node_ids and the next test's upload was then skipped, which read
  as a bug in the code under test and survived between runs. Holds now
  point at a throwaway directory, reset per test.

### Note

- gungnir's own README and package description had claimed Heimdall as a
  consumer since before the 2026-06-03 audit. That was never true and has
  been corrected upstream.

## [0.8.2] - 2026-09-15 - Both calls move to /endpoint/*

Heimdall was the last feeder in the family still calling `/api/*` for
everything: the HMAC upload and the key validation. `/endpoint/*` is the
server-side alias of the same router, same envelope, same response, and it
sits outside the pattern Cloudflare's L7 shield gates during an event, which
is exactly when a feeder still needs both calls to work. What a shield
returns then, a 429 or a challenge, reads to the operator as a bad key rather
than as a platform event.

Both paths were confirmed to answer identically before the switch: `/api/me`
and `/endpoint/me` returned byte-identical bodies for the same key, and GET
on `/api/upload/` and `/endpoint/upload/` returned the same 405 envelope.
`--api-url` still overrides the upload URL, and the web frontend's stored
`heimdall.apiurl` wins over the new default for anyone who set one.

## [0.8.1] - 2026-08-14 - Fix: 0.8.0 killed the web frontend on import

0.8.0 imported `sqlite3` at module top level. Pyodide **unvendors** `sqlite3`
from the standard library, exactly as it does `ssl`, so that one line took the
entire web frontend down at import time before it could parse anything at all,
CSV and JSON included. The live Pages deploy showed
`ModuleNotFoundError: No module named 'sqlite3'` and a version pill reading
`heimdall ?`. The CLI was never affected; CPython vendors `sqlite3`.

Caught by loading the pinned Pyodide 0.26.4 and importing the module, rather
than by assuming a stdlib module is present because it is stdlib.

### Fixed

- `sqlite3` is imported lazily inside the database parser instead of at module
  top level, so a runtime without it loses only this one input format and
  reports a reason, rather than taking down every format on import.
- `web/app.js` loads the `sqlite3` package alongside `ssl`, which is what
  makes the database format work in the browser at all (verified: bare import
  raises, `loadPackage("sqlite3")` then provides SQLite 3.39.0).
- `sqlite3.Error` during a read is surfaced as `ValueError`, so callers need
  one except clause and do not import `sqlite3` just to catch a corrupt file.

### Added

- Three regression guards, each confirmed failing against a deliberately
  broken build: no top-level `sqlite3` import in `heimdall.py`, `web/app.js`
  still loads the package, and a simulated unvendored runtime reports a reason
  while the text parsers keep working.
- `examples/meshcore-app.db`, a synthetic nine-row fixture (see
  `examples/README.md`). Synthetic rather than a scrubbed real database,
  because the parser drops rows with no fix, so a zeroed real database would
  parse to nothing and demonstrate nothing.
- The web frontend accepts `.db` / `.sqlite` / `.sqlite3` and says so.

### Fixed (web preview, pre-existing)

- The results table's `TYPE` column rendered `type`, which has been the
  constant `"MESHCORE"` on every record since v0.4.2, so it showed the same
  word on every row while the node's actual role was never displayed. It now
  shows `node_type` under a `ROLE` header, with a `HOPS` column beside it.

## [0.8.0] - 2026-08-14 - Reads the MeshCore app's own database, three times the nodes

The app's SQLite store, not just the JSON it exports. Checked against a real
capture, everything in the export was also in the database while a large
majority of the database's nodes with a fix and a key never reached the
export, so the export was always a strict subset. A full public key on every row is the part that
matters most: that is what yields a server-legal 16-hex `node_id`.

### Added

- `parse_meshcore_db` reads the MeshCore phone app database. Both node
  tables (`discovered_contacts` and `contacts`, same column set, one saved
  and one everything-ever-heard) are unioned on public key, keeping the
  newer `last_advert` when a node is in both. Opened read-only through a
  `file:...?mode=ro` URI so a live app database is never written to,
  journalled, or silently upgraded by being read.
- Hop counts recovered from the database, which earlier analysis had
  concluded it did not carry. `out_path_len` really is -1 on every row in
  both tables, but `discovered_contacts.advert_path_len` is bit-packed: hop
  count in the low 6 bits, bytes-per-hop minus one in the top two. The
  identity `len(advert_path) == hops * bytes_per_hop` held for every row of
  a real capture with zero violations across all three observed hop widths,
  which is what makes it a decode rather than a guess. Any row failing that
  identity uploads with no hop count rather than an invented one. The large
  majority of records came out with a hop count.
- `--since-days N` gates on `last_advert`. The database is all-time, so the
  previous way to avoid uploading years of history was to trim the file by
  hand before uploading.
- Format dispatch sniffs SQLite's file magic before anything else, so the
  database is recognised under whatever filename it was shared as, and a
  `.db` that is not SQLite still falls through to the text parsers.

### Notes on what the database cannot give

- No RSSI or SNR columns exist anywhere in it. It records that a node was
  heard and where it said it was, not how strongly, so those records carry
  `rssi: null` rather than a zero that would read as a real measurement.
  Signal strength lives in the rx log, which needs a MeshCore frame decoder
  and stays out of scope.
- `type` is an integer, so this is the one input that translates the role
  instead of passing it through verbatim, because there is no name in there
  to pass through (`1` companion, `2` repeater, `3` room server, `4`
  sensor, confirmed from the app by the reference node's operator). An
  unrecognised integer drops the row rather than defaulting it to
  `REPEATER`, which is the failure mode that quietly mislabelled rows in
  0.6.x.
- Rows whose `last_advert` is outside any plausible range are dropped, not
  clamped. A handful of rows in a real capture were bad, the worst reading
  as the year 2083. Since WDGWars settles which sighting owns a node's
  position partly by recency, a year-2083 `first_seen` would outrank every
  genuine sighting of that node indefinitely, and clamping to "now" causes
  the same damage more quietly. Every row that does not make it out is
  accounted for as one of: duplicated across the two tables, no GPS fix, or
  an unusable timestamp.
- `custom_name` is never read. It is the operator's private label for
  someone else's node, not something that node broadcast.

### Compatibility

- Additive. The envelope, HMAC, key handling, scheduler, chunking, and every
  existing parser are untouched, and `network` remains the constant
  `"meshcore"`. `parse_file` takes an optional `since_days` argument that
  defaults to None, so existing callers are unaffected.

## [0.7.0] - 2026-08-12 - Meshcore/Meshtastic told apart by `network`, hop count carried through

LOCOSP shipped a new mesh-slot contract today, in direct response to a
question raised from this project: the slot now takes both Meshcore and
Meshtastic, told apart by an explicit `network` field rather than by
guessing from role-name casing.

### Added

- Every record now carries `network: "meshcore"`. Heimdall's parsers only
  ever read Meshcore capture formats, so this states that positively
  instead of leaving WDGWars to infer it from `node_type`'s casing.
- `path_hops` and `path_length` are carried through from the MeshMapper flat
  "Copy CSV" export when the row has them, omitted entirely (not sent as
  null) when it does not, same pattern as the existing optional
  `public_key` field. WDGWars never rejects a sighting for being hopped;
  it just will not let a hopped sighting move a node's position ahead of
  one that arrived at least as directly.

### Fixed

- An unrecognised MeshMapper node-type marker (anything other than the
  confirmed `R` -> `REPEATER`) was silently coerced to the default
  `REPEATER` instead of being carried through as captured. It now rides
  through verbatim, since WDGWars keeps `node_type` verbatim and maps it
  onto its own internal set rather than asking feeders to translate it.
- `_build_record` no longer force-upper-cases `node_type`. That was itself
  a normalisation of a captured value; the new contract asks for the role
  exactly as captured.

### Compatibility

- Key handling, the HMAC envelope, the scheduler, and the version-check
  behaviour are all untouched.
- No leading `!` stripping was added: none of Heimdall's parsed capture
  formats (MeshMapper CSV, MeshCore offline ping-log JSON) have ever been
  observed to emit an id with a leading `!`, so there is nothing here to
  strip.

## [0.6.0] - 2026-08-12 - No unattended calls: the version check is now opt-in

### Changed

- **Heimdall no longer contacts GitHub on its own.** The release check used to
  run on almost every invocation, cached for 24h, with `--no-version-check` or
  `--quiet` as the opt-out. Nobody consented to that and most people never
  read the flag. It is now reachable only through the new `--check-version`,
  and an ordinary run makes no third-party request at all.

  Worth stating why, since the check never sent anything about you or your
  captures: the request itself shows GitHub your source IP, the exact version
  you are running (it is in the User-Agent), which tool you are running, and
  the time you ran it. Roughly once a day, per machine. That is a disclosure
  to a third party, so it should be something you ask for rather than
  something you have to find a flag to stop.

- `_check_for_update()` takes `force=True` from `--check-version` so an
  explicit ask bypasses the 24h cache and gets a fresh answer. `--update`
  still uses the cached path.

### Compatibility

- `--no-version-check` is still accepted and now does nothing. It is baked
  into existing cron lines, systemd units, and schtasks actions, and erroring
  on an unknown argument would break a working scheduled upload. It is hidden
  from `--help`. `--quiet` keeps its other meanings.
- Nothing else changed: uploads, the HMAC envelope, key handling, and the
  scheduler are untouched.

### Added

- `tests/test_security.py::NoUnattendedEgressTests` locks the new contract in:
  a normal run never calls the checker, `--check-version` still does, and the
  legacy flag is still accepted.

## [0.5.0] - 2026-08-10 - node_id from the node's public key

The `bad_node_id` wall comes down for captures that log keys. It was never a
server-side problem: LOCOSP's gate is `[a-f0-9]{8,16}`, so the 16-hex form has
always been accepted, and 13 nodes uploaded through a contributor's own proxy
were already in the database before this release. Every previous changelog entry
treating the length floor as something wdgwars.pl had to relax was reading a
client-side gap as a server-side gate.

### Added

- **`node_id` is derived from the heard node's own public key** (first 16 hex
  chars / 8 bytes) wherever a capture logs one, with the short on-air ID kept
  as the record `name`. MeshCore identifies a node by the leading bytes of its
  key, so the 2-6 hex ID a capture prints is a truncation of the same number,
  not a different identifier: taking more digits of it clears the gate without
  inventing anything. On the contributed `examples/offline-pings.json`, 15 of 16
  unique nodes now clear the node_id gate where previously none did.

  Found by **@nicolasrata** (issue #1, 2026-08-08), who proved it with a proxy
  that rewrote the ID between MeshMapper and Heimdall. Doing it in the parser
  means nobody has to run a proxy. **@formtapez** established the constraint
  that made it necessary: a repeater only puts its full ID on the air in an
  advert frame, so waiting for capture apps to log longer IDs was never going to
  work.

  8 bytes is not an arbitrary cut. LOCOSP confirmed it (2026-08-10) as the
  canonical meshcore node_id: the server's column is `varchar(16)`, and his own
  corpus is why it is not shorter - across 3,723 nodes a 1-byte prefix collides
  for every node, 2 bytes collapses 396 into 179 groups (one prefix, `fddd`,
  covers six distinct nodes), 3 bytes collapses 122 into 57, 4 bytes is the
  first clean one. Collisions are not cosmetic there: the importer updates
  position on an id match, so two repeaters sharing an id overwrite each other's
  coordinates.

  Two guard rails, because a wrong node identity is worse than a rejected one.
  The key is used only when it actually starts with the short ID the capture
  heard, and `RX` token sightings (short ID only) resolve against keys found
  elsewhere in the same capture only when exactly one node matches that prefix.
  Anything ambiguous keeps its short ID and is reported as a predicted
  rejection.

- **`public_key` is sent on records where the capture gave us the node's full
  key.** The field is optional server-side (confirmed live by LOCOSP): when
  present it is checked at 64 hex with `node_id` verified as its prefix,
  rejecting as `bad_public_key` / `key_prefix_mismatch` otherwise, and its
  absence never rejects. Heimdall omits it entirely rather than sending null,
  and never sends a key it cannot tie to that node - a partial key still derives
  an id but is not sent, and where two full keys share one 8-byte prefix the id
  ships without a key rather than guessing which is that node's.

  The point is not proof of existence: anyone can mint a keypair and derive a
  matching id, so the prefix check catches mistakes, not a determined faker.
  Holding full keys is what lets wdgwars.pl re-derive the canonical id form
  later and merge id namespaces deterministically, without asking every feeder
  to change again.

- **Key-bearing pings of any type parse as sightings.** Previously only `DISC`
  and `RX` did. `TRACE` is a second key-bearing type (reported in issue #1, and
  confirmed independently in DedDrop's MeshMapper ingest); gating on the
  presence of `public_key` rather than on a type label means it parses without
  guessing at its spelling.

### Changed

- `predict_server_rejects`'s `bad_node_id` warning no longer says the problem is
  unfixable client-side. It names the actual cause of a surviving short ID (no
  key anywhere in the capture for that node) and points at the offline-JSON
  export, which logs keys where the MeshMapper CSV export does not.

### Notes

- The web flavour picks this up for free: it runs `heimdall.py`'s parser under
  Pyodide rather than reimplementing it.
- Nodes already uploaded as short IDs are not orphaned by this. The short form
  is a prefix of the long one, so the merge is unambiguous, and LOCOSP is
  handling it server-side; no feeder-side migration is expected.

## [0.4.9] - 2026-07-29 - Trim the v0.4.7/v0.4.8 helpers

### Changed

- Internal cleanup of the two previous releases, no behavior change:
  `dict.setdefault` for the first-wins dedupe instead of a parallel seen-set,
  one nullable counter for the response audit instead of a second boolean,
  and the pre-upload warnings cut to the facts. The filler-ID heads-up now
  reads `(3x)` rather than `(3 sightings)`.

## [0.4.8] - 2026-07-29 - CLI collapses repeat sightings; filler-ID heads-up

### Changed

- **The CLI now collapses repeat sightings of the same `node_id` before
  upload** (first sighting wins), matching the web flavour (v0.4.6) and the
  server's own dedupe. A capture that logs the same repeater in several
  DISC/TX rows no longer inflates the parsed count or the upload payload;
  the CLI reports how many sightings were collapsed and how many unique
  nodes remain. The issue #1 sample's "53 nodes" included the same IDs
  sighted repeatedly.

### Added

- **Heads-up for filler node_ids.** A `node_id` that is one hex digit
  repeated (`eeeeee` appeared in three separate sightings of the issue #1
  capture) reads as placeholder output from the capture app rather than a
  heard node. One sample is not enough to filter on, so these upload
  unchanged, but the CLI now flags them with their sighting count.

## [0.4.7] - 2026-07-29 - Predict server rejections before uploading

### Added

- **The CLI now predicts wdgwars.pl's per-record rejections at parse time**
  (issue #1). The server's meshcore gates (node_id must be 8-16 lowercase
  hex; GPS fix must not be 0,0) are mirrored client-side, and a heads-up
  line is printed before upload and preview when records are going to miss
  them. Answers "where does that `bad_node_id` come from?" up front instead
  of leaving a rejected count in the response as the only clue: MeshMapper
  exports only carry a 2-6 hex tail of the mesh public key, which falls
  under the server's 8-hex floor, so those records are rejected server-side
  regardless of anything the client does.
- **The CLI cross-checks the server's response arithmetic.** Every submitted
  node should come back as imported, already seen, or rejected; when a 2xx
  response's counters cover fewer nodes than were sent, a note says how many
  got no verdict. Observed live in issue #1: re-submitting a payload the
  server had just itemised as `bad_node_id: 53` returned all-zero counters,
  which previously printed as a clean "accepted, 0 new" with no trace of
  the 53 dropped records. Heimdall keeps no state between runs; the
  difference was entirely in the server's response.

## [0.4.6] - 2026-07-19 - Web flavour stops pointing players at the website upload form

### Fixed

- **The web copy no longer tells players to drag the downloaded JSON into
  wdgwars.pl's website upload form.** The download is the unsigned
  `/api/upload/` payload envelope (`{networks, aircraft, meshcore_nodes}`);
  the website form is only confirmed to accept WiGLE CSV and dump1090-fa
  aircraft JSON, so meshcore JSON gets a parse error there (player report,
  2026-07-19). The "Next step" hint, web/README.md's public-deploy section,
  and the CORS fallback message now point at the CLI direct upload (API key)
  or a self-hosted `serve.py` proxy instead, until LOCOSP confirms
  website-form support for meshcore JSON.
- The app.js comment that mislabeled the download payload as
  "dump1090-fa-shaped" now describes the actual envelope.

### Changed

- **The web flavour collapses repeat sightings of the same `node_id`**
  (first sighting wins, matching the server's own dedupe) before preview,
  download, and direct upload. The summary now shows how many repeat
  sightings were collapsed. CLI behavior is unchanged; the server already
  reports repeats as `already_seen` on that path.

## [0.4.5] - 2026-07-18 - Wrapper-refreshing --update + org migration

### Fixed

- **`--update` now refreshes the six wrapper scripts** (`run`/`setup`/
  `update` `.sh`/`.bat`) on the raw-download (ZIP install) path, closing the
  family bug where a fix living in a wrapper could never reach ZIP-installed
  users through self-update. The list is hard-coded, not a remote
  manifest. So the update path can never be steered into writing arbitrary
  filenames. Wrapper download failures warn and continue; deleted wrappers
  are respected; `.sh` wrappers get their exec bit restored on POSIX.
  Covered by `tests/test_update_wrappers.py`. Same implementation shape as
  Muninn / wigle-to-wdgwars modulo naming.
- **Org migration completed in code**: `GITHUB_REPO` (drives `--update`, the
  daily version check, and the User-Agent) and the raw-GitHub URLs in all
  four setup/update wrappers now point at `Yggdrasil-AI-labs` instead of
  surviving on GitHub's rename redirect from the old `HiroAlleyCat` owner.
  README's web-flavor link now points at
  `yggdrasil-ai-labs.github.io/meshcore-to-wdgwars`.
- **Docs told the truth again**: SECURITY.md's v0.1.0-era claims ("no
  version-check, no auto-update", "key is not persisted to disk",
  `--api-key`/`--endpoint` as current names) were contradicted by shipped
  behavior since v0.3.0 and are rewritten to match reality. README no longer
  documents an impossible `No module named 'gungnir'` error (Heimdall is
  pure stdlib), no longer promises alias removal "in v0.4" (they still ship;
  removal is now "a future major release"), and the privacy section names
  `--key`. The tests/__init__.py comment falsely claiming a gungnir
  dependency is fixed. The module docstring now records *why* Heimdall is
  the only family member with inlined transport (2026-06-03 decision).

### Removed

- Dead constants `TARGET_FIELDS` and `MESHMAPPER_RX_HEADERS` (defined,
  never referenced). `_normalise_meshmapper_row`'s docstring no longer
  lists `snr` in the wire schema (dropped from the wire in v0.4.3).

## [0.4.4] - Lower-case node_id; surface wdgwars.pl's new reject reasons

LOCOSP confirmed (2026-07-03, mod-reports) the actual cause behind v0.4.2
and v0.4.3 both landing zero change: `/api/upload/`'s meshcore ingest has
gated every node since 2026-05-24 on (1) a real GPS fix, (2) a node_id
that is 8-16 *lowercase* hex, and (3) a recognised node_type, silently
dropping anything that missed, with no `already_seen` or reject reason
in the response to tell the difference. He's now shipped
`meshcore_already_seen`, `meshcore_rejected`, and
`meshcore_reject_reasons: {no_gps, bad_node_id, error}` on his end, and
node_type mismatches now coerce to Unknown instead of being rejected.

MeshMapper's real node IDs are uppercase (e.g. `0CE8`), so that was a
guaranteed miss on the case gate alone, fixed here. The *length* gate is
still an open question: real MeshMapper IDs run 2-4 hex chars, well under
the 8-16 floor, and there's nothing to pad with since MeshMapper never
gives us more bytes than that. Whether that's a client bug or something
wdgwars.pl needs to relax is what `meshcore_reject_reasons` on the next
live test will tell us.

### Fixed

- `_build_record()` lower-cases `node_id` before it goes on the wire.
- CLI now prints `meshcore_rejected` and `meshcore_reject_reasons` when
  present in the upload response, instead of only `meshcore_imported`/
  `meshcore_already_seen`.

## [0.4.3] - Drop blank `name` and unrecognised `snr` from meshcore records

v0.4.2 fixed the `type`/`node_type` swap, but a live re-test (@nicolasrata,
2026-07-03) still came back `meshcore_imported: 0`, unchanged from before
the fix, which means something else is also wrong. Two more differences
from the one confirmed-working record on file:

- Heimdall always sent `"name": ""` (MeshMapper exports carry no name
  field). The confirmed-working record had a real, non-empty name: a
  blank required field is a plausible reason a schema-correct-looking
  record still gets silently dropped.
- Heimdall sent an extra `snr` field not present in the confirmed shape.
  An unrecognised extra key is another plausible silent-drop cause.

`name` now falls back to `node_id` when there's nothing better; `snr` is
dropped from the wire record entirely (still computed internally, just
not sent). Like v0.4.2, **this has not been confirmed against a live
upload**: it's the next best-evidenced guess, not a verified fix.

### Fixed

- `_build_record()` defaults `name` to `node_id` instead of `""`.
- `_build_record()` no longer includes `snr` in the emitted record.

## [0.4.2] - Fix the meshcore record schema: every upload was silently dropped

Every meshcore upload attempt on record (two different real MeshMapper
exports, tested a week apart by @nicolasrata) was accepted by wdgwars.pl
(`ok: true`) but came back with `meshcore_imported: 0` and no
`meshcore_already_seen` key at all. The record shape Heimdall built was
wrong:

- `type` held the node's own role (e.g. `"repeater"`). wdgwars.pl expects
  `type` to be a constant marking the record as part of the meshcore
  family (`"MESHCORE"`), with the actual role in a separate `node_type`
  field, which Heimdall never sent.
- The date field was `timestamp` in full ISO-8601 with microseconds.
  wdgwars.pl expects `first_seen` as `YYYY-MM-DD HH:MM:SS`.

The server never errors on an unrecognized record shape. It just accepts
the envelope and counts nothing, which is why this went unnoticed for two
independent test rounds. New target schema:

```
node_id, node_type, name, lat, lon, rssi, snr, first_seen, type
```

This has not yet been confirmed against a live upload post-fix; if you
hit this, please pull `--update` and report back whether `meshcore_imported`
moves off zero.

### Fixed

- `_normalise_meshmapper_row`, `_node_token_to_record`, and `_ping_to_records`
  now all build records via a single `_build_record()` helper emitting the
  corrected shape (`node_type` + constant `type: "MESHCORE"` + `first_seen`).
- `DEFAULT_NODE_TYPE` and the `(R)` marker map now normalise to uppercase
  (`"REPEATER"`) to match the confirmed casing convention.

## [0.4.1] - Fix false "older version available" update notice

### Fixed

- The daily update check compared the latest GitHub release tag to
  `__version__` with a plain inequality, so any mismatch (including the
  release process lagging behind a version bump already in code) was
  reported as "a newer version is available," even when the tag was
  actually older (issue #9). `_check_for_update()` now orders versions as
  int tuples and only surfaces the notice when the tag is genuinely higher.
- No GitHub Release had been published for 0.3.1 or 0.4.0, so
  `/releases/latest` was legitimately still returning `v0.3.0`. The stale
  release gap is also being closed alongside this fix.

## [0.4.0] - Real MeshMapper formats: multi-section CSV + offline JSON

First release driven by real-world capture data (issue #1 baseline,
contributed by @nicolasrata, 2026-06-27). The parser was written against an
assumed flat "Copy CSV" shape; a real MeshMapper export is a multi-section
file and the offline capture is JSON. The old parser returned **zero** nodes
on both.

### Added

- Multi-section MeshMapper CSV parsing: `--- TX Log ---`, `--- RX Log ---`,
  `--- DISC Log ---` blocks, each with its own header. The heard nodes are
  packed into a trailing `events` (TX) / `nodes` (DISC) column as
  `ID(snr)` / `ID(R)(snr)` tokens; one record is emitted per heard node.
- MeshCore offline ping-log JSON parsing (`pings[]` of `DISC` / `RX`).
  `DISC` pings carry full telemetry including real `local_rssi` + `local_snr`;
  `RX` pings carry a `heard_repeats` SNR token.
- `parse_file()` format dispatch (extension first, then content sniff) and a
  matching `parse_offline_json()` / `parse_meshmapper_text()` API. The CLI and
  the web dropzone both accept CSV or JSON now.
- Scrubbed fixtures: `examples/meshmapper-sections.csv`,
  `examples/offline-pings.json`. Tests covering both new formats.

### Changed

- Flat single-section "Copy CSV" exports still parse exactly as before
  (`examples/sample.csv` is unchanged), the section logic only engages when
  `--- X Log ---` markers are present.
- Web (Pyodide) parser brought to parser parity with the root module and to
  v0.4.0; its dropzone calls `parse_file` and reports the detected format.

### Known limitation

- CSV `TX`/`RX`/`DISC` sections and JSON `RX` pings log SNR + receiver noise
  floor but no per-node RSSI, so those records carry `rssi: null`. Only
  offline-JSON `DISC` pings have a real RSSI. Node-type markers other than
  `(R)` (repeater) are normalised to the default pending a confirmed sample.

## CI quality gates + security review (tooling-only, landed unversioned mid-0.4.x)

Tooling and CI only, no change to `heimdall.py` behavior, so no version bump.
(Header renamed from "[Unreleased]" in v0.4.5: the work has long been on
`main` and this section's mid-file position kept confusing changelog reads.)

Brings Heimdall onto the same gated CI pipeline as the sibling
adsb-to-wdgwars (Muninn) and wigle-to-wdgwars repos: pytest + coverage →
SonarCloud quality gate → Snyk dependency scan → gated release-artifact build.
The `sonarcloud` / `snyk` jobs stay red until the repo is imported into
SonarCloud and the `SONAR_TOKEN` / `SNYK_TOKEN` Actions secrets are added (see
CI.md); the test and coverage stage is independent and passes on its own.
(Heimdall is pure stdlib, so the Snyk stage is effectively a no-op, kept for
family parity.)

A review against the SonarCloud SAST finding classes found nothing to
remediate, the scheduler arguments (including the CSV path) are shell-quoted,
the API key never reaches the command line, and `save_key` refuses symlinks
and uses mode 600. See SECURITY-FINDINGS.md.

### Added

- `.github/workflows/ci-quality-gates.yml`: gated quality + security pipeline.
- `sonar-project.properties`, `requirements-dev.txt`, `pyproject.toml`
  (pytest + coverage config with a regression floor), and `CI.md`.
- `tests/test_security.py`: regression tests locking in the existing
  defenses (shell-quoting incl. the CSV path, no-key-in-argv, safe key-file
  writes).
- `SECURITY-FINDINGS.md`: the security review write-up; pointer added to
  `SECURITY.md`.

## [0.3.1] - 2026-06-05 - Structured 413 message for the 15 MB upload cap

LOCOSP rolled out a temporary 15 MB body cap on every wdgwars.pl upload
endpoint on 2026-06-05 with a structured 413 envelope
(`{error: payload-too-large, max_bytes, received, ...}`). Heimdall does
not use gungnir (its HMAC transport is pure-stdlib local code), so the
gungnir v0.1.3 upgrade does not reach it. This release patches the same
behavior into Heimdall directly: cosmetic log-message change only, no
control-flow change.

Mesh-node payloads are kilobytes per cycle, well under the 15 MB cap,
so this is defensive insurance. If the 413 does fire, the error line
now names the cap and shows `max_bytes` + `received` instead of a
generic "rejected by wdgwars.pl (HTTP 413): payload-too-large".

### Changed

- Upload rejection branch in `main()` checks for the
  `payload-too-large` envelope shape and prints a structured line.
  Other 4xx / 5xx rejections keep the generic format.

### Not changed

- Upload control flow: 413 still returns `rc=1` and skips the batch,
  same as any other rejection. There is no auto-retry blast.
- No new tests: the 413 branch is print-only and the existing test
  suite has no upload-side coverage to extend. Sibling tools
  (gungnir v0.1.3, wigle-to-wdgwars v1.4.0) carry the contract tests
  for the envelope shape.

## [0.3.0] - 2026-06-03 - Family alignment: scheduler + naming + safety nets

Largest end-user-visible alignment of the 2026-06-03 feeder-family audit
sweep. Brings Heimdall to feature parity with Muninn + wigle-to-wdgwars
for the install / schedule / daily-run flow, and aligns the flag names
so muscle memory transfers across the family.

### Added

- `--schedule` / `--unschedule` / `--schedule-csv PATH` /
  `--schedule-time HH:MM` / `--schedule-dry-run`. Installs the right
  artifact per OS: user systemd timer on Linux-with-systemd, user
  crontab on macOS / Linux-without-systemd, scheduled task on
  Windows. Default time `03:00`. Every artifact carries a
  `# managed-by-heimdall` marker so the uninstaller can find and
  remove it cleanly. The API key is **never** baked into the unit
  file / cron line / schtasks action, the saved-on-disk key file
  is read at run-time instead.
- `--key` flag (canonical name, matches Muninn + wigle).
- `--api-url` flag (canonical name, matches Muninn).
- `scripts/smoke.sh`: pre-release smoke (README linter + AST/import
  + offline tests + `--version`/`--help` + Linux/systemd unit-write
  roundtrip + no-key-leak assertion).
- `scripts/check_readme_examples.py`: README linter ported from
  Muninn / wigle. Auto-detects the entrypoint script. Catches
  `python3 heimdall.py ...` examples that drift outside venv-teaching
  blocks. Heimdall is stdlib-only and works without a venv, so two
  intentional bootstrap examples are annotated
  `# direct invocation` to keep the linter quiet.
- README `## Running on a schedule` section with per-OS mechanism
  table.
- README `## Troubleshooting` section.
- `tests/test_scheduler.py`: 17 new tests covering the pure
  renderers (`render_systemd_units` / `render_cron_line` /
  `render_schtasks_create`), HH:MM validation, the schedule mechanism
  selector, and a no-key-leak assertion that the renderers never
  bake credentials into the artifact.

### Changed

- `_prompt_yes_no` now emits an explicit newline after consuming a
  piped-stdin answer. Interactive TTY input gets one from the
  terminal; piped input doesn't, which used to glue the next
  section header onto the prompt line in scripted runs.
- `setup.sh` / `run.sh` / `update.sh` now `[ -t 0 ]`-gate the
  trailing `Press any key to close...`. Piped / non-TTY invocations
  used to hang indefinitely on that line.
- README code-block examples rewritten from `python3 heimdall.py
  ...` to `./run.sh ...` (the venv-aware shim).

### Deprecated

- `--api-key` flag: replaced by `--key`. The old name still works
  and now emits a one-line deprecation note on stderr. Will be
  removed in `v0.4`.
- `--endpoint` flag: replaced by `--api-url`. Same one-release
  deprecation treatment.

### Not changed (out of audit scope)

- gungnir extraction: Heimdall is still pure stdlib with an inlined
  HMAC envelope. The `v0.2-gungnir` branch from earlier never landed
  on `main`. Architectural refactor, not alignment work, filed as
  a tracked note for a separate session.

## [0.2.2] - 2026-06-01 - setup.sh: PEP 668 / Bookworm fix

`setup.sh`, `run.sh`, and `update.sh` now install Heimdall into a
project-local `.venv/` instead of the system Python.

On Raspberry Pi OS Bookworm, Debian 12+, Ubuntu 23.04+, and Homebrew
Python, the previous `python3 -m pip install -r requirements.txt` line
errored out with `error: externally-managed-environment` (PEP 668).
The script crashed before saving the API key. Same flaw as Muninn's
v2.0.8 fix, found by sweeping the feeder family after a Pi24 user
reported the Muninn crash in the WDGoWars Discord.

The wrappers now `python3 -m venv .venv` on first run and call
`.venv/bin/python` for every subsequent step. `run.sh` and `update.sh`
detect the venv and reuse it. If `python3 -m venv` itself fails
(the `python3-venv` apt package missing on some Pi images), the
script prints the exact `sudo apt install -y python3-venv python3-full`
line and exits cleanly instead of leaving a half-installed state.

Heimdall has no third-party deps today, so this is mostly future-proofing
the wrapper, but it removes the Bookworm crash that bit anyone running
`./setup.sh` to save their API key.

### Fixed

- `setup.sh` no longer fails with `externally-managed-environment` on
  PEP 668 distros. Installs into `.venv/` instead.
- `run.sh` and `update.sh` now use `.venv/bin/python` when present.

## [0.2.1] - 2026-05-29 - Harden install/update path (preventive)

Heimdall has no third-party dependencies today and isn't broken by
the install issue that hit Muninn 2.0.1 and wigle-to-wdgwars 1.1.0
(see those changelogs for context). This release applies the same
hardening pattern preventively, so that when Heimdall eventually
migrates its inline HMAC code to the shared `gungnir` library
(matching its siblings), the bootstrap is already robust and no
user hits a `ModuleNotFoundError` on first install or first update
after the dep is added.

### Fixed

- **`heimdall.py --update` now refreshes `requirements.txt` and runs
  `python -m pip install --upgrade -r requirements.txt` against
  `sys.executable` after updating the script.** Today this is a no-op
  (requirements.txt is comment-only); the helper exits early without
  printing a misleading "installing deps" banner when there's nothing
  to install. The plumbing is in place so a future dep-bumping release
  self-heals without needing another wrapper-script revision.

### Added

- **`setup.bat` / `setup.sh` / `update.bat` / `update.sh` now check
  for Python ≥ 3.10 first, fetch `requirements.txt` from `main`, run
  `pip install --upgrade -r requirements.txt`, then invoke
  `heimdall.py`.** Order matters across versions that add or bump a
  dep. Pip has to know about the new dep before heimdall.py tries to
  import it. Previously the wrappers just ran `python heimdall.py
  --setup` (or `--update`) with no dep management at all.

- `_fetch_raw(path, dest)` and `_pip_install_requirements(script_dir)`
  helpers in `heimdall.py`. Used by `--update` to refresh sibling
  files atomically and invoke pip against the currently-running
  interpreter.

## [0.2.0]

### Added
- **`--setup` wizard.** Interactive one-time API-key flow, validates the
  key against `/api/me`, saves it to `~/.config/heimdall/api.key` (mode
  `0600` on Unix) or `%APPDATA%\heimdall\api.key` on Windows. Refuses to
  write through a symlink.
- **`--save-key KEY`.** Non-interactive equivalent for scripted installs.
- **`--whoami`.** Hit `/api/me` and print username + node counts to
  confirm a stored key is good.
- **Persistent API-key resolution.** `--api-key` flag, then
  `$WDGWARS_API_KEY`, then the saved key file. After `--setup`, no key
  flags are needed for daily use.
- **`--update`.** Self-update via `git pull --ff-only` for clones, or
  raw-GitHub fetch + atomic replace for ZIP installs. Syntax-validates
  the downloaded file before swapping.
- **Daily version-check banner.** Quiet GitHub release-API ping cached
  for 24h. Disable per-run with `--no-version-check` or globally with
  `--quiet`. Three-second timeout, never blocks an upload.
- **Helper scripts.** `setup.sh` / `setup.bat`, `run.sh` / `run.bat`,
  `update.sh` / `update.bat` in the repo root for double-click users.
- **Explicit SSL context** on every outbound request (defense in depth).

### Changed
- README rewritten to cover both git-clone and ZIP-download install
  paths, the new key-persistence flow, and the `--update` workflow.


## [0.1.0] - initial alpha

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
