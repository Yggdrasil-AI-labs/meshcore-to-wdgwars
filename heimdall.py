#!/usr/bin/env python3
"""
heimdall.py. MeshMapper CSV to WDGWars meshcore_nodes uplink.

Sibling of Muninn (adsb-to-wdgwars). Same HMAC envelope, same /endpoint/upload/
endpoint, different payload slot. Muninn fills `aircraft`; Heimdall fills
`meshcore_nodes`.

Transport note: Heimdall is deliberately the only family member that
inlines its HMAC envelope/transport (pure stdlib) instead of depending on
the shared gungnir library. It ships as both a CLI and a Pyodide browser
page from this single file, and zero runtime dependencies keeps the web
flavor's bundle trivial. The 2026-06-03 family audit weighed extracting to
gungnir (the historical `v0.2-gungnir` branch is dead, unmergeable
history) and decided to keep the inline transport; transport fixes that
land in gungnir must be ported here by hand. GUNGNIR_RECONCILED_AT below
records the last gungnir release that hand-porting was checked against,
and tests/test_gungnir_drift.py fails once gungnir moves past it, so the
porting debt announces itself instead of waiting to be noticed.

Target schema (`type` is the constant envelope marker; the node's own role
goes in the separate `node_type` field, earlier releases swapped these two
and had every upload accepted with meshcore_imported: 0):

    node_id, node_type, name, lat, lon, rssi, first_seen, type, network

`network` is a constant "meshcore" (LOCOSP's 2026-08-12 mesh-slot contract,
which now takes both MeshCore and Meshtastic and tells them apart by this
field rather than by role-name casing). `public_key`, `path_hops`, and
`path_length` are optional, sent only when the capture actually gave that
value; a hopped sighting still counts, it just cannot move a node's position
ahead of one that arrived at least as directly.

License: MIT
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


__version__ = "0.9.2"
GITHUB_REPO = "Yggdrasil-AI-labs/meshcore-to-wdgwars"

# /endpoint/* is the server-side alias of /api/*: same router, same HMAC
# envelope, same response. It sits outside the /api/* pattern Cloudflare's
# L7 shield gates during an event, which is when a feeder most needs both
# calls to work. Muninn's shared transport (gungnir) moved uploads in
# v0.1.2 and key validation in v0.1.6; Heimdall carries its own constants,
# so it follows here. Both paths were confirmed to answer identically
# (2026-09-15). Override the upload URL with --api-url.
DEFAULT_ENDPOINT = "https://wdgwars.pl/endpoint/upload/"
ME_API_URL = "https://wdgwars.pl/endpoint/me"
BATCH_SIZE = 1000

# ── Scheduler constants ─────────────────────────────────────────────────────
SCHEDULE_MARKER = "managed-by-heimdall"
SYSTEMD_UNIT_NAME = "heimdall"  # .service + .timer share this stem
WINDOWS_TASK_NAME = "Heimdall"
DEFAULT_SCHEDULE_TIME = "03:00"

# Every record in the meshcore_nodes envelope carries a constant `type` - 
# it marks the record as belonging to this envelope family, mirroring how
# Muninn's `aircraft` records don't repeat "aircraft" per row. The node's
# own role (repeater/client/zigbee/...) goes in the separate `node_type`
# field. A prior mesh feeder confirmed this shape live (a push using
# node_type + a constant `type: MESHCORE` returned meshcore_imported: 1).
# Every Heimdall release before this one put the role in `type` and never
# sent `node_type` at all, and every one of those uploads came back
# accepted (ok: true) but with meshcore_imported: 0 - the server silently
# drops unrecognised record shapes rather than erroring, so this is easy
# to get wrong quietly. See CHANGELOG for the v0.4.2 writeup.
MESHCORE_ENVELOPE_TYPE = "MESHCORE"

DEFAULT_NODE_TYPE = "REPEATER"

# ── MeshCore app database (SQLite) ──────────────────────────────────────────
# The MeshCore phone app keeps its own SQLite store, and it holds several
# times the nodes its JSON export emits. Checked against a real capture:
# every node in the export was also in the database, while a large
# majority of the database's nodes carrying both a fix and a key never
# reached the export at all. It is a strict superset, not a different view.
#
# Two tables carry nodes with the same column set: `contacts` is the list the
# operator has saved, `discovered_contacts` is everything the app has ever
# heard. They overlap by public_key and are unioned here.
MESHCORE_DB_TABLES = ("discovered_contacts", "contacts")

# adv_lat / adv_lon are integers scaled by 1e6, not the 1e7 some other mesh
# stacks use. Confirmed by decoding the reference dump both ways and checking
# the results against the place names the rows carry in `adv_name`: at 1e6
# every node landed in the region its own name claimed, and at 1e7 the whole
# mesh landed in the Gulf of Guinea. Divided rather than shifted because the
# scale is decimal, not binary.
MESHCORE_DB_COORD_SCALE = 1_000_000

# `type` is an integer in the database, not a role name, so unlike every
# other Heimdall input this one cannot pass the role through verbatim -
# there is no name in there to pass through. Mapping confirmed 2026-08-13 by
# the operator of the reference node, from the app rather than by guessing.
# Casing follows the rest of this module (DEFAULT_NODE_TYPE,
# _NODE_TYPE_MARKERS); the server maps these onto its own internal set.
MESHCORE_DB_NODE_TYPES = {
    1: "COMPANION",
    2: "REPEATER",
    3: "ROOM_SERVER",
    4: "SENSOR",
}

# last_advert is a unix timestamp and a small minority of rows hold garbage:
# in a real capture a handful sat outside any plausible range, some far in
# the past and some far in the future, the worst reading as the year 2083.
# Those rows are dropped rather than clamped. WDGWars decides which sighting
# owns a node's position partly by recency, so uploading a year-2083
# first_seen would outrank every genuine sighting of that node indefinitely,
# and clamping to "now" does the same damage more quietly. Dropping costs
# one node; guessing corrupts one node's position for every player.
MESHCORE_DB_TS_FLOOR = 1_577_836_800   # 2020-01-01, before MeshCore shipped
MESHCORE_DB_TS_SKEW_AHEAD = 86_400     # tolerate a day of clock skew

# A real MeshMapper export is a multi-section file. Each block starts with a
# marker line like "--- DISC Log ---" and carries its own header row:
#
#   --- TX Log ---
#   timestamp,latitude,longitude,power,events
#   2026-06-27T11:37:20.859937,0.0,0.0,0.6,0CE8(-0.25)
#
#   --- DISC Log ---
#   timestamp,latitude,longitude,noisefloor,node_count,nodes
#   2026-06-27T11:36:48.792735,0.0,0.0,-99,2,910E(R)(-6.00),0CE8(R)(1.25)
#
# A flat single-section file (the legacy "Logs -> Copy CSV" RX export) has no
# markers; we treat the whole file as one unnamed section so those keep
# parsing exactly as before.
_SECTION_RE = re.compile(r"^---\s*(.+?)\s+Log\s*---\s*$", re.IGNORECASE)

# Columns that pack a list of heard nodes into a single (trailing) field:
# DISC uses "nodes", TX uses "events". The MeshCore offline-JSON RX pings use
# "heard_repeats" with the same token grammar.
_PACKED_NODE_COLUMNS = ("nodes", "events", "heard_repeats")

# One packed node token:
#   DISC: 910E(R)(-6.00)  -> id=910E, marker=R, snr=-6.00
#   TX:   0CE8(-0.25)     -> id=0CE8, marker=None, snr=-0.25
_NODE_TOKEN_RE = re.compile(
    r"^([0-9A-Fa-f]{2,})"          # node id (variable-width hex)
    r"(?:\(([A-Za-z])\))?"          # optional single-letter type marker, e.g. (R)
    r"\(([-+]?\d+(?:\.\d+)?)\)$"     # (snr) in parentheses
)

# Node-type markers seen in real exports. Only "R" (repeater) is confirmed
# from the 2026-06-27 baseline. An unrecognised marker is still something the
# capture actually recorded, so it rides through as-is (LOCOSP's 2026-08-12
# contract: send the role exactly as captured, the server keeps it verbatim
# and maps it internally) rather than being coerced to the default, which
# would misrepresent it as a repeater it may not be. Do not guess at a full
# name for a letter we have not actually seen; only the confirmed R->REPEATER
# expansion is a translation, everything else passes through untouched.
_NODE_TYPE_MARKERS = {"R": "REPEATER"}

# MeshCore identifies a node on the air by the leading bytes of its public key,
# and the short ID a capture prints (0CE8) is exactly that prefix: 2-6 hex,
# well under the 8-16 lowercase hex wdgwars.pl's ingest requires, which is why
# every node in every MeshMapper capture came back bad_node_id (issue #1).
# Where a capture also logs the node's *full* public key, the first 8 bytes of
# that key are the same identity carried further out - more digits of the same
# number, nothing invented - and they clear the gate. Contributed by
# nicolasrata (issue #1, 2026-08-08), who proved it with a proxy that rewrote
# the ID before handing the capture to Heimdall; this does it in the parser so
# nobody needs to stand up a proxy.
#
# 8 bytes is not our convention to pick: LOCOSP confirmed it (2026-08-10, DM)
# as the canonical meshcore node_id, and it is a hard ceiling either way since
# the server's node_id column is varchar(16). His own corpus is why it isn't
# shorter - across 3,723 nodes a 1-byte prefix collides for every single node,
# 2 bytes collapses 396 nodes into 179 groups (one prefix, fddd, covers six
# distinct nodes), 3 bytes collapses 122 into 57, and 4 bytes is the first
# clean one. A node_id collision is not cosmetic there: the importer updates
# position on an id match, so two repeaters sharing an id overwrite each
# other's coordinates. The short on-air ID is a local disambiguator, not an
# identity.
PUBKEY_NODE_ID_HEX = 16

# A full MeshCore public key is a 32-byte Ed25519 key, i.e. exactly 64 hex.
# wdgwars.pl rejects anything else as bad_public_key, so a key that survives
# `derive_node_id` (which only needs enough hex to slice an id out of) is not
# automatically fit to send.
PUBKEY_FULL_HEX = 64

_HEX_ONLY_RE = re.compile(r"^[0-9A-Fa-f]+$")


def _clean_pubkey(public_key: Any) -> str:
    """Normalise a public key to lowercase hex, or "" if it isn't hex."""
    key = str(public_key or "").strip().lower()
    return key if key and _HEX_ONLY_RE.match(key) else ""


def derive_node_id(public_key: Any, display_id: str) -> str:
    """Return the longest node_id justified by one heard node's own key.

    Prefers the first `PUBKEY_NODE_ID_HEX` hex chars of `public_key`, but only
    when the key is pure hex, long enough, and actually *starts with*
    `display_id`. That prefix check is what ties the key to the node the
    capture heard: a capture that ever pairs a key with a different node's
    short ID falls back instead of uploading a confident wrong identity.
    The fallback is `display_id` unchanged, which `predict_server_rejects`
    then flags as too short for the server.
    """
    key = _clean_pubkey(public_key)
    display_id = str(display_id or "").strip()
    if len(key) < PUBKEY_NODE_ID_HEX:
        return display_id
    if display_id and not key.startswith(display_id.lower()):
        return display_id
    return key[:PUBKEY_NODE_ID_HEX]


def _pubkey_for(pubkeys: set[str] | None, display_id: str) -> str | None:
    """Resolve a short on-air ID against full public keys learned elsewhere in
    the same capture.

    Matches by prefix, since the short ID *is* the key's leading hex. Returns
    None unless exactly one identity matches: two different keys sharing a
    short ID means the capture can't tell those nodes apart, and guessing one
    would attach a sighting to the wrong node - which on wdgwars.pl means one
    repeater overwriting another's position, not just an untidy row. Keys
    agreeing on their first `PUBKEY_NODE_ID_HEX` chars are one identity for
    node_id purposes, so they resolve; the full key is only returned when it
    is also unambiguous, since that is what gets asserted on the wire.
    """
    if not pubkeys or not display_id:
        return None
    prefix = display_id.strip().lower()
    keys = {k for k in pubkeys if k.startswith(prefix)}
    if len({k[:PUBKEY_NODE_ID_HEX] for k in keys}) != 1:
        return None
    if len(keys) == 1:
        return keys.pop()
    # One identity logged under two different full keys: enough to agree on the
    # node_id, not enough to claim either key is that node's.
    return sorted(keys)[0][:PUBKEY_NODE_ID_HEX]


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _format_first_seen(ts: str) -> str:
    """Render a timestamp as wdgwars.pl's confirmed `first_seen` shape:
    'YYYY-MM-DD HH:MM:SS' (space-separated, no fractional seconds or UTC
    offset). Falls back to the raw string on anything that doesn't parse;
    better to send something than raise mid-upload over a formatting
    mismatch."""
    try:
        return datetime.datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return ts


def _build_record(node_id: str, node_type: str, name: str,
                  lat: float, lon: float, rssi: float | None,
                  snr: float | None, timestamp: str,
                  public_key: Any = None,
                  path_hops: Any = None,
                  path_length: Any = None) -> dict[str, Any]:
    """Assemble one meshcore_nodes record in the confirmed wdgwars.pl wire
    shape (`type` is the constant envelope marker, `node_type` carries the
    node's actual role, the date field is `first_seen`).

    `name` falls back to `node_id` (original casing, human-readable) rather
    than an empty string: MeshMapper exports never carry a real name.

    `node_id` itself is lower-cased: wdgwars.pl confirmed (2026-07-03) that
    `/endpoint/upload/`'s meshcore ingest gates every node on a real GPS fix, a
    node_id that is 8-16 *lowercase* hex, and a known node_type, silently
    dropping anything that misses. MeshMapper's real node IDs are uppercase
    (e.g. "0CE8"), so this was one guaranteed rejection. The *length* gate
    (8-16 hex) is answered upstream of here by `derive_node_id`, which takes
    the node_id from the node's own public key when the capture logs one; a
    capture without keys still arrives here 2-6 hex and still misses, which
    `predict_server_rejects` says out loud before the upload.

    `public_key` is an optional wire field (confirmed live by LOCOSP,
    2026-08-10): when present the server checks it is 64 hex and that node_id
    really is its prefix, rejecting as bad_public_key / key_prefix_mismatch
    otherwise, and its absence never rejects a record. It is sent whenever the
    capture actually gave us that node's full key, and omitted entirely (not
    sent as null) when it did not. The point is not proof - anyone can mint a
    keypair and derive a matching id, so it catches mistakes, not a determined
    faker - it is that holding the keys lets wdgwars.pl re-derive the canonical
    id form later and merge id namespaces deterministically, without asking
    every feeder to change.

    `network` is a constant "meshcore": LOCOSP's 2026-08-12 mesh-slot
    contract lets a feeder state which network a record belongs to instead
    of the server inferring it from role-name casing, and Heimdall only ever
    parses MeshCore captures, so it says so on every record.

    `node_type` is sent exactly as given, not upper-cased. Earlier releases
    forced upper-case here, which is itself a normalisation of a captured
    value; the same 2026-08-12 contract asks feeders to send the role
    exactly as captured and let the server map it onto its own internal set.

    `path_hops` / `path_length` are optional wire fields, same omit-rather-
    than-null pattern as `public_key`: included when the capture actually
    gave a hop count for this sighting, omitted entirely when it did not.
    Their absence never rejects a sighting per the same contract, a hopped
    sighting always counts, it just cannot move a node's position ahead of
    a sighting that is at least as direct."""
    record = {
        "node_id": node_id.lower(),
        "node_type": node_type,
        "name": name or node_id,
        "lat": lat,
        "lon": lon,
        "rssi": rssi,
        "first_seen": _format_first_seen(timestamp),
        "type": MESHCORE_ENVELOPE_TYPE,
        "network": "meshcore",
    }
    key = _clean_pubkey(public_key)
    if len(key) == PUBKEY_FULL_HEX and key.startswith(record["node_id"]):
        record["public_key"] = key
    if path_hops not in (None, ""):
        record["path_hops"] = path_hops
    if path_length not in (None, ""):
        try:
            record["path_length"] = int(path_length)
        except (TypeError, ValueError):
            pass
    return record


# ───────────────────────── Already-sent holds ────────────────────────────────
#
# Don't re-upload nodes the server has already confirmed it holds. A capture
# pushed on a timer carries the same nodes every run, and the server counts
# those as syncs that carried nothing new (the Uplink page says so out loud).
#
# The mechanism is gungnir.holds, shared with Muninn and wigle-to-wdgwars, but
# Heimdall does NOT take a dependency on gungnir and this code must not make
# it one. Two reasons, both load-bearing:
#
#   1. The 2026-06-03 family audit deliberately kept Heimdall's transport
#      inlined so this one file ships as both a CLI and a Pyodide page with
#      zero runtime dependencies.
#   2. gungnir imports `ssl` at module scope and builds an SSL context at
#      import time. Pyodide unvendors `ssl`. v0.8.0 already took the live
#      Pages deploy down with an import of that shape.
#
# So: imported lazily inside the two functions that need it, never at module
# scope, never in the browser, and any failure leaves the gate simply off.
# An operator who has gungnir installed gets the gate; everyone else keeps
# exactly the behaviour they had.
# The gungnir release Heimdall's inlined transport was last reconciled
# against. Heimdall does not depend on gungnir (see the module docstring),
# so a transport fix landing there reaches this file only when a human
# carries it over. Nothing announced that debt before: check_deliberate_skip
# shipped in gungnir v0.1.4 and Heimdall went without it, while gungnir's
# own README listed Heimdall as a consumer.
#
# Bump this after reading gungnir's changelog and either porting the change
# or deciding it does not apply. The test that reads it is skipped wherever
# gungnir is not installed, which includes CI.
#
# Reconciliation log:
#   0.4.1 -> 0.4.2: nothing to port. The only files that changed were a test
#   and the version string; `git diff v0.4.1..v0.4.2 --name-only` touches no
#   transport code. Checked, not assumed, because the whole value of this
#   marker is that raising it means something.
GUNGNIR_RECONCILED_AT = "0.4.2"

HOLDS_TOOL = "heimdall"
HOLDS_SLOT = "meshcore_nodes"


# Phrases the server uses to say it chose not to reprocess a payload it
# already had. Kept in step with gungnir.diagnostics.DELIBERATE_SKIP_MARKERS;
# matched only against the informational fields, never against counters,
# because an explicit statement is the contract and zero counters are not.
_DELIBERATE_SKIP_MARKERS = ("already uploaded recently", "no new processing")
_SKIP_FIELDS = ("info", "message", "note")


def _deliberate_skip(response: dict) -> str | None:
    """The server's own words if it says it skipped a payload it had."""
    for field in _SKIP_FIELDS:
        text = response.get(field)
        if isinstance(text, str) and any(
                m in text.lower() for m in _DELIBERATE_SKIP_MARKERS):
            return text
    return None


def _in_browser() -> bool:
    """True under Pyodide/Emscripten, where gungnir must not be imported."""
    return sys.platform == "emscripten" or "pyodide" in sys.modules


def _holds():
    """The gungnir.holds module, or None when the gate cannot run here.

    Lazy and broad in what it swallows on purpose. This must never be the
    reason an upload fails or a browser page dies.
    """
    if _in_browser():
        return None
    try:
        import gungnir.holds as _h
        return _h
    except Exception:
        return None


def holds_available() -> bool:
    """Whether the already-sent gate is active in this environment."""
    return _holds() is not None


def filter_already_sent(
        nodes: list[dict[str, Any]],
        now: float) -> tuple[list[dict[str, Any]], int]:
    """Drop nodes still held from an earlier upload.

    Returns ``(remaining, dropped)``. With no gungnir, or nothing held,
    every node is returned: the gate being unavailable must look exactly
    like having nothing to skip.
    """
    h = _holds()
    if h is None or not nodes:
        return nodes, 0
    state = h.prune(h.load(HOLDS_TOOL), now)
    if not state:
        return nodes, 0
    keep = [n for n in nodes
            if not h.is_held(n.get("node_id", "").upper() or None, state, now)]
    return keep, len(nodes) - len(keep)


def record_sent_nodes(nodes: list[dict[str, Any]], sent_at: float,
                      imported: int | None) -> None:
    """Hold the nodes just uploaded, for as long as the server's answer
    justifies: a day when it imported nothing (so it already held them
    all), an hour otherwise.

    ``imported`` is the caller's own total across chunks, NOT read back
    from gungnir's watermark: Heimdall posts with its own transport and
    never writes one. None means the total could not be established, which
    is not the same as zero and must not earn the long hold.
    """
    h = _holds()
    if h is None or not nodes:
        return
    h.record_keys(HOLDS_TOOL,
                  [n.get("node_id", "").upper() for n in nodes],
                  sent_at, h.ttl_for(imported))


def collapse_repeat_sightings(
        nodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Collapse repeat sightings of the same node_id, first sighting wins.

    Matches the web flavour (v0.4.6) and the server's own dedupe, which
    reports repeats as already_seen. Returns (unique_nodes, collapsed_count);
    input order is preserved.
    """
    uniq: dict[str, dict[str, Any]] = {}
    for n in nodes:
        uniq.setdefault(n["node_id"], n)
    return list(uniq.values()), len(nodes) - len(uniq)


# A node_id that is one hex digit repeated (eeeeee, ffff, 000000). Seen in a
# real MeshMapper export (issue #1, 2026-07-27: `eeeeee` appearing in three
# separate sightings) where it reads as placeholder output rather than a
# heard node. One sample is not enough to filter on, so these are flagged,
# never dropped.
_FILLER_ID_RE = re.compile(r"^([0-9a-f])\1+$")


def flag_filler_ids(nodes: list[dict[str, Any]]) -> list[str]:
    """Warn about node_ids that look like MeshMapper placeholder output.

    Takes the raw (pre-collapse) sighting list so the warning can say how
    often the ID recurred. Returns human-readable warning lines.
    """
    counts: dict[str, int] = {}
    for n in nodes:
        if _FILLER_ID_RE.match(n["node_id"]):
            counts[n["node_id"]] = counts.get(n["node_id"], 0) + 1
    return [
        f"node_id '{node_id}' ({count}x) is a single repeated hex digit, "
        f"which looks like placeholder output from the capture app rather "
        f"than a heard node. Uploading it anyway."
        for node_id, count in counts.items()
    ]


# wdgwars.pl's per-record gates, mirrored client-side (see _build_record for
# the 2026-07-03 confirmation). The server silently drops gated records and
# only itemises them in meshcore_reject_reasons after the fact, which reads
# as a mystery ("accepted, 0 new, 53 rejected: bad_node_id" - issue #1).
# Predicting the verdict at parse time turns that into a plain answer.
_SERVER_NODE_ID_GATE = re.compile(r"^[0-9a-f]{8,16}$")


def predict_server_rejects(nodes: list[dict[str, Any]]) -> list[str]:
    """Dry-run wdgwars.pl's per-record gates against built records.

    Returns human-readable warning lines; empty when everything should pass.
    Mirrors the two gates a client can evaluate: node_id shape (8-16
    lowercase hex) and a real GPS fix (not 0,0). The third gate, node_type,
    coerces to Unknown server-side since 2026-07-03 and no longer rejects.
    """
    warnings: list[str] = []
    short = sum(1 for n in nodes
                if not _SERVER_NODE_ID_GATE.match(n["node_id"]))
    if short:
        warnings.append(
            f"{short} of {len(nodes)} node_ids are outside the 8-16 hex range "
            f"wdgwars.pl requires, so the server will reject them as "
            f"bad_node_id. Those sightings name a node only by its short "
            f"on-air ID (2-6 hex) with no public key anywhere in the capture "
            f"to derive a longer one from. MeshCore's offline ping-log JSON "
            f"logs the key on DISC pings; the MeshMapper CSV export does not."
        )
    no_gps = sum(1 for n in nodes if not n["lat"] and not n["lon"])
    if no_gps:
        warnings.append(
            f"{no_gps} of {len(nodes)} nodes have no GPS fix (lat/lon 0,0); "
            f"the server will reject each of them as no_gps."
        )
    return warnings


def _node_token_to_record(token: str, timestamp: str,
                          lat: float, lon: float,
                          pubkeys: set[str] | None = None,
                          ) -> dict[str, Any] | None:
    """Parse one ID(snr) / ID(R)(snr) token into a meshcore record.

    rssi is None: TX/DISC/RX packed tokens carry SNR but no per-node RSSI
    (the section only logs the receiver's noise floor, not a signal level).
    Returns None for tokens that don't match the grammar.

    A packed token never carries a public key of its own, so `pubkeys` lets a
    caller pass the keys learned elsewhere in the same capture (offline-JSON
    DISC pings do carry them) and have the token's short ID resolved to the
    same longer node_id the DISC records use. Without it the short ID stands,
    which is the previous behaviour and all a CSV export can support.
    """
    token = token.strip()
    if not token:
        return None
    m = _NODE_TOKEN_RE.match(token)
    if not m:
        return None
    display_id, marker, snr_s = m.group(1), m.group(2), m.group(3)
    node_type = (_NODE_TYPE_MARKERS.get(marker.upper(), marker.upper())
                 if marker else DEFAULT_NODE_TYPE)
    key = _pubkey_for(pubkeys, display_id)
    node_id = derive_node_id(key, display_id)
    return _build_record(node_id, node_type, display_id, lat, lon, None,
                        float(snr_s), timestamp, key)

# Explicit SSL context. urllib defaults to system trust + full cert verification
# since Python 3.4.3 (PEP 476); being explicit just makes that obvious in review.
_SSL_CTX = ssl.create_default_context()


# ---------------------------------------------------------------------------
# Color tags
# ---------------------------------------------------------------------------

_USE_COLOR = sys.stderr.isatty() and os.environ.get("NO_COLOR") is None


def _tag(label: str, code: str) -> str:
    if _USE_COLOR:
        return f"\033[{code}m{label}\033[0m"
    return label


def _OK() -> str:   return _tag("[OK]", "1;32")
def _FAIL() -> str: return _tag("[FAIL]", "1;31")
def _INFO() -> str: return _tag("[..]", "1;36")


# ---------------------------------------------------------------------------
# Config dir + persistent API key
# ---------------------------------------------------------------------------

def _config_dir() -> Path:
    """Persistent config location. %APPDATA%\\heimdall on Windows, XDG on Unix."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "heimdall"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "heimdall"


def _key_path() -> Path:
    return _config_dir() / "api.key"


def load_key(cli_key: str | None) -> str:
    """Resolve API key in priority order:
    1. --api-key CLI flag
    2. $WDGWARS_API_KEY env var
    3. saved api.key under the user config dir
    """
    if cli_key:
        return cli_key.strip()
    env = os.environ.get("WDGWARS_API_KEY", "").strip()
    if env:
        return env
    p = _key_path()
    if p.exists():
        try:
            return p.read_text().strip()
        except Exception as e:
            print(f"warn: could not read {p}: {e}", file=sys.stderr)
    return ""


def save_key(key: str) -> None:
    """Save the API key to user config. Refuses to write through a symlink
    so a hostile redirect cannot trick us into overwriting unrelated files."""
    p = _key_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_symlink():
        sys.exit(f"refusing to write through symlink: {p} -> {os.readlink(p)}\n"
                 f"remove the symlink and re-run --save-key")
    # Open with restrictive mode BEFORE writing so the secret is never
    # world-readable, even briefly.
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (key.strip() + "\n").encode())
    finally:
        os.close(fd)
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass
    print(f"[heimdall] saved API key to {p}", file=sys.stderr)
    print(f"[heimdall] (file mode 600 on Unix, only your user can read it)", file=sys.stderr)
    print(f"[heimdall] you can now run uploads without --api-key or env var",
          file=sys.stderr)


def _scrub(text: str, key: str) -> str:
    """If the API key ever leaks into a server error or trace, redact before printing."""
    if key and len(key) > 8 and key in text:
        return text.replace(key, key[:4] + "..." + key[-4:])
    return text


# ---------------------------------------------------------------------------
# /endpoint/me whoami check
# ---------------------------------------------------------------------------

def check_whoami(key: str) -> int:
    """Hit /endpoint/me to validate the key. Prints username + counts on success.
    Never echoes the API key in any output, even on failure."""
    req = urllib.request.Request(
        ME_API_URL,
        headers={"X-API-Key": key,
                 "User-Agent": f"heimdall/{__version__}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            data = json.loads(resp.read().decode())
            if not data.get("ok"):
                err = data.get("error", "unknown")
                print(f"[heimdall] key rejected: {_scrub(err, key)}",
                      file=sys.stderr)
                return 1
            print(f"[heimdall] key OK, user={data.get('username')}",
                  file=sys.stderr)
            # Print whatever counters the server gives us. Heimdall's interest
            # is meshcore, but show the full picture so users can sanity-check.
            parts = []
            for label in ("wifi", "ble", "aircraft", "meshcore", "total"):
                if label in data:
                    parts.append(f"{label}={data.get(label, 0)}")
            if parts:
                print(f"[heimdall]   " + " ".join(parts), file=sys.stderr)
            return 0
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        print(f"[heimdall] HTTP {e.code}: {_scrub(body, key)}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[heimdall] whoami failed: {_scrub(str(e), key)}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Interactive setup wizard
# ---------------------------------------------------------------------------

def _prompt_yes_no(question: str, default: bool = True) -> bool:
    """Ask a y/n question on stderr. Returns the default on EOF or Ctrl+C
    so non-interactive runs do not hang.

    Always emits a newline after the answer when stdin is piped, interactive
    TTY input gets its newline from the terminal, piped input doesn't, which
    would otherwise glue the next section header onto the prompt line.
    """
    suffix = " [Y/n] " if default else " [y/N] "
    piped = not sys.stdin.isatty()
    while True:
        try:
            print(question + suffix, end="", flush=True, file=sys.stderr)
            line = sys.stdin.readline()
            if not line:
                print("", file=sys.stderr)
                return default
            ans = line.strip().lower()
            if piped:
                print("", file=sys.stderr)
        except (KeyboardInterrupt, EOFError):
            print("", file=sys.stderr)
            return default
        if ans == "":
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print(" (please answer y or n)", file=sys.stderr)


def interactive_setup() -> int:
    """First-run setup. Asks yes/no whether to configure an API key, prompts
    for it, validates against /endpoint/me, and saves it on success.
    Returns 0 on success or skip, 1 on cancel."""
    print("", file=sys.stderr)
    print("-" * 60, file=sys.stderr)
    print(" heimdall, API key setup", file=sys.stderr)
    print("-" * 60, file=sys.stderr)
    print("", file=sys.stderr)
    print(" An API key is ONLY needed if you want to upload to WDGWars.", file=sys.stderr)
    print(" Local CSV parsing and --preview work without one.", file=sys.stderr)
    print("", file=sys.stderr)
    print(" Get your key from: https://wdgwars.pl/  ->  profile  ->  API Key", file=sys.stderr)
    print(f" It will be saved to: {_key_path()}", file=sys.stderr)
    print("", file=sys.stderr)
    print(" Generate a key just for Heimdall and name it, rather than reusing", file=sys.stderr)
    print(" one you gave another tool. You can switch a single key off from", file=sys.stderr)
    print(" the same profile page without touching the others.", file=sys.stderr)
    print("", file=sys.stderr)
    print(" By setting up a key you are authorising Heimdall to upload the", file=sys.stderr)
    print(" captures you give it to WDGWars under your own account. It will", file=sys.stderr)
    print(" not ask again per upload. Use --dry-run or --preview to see", file=sys.stderr)
    print(" exactly what would be sent first.", file=sys.stderr)
    print("", file=sys.stderr)

    if not _prompt_yes_no(" Set up your WDGWars API key now?", default=True):
        print("", file=sys.stderr)
        print(" Skipped. You can run setup later with:", file=sys.stderr)
        print("   python3 heimdall.py --setup", file=sys.stderr)
        print("", file=sys.stderr)
        return 0

    while True:
        try:
            if sys.stdin.isatty():
                import getpass
                key = getpass.getpass(" Paste your WDGWars API key (hidden): ").strip()
            else:
                print(" Paste your WDGWars API key: ", end="", flush=True,
                      file=sys.stderr)
                key = sys.stdin.readline().strip()
        except (KeyboardInterrupt, EOFError):
            print("\n[heimdall] setup cancelled, no key saved", file=sys.stderr)
            return 1

        if not key:
            print(" (empty input, try again or Ctrl+C to cancel)\n",
                  file=sys.stderr)
            continue

        print(" Validating key against wdgwars.pl/endpoint/me ...", file=sys.stderr)
        rc = check_whoami(key)
        if rc != 0:
            print(" That key was rejected. Try again, or Ctrl+C to cancel.\n",
                  file=sys.stderr)
            continue

        save_key(key)
        print("", file=sys.stderr)
        print(" Setup complete. You can now run uploads without --api-key:",
              file=sys.stderr)
        print("   python3 heimdall.py path/to/your_export.csv",
              file=sys.stderr)
        print("", file=sys.stderr)
        return 0


# ---------------------------------------------------------------------------
# MeshMapper CSV parsing
# ---------------------------------------------------------------------------

def _normalise_meshmapper_row(row: dict[str, str]) -> dict[str, Any] | None:
    """
    Map one MeshMapper RX-log CSV row to the WDGWars meshcore schema.

    MeshMapper "Copy CSV" header:
        timestamp,repeater_id,snr,rssi,path_length,header,latitude,longitude,path_hops

    Target schema (snr is parsed for validation but dropped from the wire
    since v0.4.3):
        node_id,node_type,name,lat,lon,rssi,first_seen,type,network

    `path_hops` and `path_length` are carried through onto the wire record
    (LOCOSP's 2026-08-12 contract) when this row has them; a flat CSV row
    always has both columns, so in practice they are always sent from here.

    Returns None for rows missing required fields or with unparseable numerics.
    Paste-damaged rows skip silently this way.
    """
    if not row.get("timestamp") or not row.get("repeater_id"):
        return None
    try:
        lat = float(row["latitude"])
        lon = float(row["longitude"])
        rssi = float(row["rssi"])
        snr = float(row["snr"])
    except (TypeError, ValueError, KeyError):
        return None

    return _build_record(row["repeater_id"], DEFAULT_NODE_TYPE, "",
                        lat, lon, rssi, snr, row["timestamp"],
                        path_hops=row.get("path_hops"),
                        path_length=row.get("path_length"))


def _split_sections(lines: list[str]) -> list[tuple[str | None, list[str]]]:
    """Split a MeshMapper export into (section_name, content_lines) blocks.

    A flat file with no "--- X Log ---" markers yields a single
    (None, all_non_blank_lines) block, so legacy exports parse unchanged.
    Section names are upper-cased ("TX", "RX", "DISC").
    """
    sections: list[tuple[str | None, list[str]]] = []
    name: str | None = None
    block: list[str] = []
    for line in lines:
        m = _SECTION_RE.match(line.strip())
        if m:
            if block:
                sections.append((name, block))
            name = m.group(1).strip().upper()
            block = []
            continue
        if line.strip():
            block.append(line)
    if block:
        sections.append((name, block))
    return sections


def _parse_packed_section(cols: list[str], packed_idx: int,
                          data_lines: list[str]) -> list[dict[str, Any]]:
    """Parse a TX/DISC/RX section whose heard nodes are packed into a trailing
    column. Everything from `packed_idx` onward is treated as node tokens,
    because that column holds a comma-separated, unquoted token list that
    csv.reader splits into multiple trailing fields."""
    lower = [c.lower() for c in cols]
    ts_i = lower.index("timestamp") if "timestamp" in lower else 0
    lat_i = lower.index("latitude") if "latitude" in lower else None
    lon_i = lower.index("longitude") if "longitude" in lower else None
    out: list[dict[str, Any]] = []
    for raw in csv.reader(data_lines):
        if not raw or ts_i >= len(raw) or not raw[ts_i].strip():
            continue
        ts = raw[ts_i].strip()
        lat = _safe_float(raw[lat_i]) if lat_i is not None and lat_i < len(raw) else 0.0
        lon = _safe_float(raw[lon_i]) if lon_i is not None and lon_i < len(raw) else 0.0
        for tok in raw[packed_idx:]:
            rec = _node_token_to_record(tok, ts, lat, lon)
            if rec is not None:
                out.append(rec)
    return out


def _parse_section(block: list[str]) -> list[dict[str, Any]]:
    """Parse one section (header line + data lines) to meshcore records."""
    if not block:
        return []
    header = next(csv.reader([block[0]]))
    cols = [c.strip() for c in header]
    lower = [c.lower() for c in cols]
    for key in _PACKED_NODE_COLUMNS:
        if key in lower:
            return _parse_packed_section(cols, lower.index(key), block[1:])
    # Flat RX section (legacy "Copy CSV" shape): map via the row normaliser.
    out: list[dict[str, Any]] = []
    for row in csv.DictReader(block):
        norm = _normalise_meshmapper_row(row)
        if norm is not None:
            out.append(norm)
    return out


def parse_meshmapper_text(text: str) -> list[dict[str, Any]]:
    """Parse MeshMapper CSV text (flat or multi-section) to meshcore records."""
    records: list[dict[str, Any]] = []
    for _name, block in _split_sections(text.splitlines()):
        records.extend(_parse_section(block))
    return records


class _UnsafeInput(ValueError):
    """A user-supplied path is unsafe to use as given."""


def _reject_control_chars(value: str, label: str) -> str:
    """Reject NUL / CR / LF in a path-bound value (poison-null-byte truncation
    and newline injection). Returns the value unchanged when clean."""
    if "\x00" in value or "\n" in value or "\r" in value:
        raise _UnsafeInput(
            f"refusing {label} with an embedded control character")
    return value


def _user_path(raw: str, *, label: str = "path") -> Path:
    """Normalise an untrusted capture path (argv / --schedule-csv).

    Expands ``~``, rejects embedded control characters, and collapses
    ``..``/``.`` to one canonical absolute path via ``resolve()``. Mirrors
    Muninn's sibling helper: it deliberately does NOT confine the result to a
    root (an operator CLI legitimately reads wherever the operator points it),
    but canonicalising the untrusted path at the boundary is what lets the
    downstream reads treat it as validated (pythonsecurity:S8707). See
    SECURITY-FINDINGS.md for the threat-model rationale."""
    _reject_control_chars(str(raw), label)
    return Path(raw).expanduser().resolve()


def parse_meshmapper_csv(path: Path) -> list[dict[str, Any]]:
    return parse_meshmapper_text(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# MeshCore offline ping-log JSON parsing
# ---------------------------------------------------------------------------

def _epoch_to_iso(ts: Any) -> str:
    """Render a ping timestamp as ISO-8601. Offline-JSON pings use epoch
    seconds; pass through strings unchanged (already ISO in practice)."""
    if ts is None:
        return ""
    if isinstance(ts, str):
        return ts
    try:
        return datetime.datetime.fromtimestamp(
            int(ts), datetime.timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return str(ts)


def _ping_to_records(ping: dict[str, Any],
                     pubkeys: set[str] | None = None,
                     ) -> list[dict[str, Any]]:
    """Map one offline-JSON ping to meshcore records.

    DISC pings name a single repeater with full telemetry (real local_rssi +
    local_snr + node_type) *and* its full `public_key`, which is what the
    node_id is derived from. RX pings carry a `heard_repeats` token list with
    SNR only, same grammar as the CSV packed columns (so rssi stays None); they
    name nodes by short ID alone, so they lean on `pubkeys` to reach the same
    node_id as the DISC records for the same node.

    Any other ping type carrying a `public_key` is treated as DISC-shaped.
    issue #1 reports TRACE as a second key-bearing type, and gating on the
    fields present rather than on a type label we have never seen a sample of
    means TRACE parses without anyone guessing at its spelling.
    """
    ptype = str(ping.get("type") or "").upper()
    ts = _epoch_to_iso(ping.get("timestamp"))
    lat = _safe_float(ping.get("lat"))
    lon = _safe_float(ping.get("lon"))
    if ptype == "RX":
        out: list[dict[str, Any]] = []
        for tok in str(ping.get("heard_repeats") or "").split(","):
            rec = _node_token_to_record(tok, ts, lat, lon, pubkeys)
            if rec is not None:
                out.append(rec)
        return out
    if ptype == "DISC" or ping.get("public_key"):
        display_id = str(ping.get("repeater_id") or "")
        node_id = derive_node_id(ping.get("public_key"), display_id)
        if not node_id:
            return []
        node_type = str(ping.get("node_type") or DEFAULT_NODE_TYPE)
        snr = ping.get("local_snr")
        rssi = ping.get("local_rssi")
        return [_build_record(
            node_id, node_type, display_id, lat, lon,
            _safe_float(rssi) if rssi is not None else None,
            _safe_float(snr) if snr is not None else None, ts,
            ping.get("public_key"),
        )]
    return []


def _collect_pubkeys(pings: list[Any]) -> set[str]:
    """Gather every full public key in a capture, lower-cased.

    Read in one pass before parsing so a node's key can reach the sightings
    that named it by short ID alone, whatever order the pings arrive in.
    """
    keys: set[str] = set()
    for ping in pings:
        if not isinstance(ping, dict):
            continue
        key = str(ping.get("public_key") or "").strip().lower()
        if len(key) >= PUBKEY_NODE_ID_HEX and _HEX_ONLY_RE.match(key):
            keys.add(key)
    return keys


def parse_offline_json_obj(data: dict[str, Any]) -> list[dict[str, Any]]:
    pings = data.get("pings", [])
    if not isinstance(pings, list):
        return []
    pubkeys = _collect_pubkeys(pings)
    records: list[dict[str, Any]] = []
    for ping in pings:
        if isinstance(ping, dict):
            records.extend(_ping_to_records(ping, pubkeys))
    return records


def parse_offline_json(path: Path) -> list[dict[str, Any]]:
    """Parse a MeshCore 'offline' ping-log JSON export (DISC + RX pings)."""
    return parse_offline_json_obj(json.loads(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# MeshCore app database (SQLite)
# ---------------------------------------------------------------------------

def _import_sqlite3():
    """Import sqlite3 on demand, with an explanation when it is not there.

    Imported here rather than at module top level because Pyodide *unvendors*
    sqlite3 from the standard library, exactly as it does `ssl`. A top-level
    `import sqlite3` therefore does not degrade database support in the web
    frontend, it takes the whole page down before it can parse anything at
    all, CSV and JSON included, since heimdall.py is imported as one module.
    Confirmed against the pinned Pyodide 0.26.4: bare import raises
    ModuleNotFoundError, and `loadPackage("sqlite3")` then provides SQLite
    3.39.0. web/app.js loads it alongside `ssl` for that reason.

    Keeping the import lazy means a web deploy that forgets the package still
    handles every text format and only this one input reports a clear reason.
    The CLI is unaffected either way; CPython vendors sqlite3.
    """
    try:
        import sqlite3
    except ModuleNotFoundError as e:  # pragma: no cover - CPython always has it
        raise ValueError(
            "this build has no sqlite3 module, so the MeshCore app database "
            "cannot be read (the CLI is unaffected; on the web frontend the "
            "runtime needs to load the sqlite3 package first). Export the "
            "app's JSON instead, or use the CLI."
        ) from e
    return sqlite3


def _meshcore_db_hops(advert_path: Any, advert_path_len: Any) -> tuple[int | None, int | None]:
    """Recover (path_hops, path_length) from `discovered_contacts`.

    `out_path_len` is -1 on every row of the reference dump, in both tables,
    so that column carries no hop count and earlier analysis concluded the
    database had none. It does: `advert_path_len` is bit-packed, with the hop
    count in the low 6 bits and the *bytes per hop* minus one in the top two:

        hops          = advert_path_len & 0x3F
        bytes_per_hop = (advert_path_len >> 6) + 1

    which predicts the blob width exactly - `len(advert_path) == hops *
    bytes_per_hop` held for every row of a real capture, with no violations,
    across all three observed hop widths (1, 2 and 3 bytes).
    That is what makes this a decode rather than a guess; anything failing
    the identity is a shape this function has not actually seen, so it
    returns None twice and the sighting uploads without a hop count instead
    of with an invented one.

    Hop counts matter here rather than being decoration: WDGWars never
    rejects a hopped sighting, but it will not let one move a node's
    position ahead of a sighting that arrived at least as directly, so
    supplying the count is what lets a direct sighting win on merit.
    `contacts` rows have no advert_path columns at all and get (None, None).
    """
    if advert_path_len is None:
        return None, None
    try:
        packed = int(advert_path_len)
    except (TypeError, ValueError):
        return None, None
    if packed < 0:
        return None, None
    hops = packed & 0x3F
    bytes_per_hop = (packed >> 6) + 1
    blob = bytes(advert_path or b"")
    if len(blob) != hops * bytes_per_hop:
        return None, None
    return hops, len(blob)


def _meshcore_db_pubkey_hex(value: Any) -> str:
    """Render a `public_key` column as hex text.

    The app stores it as a BLOB, and the module's shared `_clean_pubkey` takes
    text - handed raw bytes it stringifies them to "b'\\xd3l...'", which is
    not hex, so the key silently fails its own validity check and every row
    drops. Converted here rather than inside `_clean_pubkey` because every
    other caller already passes text and does not need the branch. Text
    columns pass straight through, in case an app version stores hex.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value or "")


def _meshcore_db_row_to_record(row: dict[str, Any]) -> dict[str, Any] | None:
    """Convert one node row from the MeshCore app database, or None if the row
    cannot be sent honestly.

    Four reasons a row is dropped, all of them the server's own gates or a
    value this parser refuses to invent:
      * no public key, so there is no 16-hex node_id to derive and the server
        would silently drop it;
      * no GPS fix (both coordinates zero), which the ingest gates on;
      * an unrecognised `type` integer - the mapping covers 1-4 and a 5 would
        be a role this parser has never seen, so it is left for a human
        rather than defaulted to REPEATER, which is what silently mislabelled
        rows in an earlier release;
      * a `last_advert` outside any plausible range (see
        MESHCORE_DB_TS_FLOOR).
    """
    key = _clean_pubkey(_meshcore_db_pubkey_hex(row.get("public_key")))
    if len(key) < PUBKEY_NODE_ID_HEX:
        return None

    lat_raw = row.get("adv_lat") or 0
    lon_raw = row.get("adv_lon") or 0
    if not lat_raw and not lon_raw:
        return None
    lat = _safe_float(lat_raw) / MESHCORE_DB_COORD_SCALE
    lon = _safe_float(lon_raw) / MESHCORE_DB_COORD_SCALE

    try:
        node_type = MESHCORE_DB_NODE_TYPES[int(row.get("type"))]
    except (TypeError, ValueError, KeyError):
        return None

    try:
        last_advert = int(row.get("last_advert"))
    except (TypeError, ValueError):
        return None
    ceiling = int(time.time()) + MESHCORE_DB_TS_SKEW_AHEAD
    if not (MESHCORE_DB_TS_FLOOR <= last_advert <= ceiling):
        return None
    timestamp = datetime.datetime.fromtimestamp(
        last_advert, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    hops, path_length = _meshcore_db_hops(
        row.get("advert_path"), row.get("advert_path_len"))

    # `adv_name` is the name the node advertises over the air. `custom_name`
    # is the operator's own private label for it and is deliberately not
    # read: it is local annotation, not something that node broadcast, and
    # uploading it would publish one player's notes about another's node.
    name = str(row.get("adv_name") or "").strip()

    # No RSSI or SNR anywhere in these tables. The database records that a
    # node was heard and where it said it was, not how strongly - that lives
    # in the rx log, which needs a frame decoder. Sent as None rather than a
    # zero, which would read as a real measurement.
    return _build_record(
        derive_node_id(key, ""), node_type, name, lat, lon, None, None,
        timestamp, public_key=key, path_hops=hops, path_length=path_length,
    )


def parse_meshcore_db(path: Path, since_days: float | None = None
                      ) -> list[dict[str, Any]]:
    """Parse the MeshCore app's own SQLite database.

    Reads both node tables and unions them on public_key, keeping the row
    with the newer `last_advert` when a node appears in both. The database is
    all-time, so `since_days` gates on `last_advert` to keep a stale back
    catalogue out of an upload - without it the only way to avoid sending
    years of history was to trim the export by hand before uploading.

    Opened read-only through a file: URI so a live app database is never
    written to, journalled, or upgraded by being read.
    """
    sqlite3 = _import_sqlite3()
    if not path.is_file():
        raise ValueError(f"no such database: {path}")
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    cutoff = None
    if since_days is not None:
        cutoff = time.time() - (float(since_days) * 86_400)

    newest: dict[str, tuple[int, dict[str, Any]]] = {}
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        raise ValueError(f"could not open {path.name} as a database: {e}") from e
    try:
        conn.row_factory = sqlite3.Row
        present = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        tables = [t for t in MESHCORE_DB_TABLES if t in present]
        if not tables:
            raise ValueError(
                f"{path.name} is a database but has no MeshCore node table "
                f"(looked for {' or '.join(MESHCORE_DB_TABLES)})")
        for table in tables:
            for raw in conn.execute(f"SELECT * FROM {table}"):  # noqa: S608
                row = dict(raw)
                if cutoff is not None:
                    try:
                        if int(row.get("last_advert")) < cutoff:
                            continue
                    except (TypeError, ValueError):
                        continue
                record = _meshcore_db_row_to_record(row)
                if record is None:
                    continue
                seen = int(row.get("last_advert") or 0)
                prior = newest.get(record["node_id"])
                if prior is None or seen > prior[0]:
                    newest[record["node_id"]] = (seen, record)
    except sqlite3.Error as e:
        # Surfaced as ValueError so callers need one except clause and do not
        # have to import sqlite3 themselves just to catch a corrupt file. A
        # truncated or half-copied database lands here.
        raise ValueError(f"could not read {path.name}: {e}") from e
    finally:
        conn.close()
    return [record for _, record in newest.values()]


# ---------------------------------------------------------------------------
# Format dispatch
# ---------------------------------------------------------------------------

def _is_sqlite(path: Path) -> bool:
    """True if the file begins with SQLite's 16-byte format magic.

    Sniffed rather than trusted from the extension because the MeshCore app
    shares a database out under whatever name the operator saves it as, and a
    `.db` that is not SQLite should fall through to the text parsers rather
    than raise. Every SQLite file starts with this exact header.
    """
    try:
        with path.open("rb") as fh:
            return fh.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def parse_file(path: Path, since_days: float | None = None
               ) -> tuple[list[dict[str, Any]], str]:
    """Detect the capture format and parse it. Returns (records, format_id).

    Binary sniff first, since a SQLite database is not decodable as text and
    reading one as UTF-8 would fail or produce nonsense before any extension
    check got a say. Then dispatch by extension, and for unknown/missing
    extensions sniff the first non-space byte ('{' -> JSON, else CSV).
    """
    if _is_sqlite(path):
        return parse_meshcore_db(path, since_days), "meshcore-app-db"
    suffix = path.suffix.lower()
    if suffix == ".json":
        return parse_offline_json(path), "meshcore-offline-json"
    if suffix in (".csv", ".txt"):
        return parse_meshmapper_csv(path), "meshmapper-csv"
    head = path.read_text(encoding="utf-8", errors="replace").lstrip()[:1]
    if head == "{":
        return parse_offline_json(path), "meshcore-offline-json"
    return parse_meshmapper_csv(path), "meshmapper-csv"


# ---------------------------------------------------------------------------
# Upload envelope + POST
# ---------------------------------------------------------------------------

def build_envelope(nodes: list[dict[str, Any]], api_key: str) -> dict[str, str]:
    payload = {"networks": [], "aircraft": [], "meshcore_nodes": nodes}
    body_json = json.dumps(payload, separators=(",", ":"))
    data_b64 = base64.b64encode(body_json.encode()).decode()
    nonce = secrets.token_hex(8)
    sig = hmac.new(
        api_key.encode(),
        (nonce + data_b64).encode(),
        hashlib.sha256,
    ).hexdigest()
    return {"data": data_b64, "nonce": nonce, "sig": sig}


def chunked(seq: list[Any], size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def upload(
    nodes: list[dict[str, Any]],
    api_key: str,
    endpoint: str = DEFAULT_ENDPOINT,
    dry_run: bool = False,
) -> list[tuple[int, str]]:
    results = []
    for chunk in chunked(nodes, BATCH_SIZE):
        envelope = build_envelope(chunk, api_key)
        body = json.dumps(envelope).encode()
        if dry_run:
            results.append((0, f"dry-run: {len(chunk)} nodes, sig={envelope['sig'][:12]}..."))
            continue
        req = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-API-Key": api_key,
                "Accept": "application/json",
                "User-Agent": f"heimdall/{__version__}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
                results.append((resp.status, resp.read().decode(errors="replace")))
        except urllib.error.HTTPError as e:
            results.append((e.code, e.read().decode(errors="replace")))
    return results


# ---------------------------------------------------------------------------
# Daily version check + self-update
# ---------------------------------------------------------------------------

def _version_tuple(v: str) -> tuple[int, ...] | None:
    """Parse a dotted version string like '0.4.0' into an int tuple for
    ordering. Returns None if any component isn't a plain integer."""
    try:
        return tuple(int(p) for p in v.split("."))
    except ValueError:
        return None


def _is_newer(latest: str, current: str) -> bool:
    """True only if `latest` is a well-formed dotted version strictly
    greater than `current`. A malformed tag never triggers the notice,
    silently skipping beats wrongly telling someone on v0.4.0 to "upgrade"
    to v0.3.0, which is what a plain `latest != current` check does the
    moment GitHub's "latest release" isn't the highest version (issue #9)."""
    lt, ct = _version_tuple(latest), _version_tuple(current)
    return lt is not None and ct is not None and lt > ct


def _check_for_update(force: bool = False) -> str | None:
    """Quick non-blocking version check against the GitHub releases API.

    Only ever reached from --check-version or --update: nothing calls this on
    an ordinary run. Cached for 24h in the user's config dir so repeated
    --update attempts do not hammer the API; `force` skips the cache so an
    operator who explicitly asked gets a fresh answer rather than a stale one.
    Returns the latest tag if newer than __version__, else None."""
    cache = _config_dir() / "version-check.json"
    try:
        if cache.exists() and not force:
            blob = json.loads(cache.read_text())
            if time.time() - blob.get("checked_at", 0) < 86400:
                latest = blob.get("latest")
                return latest if latest and _is_newer(latest, __version__) else None
    except Exception:
        pass
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            headers={"User-Agent": f"heimdall/{__version__}"})
        with urllib.request.urlopen(req, timeout=3, context=_SSL_CTX) as r:
            data = json.loads(r.read())
            latest = (data.get("tag_name") or "").lstrip("v")
    except Exception:
        return None
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"checked_at": time.time(), "latest": latest}))
    except Exception:
        pass
    return latest if latest and _is_newer(latest, __version__) else None


def _run_update() -> int:
    """Try to update heimdall in place. Uses `git pull` if we are in a git
    checkout; otherwise falls back to fetching heimdall.py from raw GitHub.
    Either path also refreshes requirements.txt and runs pip install, so a
    future release that adds or bumps a dep doesn't leave the user with an
    updated heimdall.py importing a module they don't have."""
    import subprocess
    script_dir = Path(__file__).resolve().parent
    git_dir = script_dir / ".git"
    if git_dir.exists():
        print(f"[heimdall] updating via git pull in {script_dir}", file=sys.stderr)
        try:
            r = subprocess.run(["git", "-C", str(script_dir), "pull", "--ff-only"],
                               capture_output=True, text=True, timeout=30)
            print(r.stdout.strip(), file=sys.stderr)
            if r.returncode != 0:
                print(r.stderr.strip(), file=sys.stderr)
                return r.returncode
            _pip_install_requirements(script_dir)
            print(f"[heimdall] now on heimdall v{__version__} (re-run with "
                  f"--version to confirm latest)", file=sys.stderr)
            return 0
        except FileNotFoundError:
            print("[heimdall] git not found in PATH. Install git, or download "
                  "heimdall.py manually.", file=sys.stderr)
            return 1
    else:
        return _update_from_raw(script_dir)


def _fetch_raw(path: str, dest: Path) -> bool:
    """Fetch a file from the repo's main branch to dest atomically.
    Returns True on success, False on failure (logs the reason)."""
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{path}"
    print(f"[heimdall] fetching {path} from {raw_url}", file=sys.stderr)
    try:
        req = urllib.request.Request(raw_url, headers={
            "User-Agent": f"heimdall/{__version__}"})
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as r:
            body = r.read()
    except Exception as e:
        print(f"[heimdall] download of {path} failed: {e}", file=sys.stderr)
        return False
    tmp = dest.with_suffix(dest.suffix + ".new")
    try:
        tmp.write_bytes(body)
        os.replace(tmp, dest)
    except OSError as e:
        print(f"[heimdall] couldn't write {dest}: {e}", file=sys.stderr)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    return True


WRAPPER_SCRIPTS = ("run.sh", "run.bat", "setup.sh", "setup.bat",
                   "update.sh", "update.bat")


def _refresh_wrappers(script_dir: Path) -> None:
    """Refresh the shell/batch wrapper scripts next to heimdall.py.

    ZIP-installed users only receive wrapper fixes through --update (git
    checkouts get them via `git pull`), so the raw-update path must ship
    them too. The list is hard-coded rather than fetched from a remote
    manifest so the update path can never be steered into writing
    arbitrary filenames. A wrapper that fails to download is skipped
    with a warning. The heimdall.py update is never rolled back over a
    wrapper. Wrappers the user deleted are respected and not re-planted.
    """
    for name in WRAPPER_SCRIPTS:
        dest = script_dir / name
        if not dest.exists():
            continue
        if not _fetch_raw(name, dest):
            print(f"[heimdall] wrapper {name} not refreshed; re-download "
                  f"the release archive if it stays broken.", file=sys.stderr)
            continue
        if os.name == "posix" and name.endswith(".sh"):
            try:
                # Owner-only exec: the wrapper belongs to the operator who
                # installed the tool; granting group/other nothing keeps
                # SonarCloud S2612 quiet and loses no functionality.
                os.chmod(dest, 0o700)
            except OSError:
                pass


def _pip_install_requirements(script_dir: Path) -> None:
    """Best-effort `python -m pip install -r requirements.txt` against the
    interpreter currently running heimdall. Never fails the caller, prints
    a clear hint if pip is missing or the install errors out, so the update
    return code still reflects the heimdall.py update itself.

    Heimdall has no third-party deps today (requirements.txt is a
    comment-only placeholder), so this is a no-op in practice. The helper
    is here so future releases that add a dep self-heal without needing
    another wrapper-script revision."""
    import subprocess
    req = script_dir / "requirements.txt"
    if not req.exists():
        return
    # Skip entirely if requirements.txt has no actual install lines,
    # avoids printing a misleading "installing deps" banner when there's
    # nothing to install.
    has_deps = any(
        line.strip() and not line.lstrip().startswith("#")
        for line in req.read_text(encoding="utf-8", errors="replace").splitlines()
    )
    if not has_deps:
        return
    print(f"[heimdall] installing/refreshing deps from {req.name} "
          f"(python -m pip install --upgrade -r requirements.txt)", file=sys.stderr)
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade",
                            "-r", str(req)], timeout=300)
    except FileNotFoundError:
        print("[heimdall] python not found to invoke pip; run "
              "`python -m pip install -r requirements.txt` manually.",
              file=sys.stderr)
        return
    except subprocess.TimeoutExpired:
        print("[heimdall] pip install timed out; run "
              "`python -m pip install -r requirements.txt` manually.",
              file=sys.stderr)
        return
    if r.returncode != 0:
        print(f"[heimdall] pip install exited {r.returncode}; if the import "
              f"errors below mention a missing module, run "
              f"`python -m pip install -r requirements.txt` manually.",
              file=sys.stderr)


def _update_from_raw(script_dir: Path) -> int:
    """Non-git fallback for --update: fetch heimdall.py + requirements.txt
    from raw GitHub and replace the local files atomically, then refresh
    deps. Works for ZIP-downloaded installs."""
    target = script_dir / "heimdall.py"
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/heimdall.py"
    print(f"[heimdall] not a git checkout. Fetching latest heimdall.py from "
          f"{raw_url}", file=sys.stderr)
    try:
        req = urllib.request.Request(raw_url, headers={
            "User-Agent": f"heimdall/{__version__}"})
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as r:
            new_text = r.read().decode("utf-8")
    except Exception as e:
        print(f"[heimdall] download failed: {e}", file=sys.stderr)
        print(f"[heimdall] manual download: "
              f"https://github.com/{GITHUB_REPO}/releases/latest", file=sys.stderr)
        return 1
    try:
        import ast
        ast.parse(new_text)
    except SyntaxError as e:
        print(f"[heimdall] downloaded file failed to parse, aborting: {e}",
              file=sys.stderr)
        return 1
    import re as _re
    m = _re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']',
                   new_text, _re.MULTILINE)
    new_version = m.group(1) if m else "?"
    if new_version == __version__:
        print(f"[heimdall] already on the latest (v{__version__}). Refreshing "
              f"requirements.txt and wrappers in case a pinned dep or "
              f"wrapper fix moved.", file=sys.stderr)
        _fetch_raw("requirements.txt", script_dir / "requirements.txt")
        _refresh_wrappers(script_dir)
        _pip_install_requirements(script_dir)
        return 0
    tmp = target.with_suffix(".py.new")
    try:
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, target)
    except OSError as e:
        print(f"[heimdall] couldn't write {target}: {e}", file=sys.stderr)
        try:
            tmp.unlink()
        except OSError:
            pass
        return 1
    print(f"[heimdall] updated v{__version__} to v{new_version}", file=sys.stderr)
    _fetch_raw("requirements.txt", script_dir / "requirements.txt")
    _refresh_wrappers(script_dir)
    _pip_install_requirements(script_dir)
    print(f"[heimdall] re-run heimdall to pick up the new code "
          f"(the current process is still running the old version).",
          file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# Scheduler: install/remove a daily timer on systemd / cron / schtasks
# ---------------------------------------------------------------------------
#
# Mechanism per OS, matching wigle-to-wdgwars exactly:
#   Linux with systemd  → user systemd units in ~/.config/systemd/user/
#                         (timer + service, OnCalendar daily at HH:MM)
#   Linux without systemd, macOS → user crontab
#   Windows             → schtasks /Create /SC DAILY /ST HH:MM
#
# Heimdall, unlike wigle, has no pull-from-source flavour. The schedule
# must point at a CSV file the user keeps refreshing (e.g. their nightly
# MeshMapper export). --schedule-csv is the required input for the
# headless install; the wizard variant prompts for it.
#
# A "# managed-by-heimdall" marker comment goes into every artifact so
# --unschedule can find and remove them cleanly without touching the
# user's own crontab/systemd unit dir entries.

def _python_exe() -> str:
    return sys.executable


def _script_path() -> Path:
    return Path(__file__).resolve()


def _systemd_user_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user"


def _has_systemd() -> bool:
    """True only on a Linux host that actually runs systemd as PID 1 and
    has systemctl on PATH. Avoids the WSL false-positive where systemctl
    is installed but `/run/systemd/system` is absent."""
    if not sys.platform.startswith("linux"):
        return False
    if shutil.which("systemctl") is None:
        return False
    return Path("/run/systemd/system").exists()


def _schedule_mechanism() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux") and _has_systemd():
        return "systemd"
    return "cron"


def _validate_hhmm(s: str) -> str:
    """Parse 'HH:MM' (24h). Returns canonical 'HH:MM' or raises ValueError."""
    parts = s.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"time must be HH:MM, got {s!r}")
    hh = int(parts[0])
    mm = int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"time out of range: {s!r}")
    return f"{hh:02d}:{mm:02d}"


def _shell_quote(s: str) -> str:
    """Minimal POSIX shell quoting for systemd ExecStart and cron lines."""
    if not s:
        return "''"
    safe = ("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789@%_-+=:,./")
    if all(c in safe for c in s):
        return s
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _schedule_argv(csv_path: Path) -> list[str]:
    """Build the heimdall argv that the scheduler will run.

    Always reads the saved key from disk (no --key on the command line, that
    would leak the secret into the unit file / crontab / schtasks output,
    all of which are readable by other processes on the box).
    """
    return [_python_exe(), str(_script_path()), str(csv_path)]


# ── Pure renderers (no side effects, easy to unit-test) ─────────────────────

def render_systemd_units(time_hhmm: str, csv_path: Path,
                         python_exe: str, script_path: Path,
                         dry_run: bool = False) -> dict[str, str]:
    """Render (service, timer) unit text. Pure. Does not touch disk."""
    time_hhmm = _validate_hhmm(time_hhmm)
    argv = [python_exe, str(script_path), str(csv_path)]
    if dry_run:
        argv.append("--dry-run")
    exec_start = " ".join(_shell_quote(a) for a in argv)
    desc_suffix = " [DRY-RUN]" if dry_run else ""
    service = (
        "[Unit]\n"
        f"Description=Heimdall daily MeshCore push{desc_suffix}\n"
        f"# {SCHEDULE_MARKER}\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={exec_start}\n"
    )
    timer = (
        "[Unit]\n"
        f"Description=Run heimdall daily at {time_hhmm}\n"
        f"# {SCHEDULE_MARKER}\n"
        "\n"
        "[Timer]\n"
        f"OnCalendar=*-*-* {time_hhmm}:00\n"
        "Persistent=true\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return {"service": service, "timer": timer}


def render_cron_line(time_hhmm: str, csv_path: Path,
                     python_exe: str, script_path: Path,
                     dry_run: bool = False) -> str:
    """Render the cron line for the daily run. Pure."""
    time_hhmm = _validate_hhmm(time_hhmm)
    hh, mm = time_hhmm.split(":")
    argv = [python_exe, str(script_path), str(csv_path)]
    if dry_run:
        argv.append("--dry-run")
    cmd = " ".join(_shell_quote(a) for a in argv)
    log = "$HOME/.heimdall-cron.log"
    return (f"{int(mm)} {int(hh)} * * * {cmd} "
            f">> {log} 2>&1  # {SCHEDULE_MARKER}\n")


def render_schtasks_create(time_hhmm: str, csv_path: Path,
                           python_exe: str, script_path: Path,
                           dry_run: bool = False) -> list[str]:
    """Render the `schtasks /Create` argv for Windows. Pure.

    No `cmd /c "... >> log 2>&1"` wrap: schtasks /TR hard-caps the action
    string at 261 characters and the wrap form blows past that once the
    venv-python + script + CSV paths are included. Users see daily-run
    outcome via Task Scheduler's "Last Result" column instead, or by
    firing the same command from PowerShell to inspect stderr.
    """
    time_hhmm = _validate_hhmm(time_hhmm)
    argv = [python_exe, str(script_path), str(csv_path)]
    if dry_run:
        argv.append("--dry-run")
    action = " ".join(f'"{a}"' if " " in a else a for a in argv)
    return ["schtasks", "/Create", "/TN", WINDOWS_TASK_NAME,
            "/TR", action, "/SC", "DAILY", "/ST", time_hhmm,
            "/RL", "LIMITED", "/F"]


# ── Installers ──────────────────────────────────────────────────────────────

def install_systemd_user(time_hhmm: str, csv_path: Path,
                         dry_run: bool = False) -> int:
    units = render_systemd_units(time_hhmm, csv_path,
                                 _python_exe(), _script_path(),
                                 dry_run=dry_run)
    unit_dir = _systemd_user_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    service_path = unit_dir / f"{SYSTEMD_UNIT_NAME}.service"
    timer_path = unit_dir / f"{SYSTEMD_UNIT_NAME}.timer"
    service_path.write_text(units["service"])
    print(f"[schedule] wrote {service_path}", file=sys.stderr)
    timer_path.write_text(units["timer"])
    print(f"[schedule] wrote {timer_path}", file=sys.stderr)
    target = f"{SYSTEMD_UNIT_NAME}.timer"
    for cmd in (["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", "--now", target]):
        rc = subprocess.call(cmd)
        if rc != 0:
            print(f"[schedule] '{' '.join(cmd)}' returned {rc}",
                  file=sys.stderr)
            return rc
    print(f"[schedule] enabled and started {target}", file=sys.stderr)
    print(f"[schedule] status:  systemctl --user status {target}",
          file=sys.stderr)
    print(f"[schedule] logs:    journalctl --user -u {target} -f",
          file=sys.stderr)
    return 0


def uninstall_systemd_user() -> int:
    unit_dir = _systemd_user_dir()
    found = False
    for name in (f"{SYSTEMD_UNIT_NAME}.timer",
                 f"{SYSTEMD_UNIT_NAME}.service"):
        unit = unit_dir / name
        if unit.exists():
            found = True
            subprocess.call(["systemctl", "--user", "stop", name],
                            stderr=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL)
            subprocess.call(["systemctl", "--user", "disable", name],
                            stderr=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL)
            unit.unlink()
            print(f"[schedule] removed {unit}", file=sys.stderr)
    if found:
        subprocess.call(["systemctl", "--user", "daemon-reload"])
    else:
        print("[schedule] no heimdall systemd units found", file=sys.stderr)
    return 0


def install_cron(time_hhmm: str, csv_path: Path,
                 dry_run: bool = False) -> int:
    if shutil.which("crontab") is None:
        print("[schedule] crontab not found on PATH", file=sys.stderr)
        return 1
    new_line = render_cron_line(time_hhmm, csv_path,
                                _python_exe(), _script_path(),
                                dry_run=dry_run)
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        current = r.stdout if r.returncode == 0 else ""
    except FileNotFoundError:
        return 1
    cleaned = "\n".join(l for l in current.splitlines()
                        if SCHEDULE_MARKER not in l)
    combined = (cleaned.rstrip() + "\n" + new_line) if cleaned.strip() else new_line
    proc = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
    proc.communicate(combined)
    if proc.returncode != 0:
        print(f"[schedule] crontab write failed (rc={proc.returncode})",
              file=sys.stderr)
        return proc.returncode
    print(f"[schedule] added cron entry (marker: {SCHEDULE_MARKER})",
          file=sys.stderr)
    print(f"[schedule] view: crontab -l", file=sys.stderr)
    print(f"[schedule] log:  tail -f ~/.heimdall-cron.log", file=sys.stderr)
    return 0


def uninstall_cron() -> int:
    if shutil.which("crontab") is None:
        return 0
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if r.returncode != 0:
            return 0
        current = r.stdout
    except FileNotFoundError:
        return 0
    cleaned = "\n".join(l for l in current.splitlines()
                        if SCHEDULE_MARKER not in l)
    if cleaned == current.rstrip("\n"):
        print("[schedule] no heimdall cron entries found", file=sys.stderr)
        return 0
    proc = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
    proc.communicate(cleaned)
    print("[schedule] removed heimdall cron entries", file=sys.stderr)
    return 0


def install_windows_task(time_hhmm: str, csv_path: Path,
                         dry_run: bool = False) -> int:
    cmd = render_schtasks_create(time_hhmm, csv_path,
                                 _python_exe(), _script_path(),
                                 dry_run=dry_run)
    rc = subprocess.call(cmd)
    if rc != 0:
        return rc
    print(f"[schedule] created task: {WINDOWS_TASK_NAME}", file=sys.stderr)
    print(f"[schedule] view:    schtasks /Query /TN {WINDOWS_TASK_NAME}",
          file=sys.stderr)
    print(f"[schedule] run now: schtasks /Run /TN {WINDOWS_TASK_NAME}",
          file=sys.stderr)
    print(f"[schedule] (Task Scheduler doesn't capture stdout, to see "
          f"what a run did, fire it from PowerShell directly.)",
          file=sys.stderr)
    return 0


def uninstall_windows_task() -> int:
    rc = subprocess.call(["schtasks", "/Delete", "/TN", WINDOWS_TASK_NAME,
                          "/F"], stderr=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL)
    if rc == 0:
        print(f"[schedule] removed scheduled task: {WINDOWS_TASK_NAME}",
              file=sys.stderr)
    else:
        print(f"[schedule] no scheduled task named {WINDOWS_TASK_NAME} found",
              file=sys.stderr)
    return 0


def cmd_schedule_headless(args) -> int:
    """Headless --schedule path. Reads time + CSV path + dry-run from args."""
    if not args.schedule_csv:
        sys.exit("--schedule needs --schedule-csv PATH (the CSV file to "
                 "upload daily). Heimdall has no pull-from-source flavour, "
                 "so the scheduler must point at a file you keep refreshing.")
    csv_path = _user_path(args.schedule_csv, label="--schedule-csv")
    time_hhmm = _validate_hhmm(args.schedule_time or DEFAULT_SCHEDULE_TIME)
    dry_run = bool(args.schedule_dry_run)
    if not _key_path().exists() and not os.environ.get("WDGWARS_API_KEY"):
        sys.exit("--schedule needs a saved WDGWars API key (run --setup "
                 "first), or set WDGWARS_API_KEY in the environment the "
                 "scheduler will run under.")
    mech = _schedule_mechanism()
    if mech == "systemd":
        return install_systemd_user(time_hhmm, csv_path, dry_run=dry_run)
    if mech == "cron":
        return install_cron(time_hhmm, csv_path, dry_run=dry_run)
    if mech == "windows":
        return install_windows_task(time_hhmm, csv_path, dry_run=dry_run)
    sys.exit(f"unsupported platform for --schedule: {sys.platform}")


def cmd_unschedule() -> int:
    """Remove every heimdall-managed schedule entry on this platform."""
    rcs = []
    if sys.platform == "win32":
        rcs.append(uninstall_windows_task())
    else:
        if _has_systemd():
            rcs.append(uninstall_systemd_user())
        rcs.append(uninstall_cron())
    return 0 if all(rc == 0 for rc in rcs) else 1


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=f"Heimdall v{__version__}, MeshMapper CSV to WDGWars "
                    f"meshcore_nodes uplink.",
    )
    p.add_argument("--version", action="version",
                   version=f"heimdall {__version__}")
    p.add_argument("--update", action="store_true",
                   help="pull the latest version of heimdall (uses git pull if "
                        "you cloned the repo, otherwise downloads heimdall.py "
                        "from GitHub)")
    p.add_argument("csv", nargs="?", type=Path, metavar="capture",
                   help="MeshMapper CSV export (flat or multi-section TX/RX/"
                        "DISC) or a MeshCore offline ping-log JSON. Format is "
                        "auto-detected. Not required for --setup, --save-key, "
                        "--whoami, or --update.")
    p.add_argument("--setup", action="store_true",
                   help="interactive first-time setup, prompts for your "
                        "WDGWars API key, validates it, and saves it locally.")
    p.add_argument("--save-key", metavar="KEY",
                   help="non-interactive: save the given API key to the user "
                        "config dir. Prefer --setup for first-time install.")
    p.add_argument("--whoami", action="store_true",
                   help="validate your stored API key by hitting /endpoint/me and "
                        "showing account stats; exits after.")
    # --key is the canonical name (matches Muninn + wigle-to-wdgwars).
    # --api-key is the legacy name; kept as a deprecated alias. Removal
    # was slated for v0.4 but deliberately slipped. Drop it in the next
    # major once the operator confirms no schedulers still pass it. The
    # actual value lands on args.key after the merge below.
    p.add_argument("--key", help="WDGWars API key (or set WDGWARS_API_KEY, "
                                 "or run --setup once to save it)")
    p.add_argument("--api-key", dest="api_key_legacy",
                   help=argparse.SUPPRESS)  # deprecated alias for --key
    # --api-url is the canonical name (matches Muninn). --endpoint is the
    # legacy name; same deprecation treatment as --api-key.
    p.add_argument("--api-url", default=None,
                   help=f"override upload URL (default: {DEFAULT_ENDPOINT})")
    p.add_argument("--endpoint", dest="endpoint_legacy", default=None,
                   help=argparse.SUPPRESS)  # deprecated alias for --api-url
    p.add_argument("--since-days", type=float, default=None, metavar="N",
                   help="MeshCore app database only: skip nodes not heard in "
                        "the last N days (the database is all-time)")
    p.add_argument("--dry-run", action="store_true",
                   help="build the HMAC-signed envelope but do not POST")
    p.add_argument("--preview", action="store_true",
                   help="print first 6 normalised rows as JSON and exit")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="suppress informational banners (errors still print)")
    p.add_argument("--check-version", action="store_true",
                   help="ask GitHub whether a newer release exists, then exit "
                        "(the only thing here that contacts GitHub on its own)")
    p.add_argument("--no-version-check", action="store_true",
                   help=argparse.SUPPRESS)  # accepted for compatibility; no
    # automatic check happens any more, so this is a no-op. Existing cron
    # lines, systemd units and schtasks actions carry it, and erroring on an
    # unknown argument would break a working scheduled upload.
    # ── Scheduler flags ──
    p.add_argument("--schedule", action="store_true",
                   help="install a daily scheduled upload (systemd / cron / "
                        "schtasks per OS). Pairs with --schedule-csv. "
                        "Headless with --schedule-time / --schedule-dry-run.")
    p.add_argument("--unschedule", action="store_true",
                   help="remove every heimdall-managed scheduled task "
                        "on this host.")
    p.add_argument("--schedule-csv", metavar="PATH",
                   help="path to the CSV that should be uploaded daily. "
                        "Required for --schedule.")
    p.add_argument("--schedule-time", metavar="HH:MM",
                   help=f"24-hour daily run time for --schedule "
                        f"(default: {DEFAULT_SCHEDULE_TIME})")
    p.add_argument("--schedule-dry-run", action="store_true",
                   help="install the schedule with --dry-run baked in. "
                        "Parses + signs but never POSTs. Re-run --schedule "
                        "without this flag to go live.")
    args = p.parse_args(argv)

    # ── Deprecated-flag back-compat ──
    # If the user passed --api-key, hoist it onto args.key (with a warning).
    # Same for --endpoint -> --api-url. Both old names go in the next major.
    if args.api_key_legacy is not None:
        if args.key is None:
            args.key = args.api_key_legacy
        elif args.key != args.api_key_legacy:
            sys.exit("--key and --api-key both given with different values; "
                     "use --key only (--api-key is deprecated).")
        if not args.quiet:
            print("[heimdall] note: --api-key is deprecated, use --key. "
                  "The old name still works for now but will be removed.",
                  file=sys.stderr)
    if args.endpoint_legacy is not None:
        if args.api_url is None:
            args.api_url = args.endpoint_legacy
        elif args.api_url != args.endpoint_legacy:
            sys.exit("--api-url and --endpoint both given with different "
                     "values; use --api-url only (--endpoint is deprecated).")
        if not args.quiet:
            print("[heimdall] note: --endpoint is deprecated, use --api-url. "
                  "The old name still works for now but will be removed.",
                  file=sys.stderr)
    if args.api_url is None:
        args.api_url = DEFAULT_ENDPOINT

    # --update is a top-level mode, run before anything that needs a key/file.
    if args.update:
        return _run_update()

    # Schedule mutation modes. Don't need a key in process but the
    # installed unit will need one at run-time. cmd_schedule_headless
    # checks for a saved key and exits early if absent.
    if args.unschedule:
        return cmd_unschedule()
    if args.schedule:
        return cmd_schedule_headless(args)

    # Version check is explicit-only. It used to run on every invocation with
    # --quiet / --no-version-check as the opt-out, which disclosed the user's
    # IP, their exact version and a rough daily usage cadence to GitHub
    # without ever asking. Nothing here talks to a third party unless the
    # operator typed a command that says so.
    if args.check_version:
        newer = _check_for_update(force=True)
        if newer:
            print(f"[heimdall] v{newer} is available "
                  f"(you're on v{__version__}). Run `--update` to upgrade.",
                  file=sys.stderr)
        else:
            print(f"[heimdall] v{__version__} is current.", file=sys.stderr)
        return 0

    # Key management modes, handled before requiring an input file.
    if args.setup:
        return interactive_setup()
    if args.save_key:
        save_key(args.save_key)
        return 0
    if args.whoami:
        key = load_key(args.key)
        if not key:
            print("no API key found, run `python3 heimdall.py --setup` "
                  "for first-time setup", file=sys.stderr)
            return 2
        return check_whoami(key)

    # From here on we need a CSV.
    if args.csv is None:
        p.error("the following arguments are required: csv "
                "(or use --setup / --save-key / --whoami / --update / "
                "--schedule / --unschedule)")

    # Canonicalise the untrusted argv path once, at the boundary, before any
    # filesystem access (pythonsecurity:S8707). Downstream parsers then read a
    # validated path. _UnsafeInput (a ValueError) is caught below.
    try:
        capture = _user_path(str(args.csv), label="capture")
    except _UnsafeInput as e:
        print(f"refusing capture path: {e}", file=sys.stderr)
        return 2

    if not capture.is_file():
        print(f"file not found: {args.csv}", file=sys.stderr)
        return 2

    try:
        nodes, fmt = parse_file(capture, args.since_days)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"could not parse {capture.name}: {e}", file=sys.stderr)
        return 1
    if args.since_days is not None and fmt != "meshcore-app-db":
        print(f"[heimdall] heads-up: --since-days only filters a MeshCore app "
              f"database; {capture.name} parsed as {fmt} and was not filtered.",
              file=sys.stderr)
    print(f"parsed {len(nodes)} meshcore nodes from {args.csv.name} ({fmt})")
    if not nodes:
        print("nothing to upload", file=sys.stderr)
        return 1
    for line in flag_filler_ids(nodes):
        print(f"[heimdall] heads-up: {line}", file=sys.stderr)
    nodes, collapsed = collapse_repeat_sightings(nodes)
    if collapsed:
        print(f"[heimdall] collapsed {collapsed} repeat sighting(s) of the "
              f"same node_id (first sighting wins, matching the server's "
              f"dedupe); {len(nodes)} unique nodes remain.", file=sys.stderr)
    for line in predict_server_rejects(nodes):
        print(f"[heimdall] heads-up: {line}", file=sys.stderr)

    if args.preview:
        for row in nodes[:6]:
            print(json.dumps(row))
        return 0

    # After --preview, so a preview always shows the whole capture, and
    # before the key is resolved, so a run with nothing new to say costs
    # nothing at all. Skipped for --dry-run: a dry run reports what WOULD
    # be sent, which is not the same question.
    if not args.dry_run:
        nodes, held_back = filter_already_sent(nodes, time.time())
        if held_back:
            if not nodes:
                print(f"[heimdall] nothing new to send: all {held_back} "
                      f"node(s) are already on your account. Skipping "
                      f"upload.", file=sys.stderr)
                return 0
            print(f"[heimdall] {held_back} node(s) already on your account, "
                  f"sending {len(nodes)}.", file=sys.stderr)

    key = load_key(args.key)
    if not key:
        print("missing API key: pass --key, set WDGWARS_API_KEY, or run "
              "`python3 heimdall.py --setup` once to save it", file=sys.stderr)
        return 2

    rc = 0
    # Cross-check the server's arithmetic: every submitted node should come
    # back as imported, already seen, or rejected. wdgwars.pl has been seen
    # returning all-zero counters for a payload it itemised as rejected
    # moments earlier (issue #1, second identical run), and Heimdall keeps
    # no state between runs, so make that silence visible instead of letting
    # it read as a clean upload.
    # None means "no counters to audit" (dry-run, or a body we couldn't parse).
    # Ported by hand from gungnir.diagnostics (v0.1.4) while reconciling
    # against v0.4.1. The server answers a payload it has already taken
    # with 200, ok:true, every counter zero and an explanation in the
    # clear. gungnir's consumers had that misread as a silent drop and
    # failed the upload; Heimdall never had that bug, having no
    # silent-drop detector, but it WOULD print its "gave no verdict" note
    # and tell the operator the server refused to account for their
    # nodes. It accounted for them on an earlier push.
    deliberate_skip: str | None = None
    accounted: int | None = 0
    # Same None-means-unknown discipline as `accounted`, for the holds gate:
    # a day-long hold is only earned by a server that actually said it
    # imported nothing, never by a total we failed to read.
    imported_total: int | None = 0
    sent_at = time.time()
    for status, body in upload(nodes, key, endpoint=args.api_url, dry_run=args.dry_run):
        if status == 0:
            print(f"{_INFO()} {body}", file=sys.stderr)
            accounted = None
            imported_total = None
            continue
        if 200 <= status < 300:
            try:
                data = json.loads(body)
                deliberate_skip = deliberate_skip or _deliberate_skip(data)
                imp = data.get("meshcore_imported", 0)
                seen = data.get("meshcore_already_seen", 0)
                rejected = data.get("meshcore_rejected", 0)
                reasons = data.get("meshcore_reject_reasons") or {}
                badges = data.get("new_badges") or []
                if accounted is not None:
                    accounted += imp + seen + rejected
                if imported_total is not None:
                    imported_total += imp
                print(f"{_OK()} accepted by wdgwars.pl. "
                      f"{imp} new meshcore nodes, {seen} already on your account.",
                      file=sys.stderr)
                if rejected:
                    print(f"  {rejected} rejected: {reasons}", file=sys.stderr)
                if badges:
                    print(f"  new badges: {badges}", file=sys.stderr)
            except Exception:
                accounted = None
                imported_total = None
                print(f"{_OK()} accepted by wdgwars.pl (HTTP {status}): "
                      f"{_scrub(body[:200], key)}", file=sys.stderr)
        else:
            data: dict = {}
            try:
                data = json.loads(body)
            except Exception:
                pass
            if status == 413 and isinstance(data, dict) and data.get("error") == "payload-too-large":
                max_b = data.get("max_bytes")
                recv = data.get("received")
                print(
                    f"{_FAIL()} 413 payload-too-large from wdgwars.pl "
                    f"(max_bytes={max_b} received={recv}). LOCOSP added a "
                    f"15 MB upload cap on 2026-06-05; mesh-node payloads are "
                    f"normally well under it, so this is unexpected. Drop the "
                    f"batch size or wait for the next cycle.",
                    file=sys.stderr,
                )
            else:
                print(f"{_FAIL()} rejected by wdgwars.pl (HTTP {status}): "
                      f"{_scrub(body[:200], key)}", file=sys.stderr)
            accounted = None
            imported_total = None
            rc = 1
    if deliberate_skip and accounted == 0:
        print(f"{_INFO()} the server had already taken this payload: "
              f"{deliberate_skip} Nothing was lost and there is nothing to "
              f"fix.", file=sys.stderr)
    elif accounted is not None and accounted < len(nodes):
        print(f"[heimdall] note: the server's counters account for "
              f"{accounted} of the {len(nodes)} submitted nodes (imported + "
              f"already seen + rejected), and gave no verdict for the other "
              f"{len(nodes) - accounted}. Seen live when re-submitting a "
              f"payload it had just itemised as rejected (issue #1); the "
              f"unaccounted nodes were NOT imported.", file=sys.stderr)
    if rc == 0 and not args.dry_run:
        record_sent_nodes(nodes, sent_at, imported_total)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
