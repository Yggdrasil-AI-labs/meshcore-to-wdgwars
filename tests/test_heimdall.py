"""Smoke tests for Heimdall v0.1.0.

Run: python -m unittest tests/test_heimdall.py
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import builtins
import json
import sqlite3
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import heimdall  # noqa: E402


SAMPLE = """timestamp,repeater_id,snr,rssi,path_length,header,latitude,longitude,path_hops
2026-05-21T13:20:11.520125,E4,5.75,-111,8,0x15,0.0,0.0,29|1F|60|AE|08|77|79|E4
2026-05-21T13:12:07.643099,19,-1.75,-117,11,0x11,0.0,0.0,37|AB|54|61|60|AE|98|31|47|A1|19
2026-05-21T13:16:18.777612,3023,-3.25,-119,4,0x11,0.0,0.0,FBC2|014C|9891|3023
"""


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        )
        self.tmp.write(SAMPLE)
        self.tmp.close()
        self.path = Path(self.tmp.name)

    def test_parses_three_rows(self):
        rows = heimdall.parse_meshmapper_csv(self.path)
        self.assertEqual(len(rows), 3)

    def test_field_mapping(self):
        rows = heimdall.parse_meshmapper_csv(self.path)
        first = rows[0]
        self.assertEqual(first["first_seen"], "2026-05-21 13:20:11")
        self.assertEqual(first["node_id"], "e4")  # lower-cased for wdgwars.pl's gate
        self.assertEqual(first["node_type"], "REPEATER")
        self.assertEqual(first["type"], "MESHCORE")
        self.assertEqual(first["name"], "E4")  # falls back to node_id, original casing
        self.assertEqual(first["lat"], 0.0)
        self.assertEqual(first["lon"], 0.0)
        self.assertEqual(first["rssi"], -111.0)
        self.assertNotIn("snr", first)  # dropped from the wire record

    def test_numeric_coercion(self):
        rows = heimdall.parse_meshmapper_csv(self.path)
        for r in rows:
            self.assertIsInstance(r["lat"], float)
            self.assertIsInstance(r["lon"], float)
            self.assertIsInstance(r["rssi"], float)

    def test_variable_width_node_ids(self):
        rows = heimdall.parse_meshmapper_csv(self.path)
        ids = [r["node_id"] for r in rows]
        self.assertIn("e4", ids)       # 2-hex
        self.assertIn("19", ids)       # 2-hex
        self.assertIn("3023", ids)     # 4-hex

    def test_skips_malformed_row(self):
        path = Path(self.tmp.name).with_suffix(".bad.csv")
        path.write_text(
            "timestamp,repeater_id,snr,rssi,path_length,header,latitude,longitude,path_hops\n"
            "good,abc,1,2,3,4,5,6,7\n"
            ",noid,1,2,3,4,5,6,7\n"            # missing timestamp -> dropped
            "good2,xyz,not-a-float,2,3,4,5,6,7\n"  # bad numeric -> dropped
        )
        rows = heimdall.parse_meshmapper_csv(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["node_id"], "abc")


# A real MeshMapper multi-section export (TX + DISC), scrubbed lat/lon.
SECTIONED_CSV = """--- TX Log ---
timestamp,latitude,longitude,power,events
2026-06-27T11:37:20.859937,0.0,0.0,0.6,0CE8(-0.25)

--- DISC Log ---
timestamp,latitude,longitude,noisefloor,node_count,nodes
2026-06-27T11:36:48.792735,0.0,0.0,-99,2,910E(R)(-6.00),0CE8(R)(1.25)
"""

# A MeshCore offline ping-log JSON: one DISC (full telemetry) + one RX (token).
OFFLINE_JSON = json.dumps({
    "offline": True,
    "created_at": "2026-06-26T07:09:24.525009",
    "ping_count": 2,
    "device_name": "EXAMPLE_NODE",
    "pings": [
        {"type": "DISC", "lat": 0.0, "lon": 0.0, "noisefloor": -99,
         "repeater_id": "0CE8", "node_type": "REPEATER",
         "local_snr": 2.25, "local_rssi": -98, "remote_snr": -11.25,
         "public_key": "0CE8FE", "timestamp": 1782450531, "power": "0.6w"},
        {"type": "RX", "lat": 0.0, "lon": 0.0, "noisefloor": -102,
         "heard_repeats": "FB03(-8.00)", "timestamp": 1782450685,
         "power": "0.6w"},
    ],
})


def _write_tmp(text: str, suffix: str) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False,
                                    encoding="utf-8")
    f.write(text)
    f.close()
    return Path(f.name)


class SectionedCsvTests(unittest.TestCase):
    def setUp(self):
        self.path = _write_tmp(SECTIONED_CSV, ".csv")

    def test_parses_tx_and_disc_nodes(self):
        rows = heimdall.parse_meshmapper_csv(self.path)
        # 1 TX event + 2 DISC nodes
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["node_id"] for r in rows], ["0ce8", "910e", "0ce8"])
        self.assertTrue(all("snr" not in r for r in rows))  # dropped from the wire record

    def test_disc_repeater_marker_maps_to_repeater(self):
        rows = heimdall.parse_meshmapper_csv(self.path)
        self.assertTrue(all(r["node_type"] == "REPEATER" for r in rows))
        self.assertTrue(all(r["type"] == "MESHCORE" for r in rows))

    def test_packed_csv_has_no_rssi(self):
        # TX/DISC sections carry no per-node RSSI, only a noise floor.
        rows = heimdall.parse_meshmapper_csv(self.path)
        self.assertTrue(all(r["rssi"] is None for r in rows))

    def test_dispatch_returns_csv_format(self):
        rows, fmt = heimdall.parse_file(self.path)
        self.assertEqual(fmt, "meshmapper-csv")
        self.assertEqual(len(rows), 3)


class OfflineJsonTests(unittest.TestCase):
    def setUp(self):
        self.path = _write_tmp(OFFLINE_JSON, ".json")

    def test_parses_disc_and_rx(self):
        rows = heimdall.parse_offline_json(self.path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["node_id"], "0ce8")
        self.assertEqual(rows[1]["node_id"], "fb03")

    def test_disc_carries_real_rssi(self):
        rows = heimdall.parse_offline_json(self.path)
        disc = rows[0]
        self.assertEqual(disc["rssi"], -98.0)
        self.assertNotIn("snr", disc)  # dropped from the wire record
        self.assertEqual(disc["node_type"], "REPEATER")
        self.assertEqual(disc["type"], "MESHCORE")

    def test_rx_token_has_no_rssi(self):
        rows = heimdall.parse_offline_json(self.path)
        rx = rows[1]
        self.assertNotIn("snr", rx)  # dropped from the wire record
        self.assertIsNone(rx["rssi"])

    def test_epoch_timestamp_becomes_first_seen(self):
        rows = heimdall.parse_offline_json(self.path)
        self.assertTrue(rows[0]["first_seen"].startswith("2026-"))
        self.assertNotIn("T", rows[0]["first_seen"])

    def test_dispatch_detects_json_by_extension(self):
        rows, fmt = heimdall.parse_file(self.path)
        self.assertEqual(fmt, "meshcore-offline-json")
        self.assertEqual(len(rows), 2)


# A capture whose DISC pings carry the node's full public key, plus an RX ping
# that names one of the same nodes by short ID only. Keys are real-shaped
# (64 hex, short ID as the leading digits) but not real nodes.
KEYED_JSON = json.dumps({
    "offline": True,
    "ping_count": 3,
    "device_name": "EXAMPLE_NODE",
    "device_public_key": "DEADBEEF" * 8,
    "pings": [
        {"type": "DISC", "lat": 1.5, "lon": 2.5, "repeater_id": "0CE8",
         "node_type": "REPEATER", "local_snr": 2.25, "local_rssi": -98,
         "public_key": "0CE8FE4FB8E5EA2C0A7B1A974DFBD604"
                       "BA8A63F68DA6A26F146C4BD1CEA1FFE6",
         "timestamp": 1782450531},
        {"type": "TRACE", "lat": 1.5, "lon": 2.5, "repeater_id": "EF8B",
         "public_key": "EF8B822C5059AA5C39713F2F6BA9A9C1"
                       "2E9F6701884C0D61880B2C7CE88449B7",
         "timestamp": 1782450676},
        {"type": "RX", "lat": 1.5, "lon": 2.5,
         "heard_repeats": "0CE8(-8.00),FB03(-9.00)",
         "timestamp": 1782450685},
    ],
})


class DerivedNodeIdTests(unittest.TestCase):
    """nicolasrata's fix (issue #1): take the node_id from the node's own
    public key so it clears wdgwars.pl's 8-16 hex gate."""

    KEY = "0CE8FE4FB8E5EA2C0A7B1A974DFBD604BA8A63F68DA6A26F146C4BD1CEA1FFE6"

    def test_derives_sixteen_hex_from_key(self):
        self.assertEqual(heimdall.derive_node_id(self.KEY, "0CE8"),
                         "0ce8fe4fb8e5ea2c")

    def test_derived_id_clears_the_server_gate(self):
        node_id = heimdall.derive_node_id(self.KEY, "0CE8").lower()
        self.assertRegex(node_id, heimdall._SERVER_NODE_ID_GATE)

    def test_key_not_matching_the_short_id_is_not_used(self):
        # A key paired with someone else's short ID would upload a confident
        # wrong identity, so the short ID stands and the server rejects it.
        self.assertEqual(heimdall.derive_node_id(self.KEY, "FB03"), "FB03")

    def test_short_or_nonhex_key_falls_back(self):
        self.assertEqual(heimdall.derive_node_id("0CE8FE", "0CE8"), "0CE8")
        self.assertEqual(heimdall.derive_node_id("not-hex-at-all!", "0CE8"),
                         "0CE8")
        self.assertEqual(heimdall.derive_node_id(None, "0CE8"), "0CE8")

    def test_key_with_no_short_id_is_still_used(self):
        self.assertEqual(heimdall.derive_node_id(self.KEY, ""),
                         "0ce8fe4fb8e5ea2c")


class KeyedCaptureTests(unittest.TestCase):
    def setUp(self):
        self.rows = heimdall.parse_offline_json(_write_tmp(KEYED_JSON, ".json"))

    def test_disc_uses_the_key_and_keeps_the_short_id_as_name(self):
        disc = self.rows[0]
        self.assertEqual(disc["node_id"], "0ce8fe4fb8e5ea2c")
        self.assertEqual(disc["name"], "0CE8")
        self.assertEqual(disc["rssi"], -98.0)

    def test_trace_ping_parses_as_a_keyed_sighting(self):
        trace = self.rows[1]
        self.assertEqual(trace["node_id"], "ef8b822c5059aa5c")
        self.assertEqual(trace["node_type"], "REPEATER")

    def test_rx_token_resolves_against_a_key_from_the_same_capture(self):
        # Same physical node as the DISC ping, so it must land on the same id
        # (and collapse into one node downstream) rather than a short 0ce8.
        self.assertEqual(self.rows[2]["node_id"], "0ce8fe4fb8e5ea2c")

    def test_rx_token_with_no_known_key_keeps_its_short_id(self):
        self.assertEqual(self.rows[3]["node_id"], "fb03")

    def test_repeat_sighting_collapses_onto_the_derived_id(self):
        uniq, collapsed = heimdall.collapse_repeat_sightings(self.rows)
        self.assertEqual(collapsed, 1)
        self.assertEqual([n["node_id"] for n in uniq],
                         ["0ce8fe4fb8e5ea2c", "ef8b822c5059aa5c", "fb03"])

    def test_only_the_unkeyed_node_is_predicted_rejected(self):
        warnings = heimdall.predict_server_rejects(self.rows)
        self.assertEqual(len(warnings), 1)
        self.assertIn("1 of 4 node_ids", warnings[0])

    def test_capturing_devices_own_key_is_not_a_node(self):
        self.assertNotIn("deadbeefdeadbeef",
                         [r["node_id"] for r in self.rows])


class PublicKeyWireFieldTests(unittest.TestCase):
    """`public_key` is optional on wdgwars.pl's meshcore record (confirmed live
    2026-08-10): 64 hex or bad_public_key, node_id must be its prefix or
    key_prefix_mismatch, absent is always fine."""

    def setUp(self):
        self.rows = heimdall.parse_offline_json(_write_tmp(KEYED_JSON, ".json"))

    def test_sent_when_the_capture_gave_us_the_key(self):
        self.assertEqual(
            self.rows[0]["public_key"],
            "0ce8fe4fb8e5ea2c0a7b1a974dfbd604"
            "ba8a63f68da6a26f146c4bd1cea1ffe6")

    def test_every_sent_key_is_64_hex_with_node_id_as_its_prefix(self):
        for r in self.rows:
            if "public_key" in r:
                self.assertEqual(len(r["public_key"]), 64)
                self.assertRegex(r["public_key"], r"^[0-9a-f]{64}$")
                self.assertTrue(r["public_key"].startswith(r["node_id"]))

    def test_omitted_rather_than_null_when_we_have_no_key(self):
        self.assertNotIn("public_key", self.rows[3])  # short-id RX token

    def test_rx_token_carries_the_key_it_resolved_against(self):
        self.assertEqual(self.rows[2]["public_key"], self.rows[0]["public_key"])

    def test_csv_records_never_claim_a_key(self):
        rows = heimdall.parse_meshmapper_csv(_write_tmp(SECTIONED_CSV, ".csv"))
        self.assertTrue(all("public_key" not in r for r in rows))

    def test_partial_key_derives_an_id_but_is_never_sent_as_a_key(self):
        # Long enough to slice a node_id out of, too short to be a real
        # Ed25519 key, so sending it would come back bad_public_key.
        rec = heimdall._build_record("0ce8fe4fb8e5ea2c", "REPEATER", "0CE8",
                                     1.0, 2.0, None, None, "t",
                                     "0CE8FE4FB8E5EA2C0A7B")
        self.assertEqual(rec["node_id"], "0ce8fe4fb8e5ea2c")
        self.assertNotIn("public_key", rec)

    def test_key_that_does_not_match_the_node_id_is_dropped(self):
        rec = heimdall._build_record("fb03", "REPEATER", "FB03", 1.0, 2.0,
                                     None, None, "t", "0CE8FE" + "0" * 58)
        self.assertNotIn("public_key", rec)


class AmbiguousKeyTests(unittest.TestCase):
    def test_two_nodes_sharing_a_short_id_leave_tokens_alone(self):
        keys = {"0ce8fe4fb8e5ea2c" + "0" * 48, "0ce8aa11b22c33d4" + "0" * 48}
        rec = heimdall._node_token_to_record("0CE8(-8.00)", "t", 1.0, 2.0, keys)
        self.assertEqual(rec["node_id"], "0ce8")

    def test_one_identity_logged_twice_still_resolves(self):
        # Same first 8 bytes, differing tails: one node_id, not an ambiguity.
        keys = {"0ce8fe4fb8e5ea2c" + "1" * 48, "0ce8fe4fb8e5ea2c" + "2" * 48}
        rec = heimdall._node_token_to_record("0CE8(-8.00)", "t", 1.0, 2.0, keys)
        self.assertEqual(rec["node_id"], "0ce8fe4fb8e5ea2c")
        # ...but we can't say which of the two keys is that node's, so neither
        # gets asserted on the wire.
        self.assertNotIn("public_key", rec)


class NodeTokenTests(unittest.TestCase):
    def test_disc_token_with_marker(self):
        rec = heimdall._node_token_to_record("910E(R)(-6.00)", "t", 0.0, 0.0)
        self.assertEqual(rec["node_id"], "910e")
        self.assertEqual(rec["node_type"], "REPEATER")
        self.assertEqual(rec["type"], "MESHCORE")
        self.assertNotIn("snr", rec)  # dropped from the wire record

    def test_tx_token_without_marker(self):
        rec = heimdall._node_token_to_record("0CE8(-0.25)", "t", 0.0, 0.0)
        self.assertEqual(rec["node_id"], "0ce8")
        self.assertNotIn("snr", rec)  # dropped from the wire record

    def test_garbage_token_returns_none(self):
        self.assertIsNone(heimdall._node_token_to_record("", "t", 0.0, 0.0))
        self.assertIsNone(heimdall._node_token_to_record("notatoken", "t", 0.0, 0.0))


class EnvelopeTests(unittest.TestCase):
    def test_envelope_shape(self):
        rows = [
            {"timestamp": "t", "node_id": "E4", "type": "repeater", "name": "",
             "lat": 0.0, "lon": 0.0, "rssi": -100.0, "snr": 1.0}
        ]
        env = heimdall.build_envelope(rows, "test-key")
        self.assertIn("data", env)
        self.assertIn("nonce", env)
        self.assertIn("sig", env)
        # nonce is 16 hex chars (token_hex(8))
        self.assertEqual(len(env["nonce"]), 16)
        # sig is 64 hex chars (sha256)
        self.assertEqual(len(env["sig"]), 64)

    def test_envelope_signature_reproducible(self):
        rows = [
            {"timestamp": "t", "node_id": "E4", "type": "repeater", "name": "",
             "lat": 0.0, "lon": 0.0, "rssi": -100.0, "snr": 1.0}
        ]
        # Rebuild a known envelope and verify the HMAC manually
        body = {"networks": [], "aircraft": [], "meshcore_nodes": rows}
        body_json = json.dumps(body, separators=(",", ":"))
        data_b64 = base64.b64encode(body_json.encode()).decode()
        nonce = "0123456789abcdef"
        expected_sig = hmac.new(
            b"test-key", (nonce + data_b64).encode(), hashlib.sha256
        ).hexdigest()
        # Compare against the envelope-build path with a fixed nonce by
        # patching secrets briefly
        import secrets as secrets_mod
        orig = secrets_mod.token_hex
        try:
            secrets_mod.token_hex = lambda n=8: "0123456789abcdef"
            env = heimdall.build_envelope(rows, "test-key")
            self.assertEqual(env["nonce"], nonce)
            self.assertEqual(env["sig"], expected_sig)
            self.assertEqual(env["data"], data_b64)
        finally:
            secrets_mod.token_hex = orig

    def test_meshcore_slot_populated_aircraft_empty(self):
        # The whole point of the envelope shape: Heimdall fills meshcore_nodes,
        # leaves aircraft empty. The inverse of Muninn.
        rows = [{"timestamp": "t", "node_id": "E4", "type": "repeater",
                 "name": "", "lat": 0.0, "lon": 0.0, "rssi": -100.0, "snr": 1.0}]
        env = heimdall.build_envelope(rows, "test-key")
        decoded = json.loads(base64.b64decode(env["data"]).decode())
        self.assertEqual(decoded["aircraft"], [])
        self.assertEqual(decoded["networks"], [])
        self.assertEqual(len(decoded["meshcore_nodes"]), 1)


class ChunkTests(unittest.TestCase):
    def test_chunked_yields_correct_sizes(self):
        data = list(range(2500))
        chunks = list(heimdall.chunked(data, 1000))
        self.assertEqual(len(chunks), 3)
        self.assertEqual(len(chunks[0]), 1000)
        self.assertEqual(len(chunks[1]), 1000)
        self.assertEqual(len(chunks[2]), 500)

    def test_chunked_empty(self):
        self.assertEqual(list(heimdall.chunked([], 1000)), [])


class PredictServerRejectsTests(unittest.TestCase):
    @staticmethod
    def _node(node_id="0ce8fe12", lat=51.0, lon=6.0):
        return {"node_id": node_id, "node_type": "REPEATER", "name": node_id,
                "lat": lat, "lon": lon, "rssi": None,
                "first_seen": "2026-07-27 06:37:27", "type": "MESHCORE"}

    def test_clean_records_produce_no_warnings(self):
        nodes = [self._node(), self._node("94c0d6ab", 50.1, 6.2)]
        self.assertEqual(heimdall.predict_server_rejects(nodes), [])

    def test_short_node_id_flagged_as_bad_node_id(self):
        # Real MeshMapper IDs: 2-6 hex, all under the server's 8-hex floor.
        nodes = [self._node("94c0d6"), self._node("e4"), self._node()]
        warnings = heimdall.predict_server_rejects(nodes)
        self.assertEqual(len(warnings), 1)
        self.assertIn("2 of 3", warnings[0])
        self.assertIn("bad_node_id", warnings[0])

    def test_uppercase_and_overlong_ids_also_flagged(self):
        # The gate is lowercase-only and capped at 16 hex; a 64-hex public
        # key or an uppercase ID misses it just like a short one.
        nodes = [self._node("0CE8FE12"), self._node("a" * 64)]
        warnings = heimdall.predict_server_rejects(nodes)
        self.assertIn("2 of 2", warnings[0])

    def test_zero_gps_flagged_as_no_gps(self):
        nodes = [self._node(lat=0.0, lon=0.0), self._node()]
        warnings = heimdall.predict_server_rejects(nodes)
        self.assertEqual(len(warnings), 1)
        self.assertIn("no_gps", warnings[0])

    def test_half_zero_gps_flagged_as_no_gps(self):
        # One real coordinate and one zero: the server rejects it as no_gps
        # (issue #11), so the prediction has to say so too.
        nodes = [self._node(lat=0.0, lon=-117.22637),
                 self._node("94c0d6ab", lat=33.7, lon=0.0), self._node()]
        warnings = heimdall.predict_server_rejects(nodes)
        self.assertEqual(len(warnings), 1)
        self.assertIn("2 of 3", warnings[0])
        self.assertIn("no_gps", warnings[0])

    def test_off_globe_coordinates_flagged(self):
        # INT32_MAX / 1e6: nonzero, so it passes a zero check, and the server
        # accepts it. It is warned about, not reported as no_gps.
        nodes = [self._node(lat=51.0, lon=2147.483647), self._node()]
        warnings = heimdall.predict_server_rejects(nodes)
        self.assertEqual(len(warnings), 1)
        self.assertIn("1 of 2", warnings[0])
        self.assertIn("off the globe", warnings[0])
        self.assertNotIn("no_gps", warnings[0])

    def test_both_gates_stack(self):
        nodes = [self._node("e4", 0.0, 0.0)]
        warnings = heimdall.predict_server_rejects(nodes)
        self.assertEqual(len(warnings), 2)


class UnaccountedNodesNoteTests(unittest.TestCase):
    """Regression for issue #1: a 2xx response whose counters cover fewer
    nodes than were submitted must not read as a clean upload."""

    def _run_main(self, response_body: str) -> str:
        import contextlib
        import io
        from unittest import mock
        # Parses to 3 sightings; 0ce8 repeats, so 2 unique nodes upload.
        csv_path = _write_tmp(SECTIONED_CSV, ".csv")
        err, out = io.StringIO(), io.StringIO()
        with mock.patch.object(heimdall, "upload",
                               return_value=[(200, response_body)]), \
             mock.patch.object(heimdall, "load_key", return_value="k"), \
             contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            rc = heimdall.main([str(csv_path), "--no-version-check"])
        self.assertEqual(rc, 0)
        return err.getvalue()

    def test_all_zero_counters_trigger_note(self):
        stderr = self._run_main(json.dumps(
            {"meshcore_imported": 0, "meshcore_already_seen": 0,
             "meshcore_rejected": 0}))
        self.assertIn("no verdict", stderr)
        self.assertIn("2 submitted nodes", stderr)

    def test_fully_accounted_response_stays_quiet(self):
        stderr = self._run_main(json.dumps(
            {"meshcore_imported": 1, "meshcore_already_seen": 0,
             "meshcore_rejected": 1,
             "meshcore_reject_reasons": {"bad_node_id": 1}}))
        self.assertNotIn("no verdict", stderr)

    def test_repeat_sightings_collapse_before_upload(self):
        stderr = self._run_main(json.dumps(
            {"meshcore_imported": 2, "meshcore_already_seen": 0,
             "meshcore_rejected": 0}))
        self.assertIn("collapsed 1 repeat sighting", stderr)
        self.assertIn("2 unique nodes", stderr)


class CollapseRepeatSightingsTests(unittest.TestCase):
    @staticmethod
    def _node(node_id, rssi=None):
        return {"node_id": node_id, "node_type": "REPEATER", "name": node_id,
                "lat": 1.0, "lon": 2.0, "rssi": rssi,
                "first_seen": "2026-07-27 06:37:27", "type": "MESHCORE"}

    def test_first_sighting_wins_order_preserved(self):
        nodes = [self._node("0ce8", rssi=-98.0), self._node("910e"),
                 self._node("0ce8", rssi=-50.0)]
        unique, collapsed = heimdall.collapse_repeat_sightings(nodes)
        self.assertEqual(collapsed, 1)
        self.assertEqual([n["node_id"] for n in unique], ["0ce8", "910e"])
        self.assertEqual(unique[0]["rssi"], -98.0)  # first sighting wins

    def test_no_repeats_is_a_noop(self):
        nodes = [self._node("0ce8"), self._node("910e")]
        unique, collapsed = heimdall.collapse_repeat_sightings(nodes)
        self.assertEqual(collapsed, 0)
        self.assertEqual(unique, nodes)


class FillerIdTests(unittest.TestCase):
    @staticmethod
    def _node(node_id):
        return {"node_id": node_id, "node_type": "REPEATER", "name": node_id,
                "lat": 1.0, "lon": 2.0, "rssi": None,
                "first_seen": "2026-07-27 06:37:27", "type": "MESHCORE"}

    def test_repeated_digit_id_flagged_with_sighting_count(self):
        # The issue #1 sample: eeeeee showing up in three sightings.
        nodes = [self._node("eeeeee")] * 3 + [self._node("94c0d6")]
        warnings = heimdall.flag_filler_ids(nodes)
        self.assertEqual(len(warnings), 1)
        self.assertIn("'eeeeee'", warnings[0])
        self.assertIn("(3x)", warnings[0])

    def test_normal_ids_not_flagged(self):
        nodes = [self._node("94c0d6"), self._node("0ce8"),
                 self._node("abab")]  # repeating pattern, but two digits
        self.assertEqual(heimdall.flag_filler_ids(nodes), [])


class VersionTests(unittest.TestCase):
    def test_version_string(self):
        self.assertIsInstance(heimdall.__version__, str)
        self.assertRegex(heimdall.__version__, r"^\d+\.\d+\.\d+")

    def test_is_newer_true_for_higher_version(self):
        self.assertTrue(heimdall._is_newer("0.4.1", "0.4.0"))
        self.assertTrue(heimdall._is_newer("0.5.0", "0.4.9"))
        self.assertTrue(heimdall._is_newer("1.0.0", "0.9.9"))

    def test_is_newer_false_for_lower_or_equal_version(self):
        # Regression for #9: GitHub's "latest release" lagging behind the
        # installed __version__ must never be reported as an upgrade.
        self.assertFalse(heimdall._is_newer("0.3.0", "0.4.0"))
        self.assertFalse(heimdall._is_newer("0.4.0", "0.4.0"))

    def test_is_newer_false_for_unparseable_tags(self):
        self.assertFalse(heimdall._is_newer("not-a-version", "0.4.0"))
        self.assertFalse(heimdall._is_newer("0.4.0", "not-a-version"))


class NetworkFieldTests(unittest.TestCase):
    """LOCOSP's 2026-08-12 mesh-slot contract: the server now takes both
    MeshCore and Meshtastic, told apart by an authoritative `network` field.
    Heimdall parses MeshCore captures only, so every record it builds states
    that positively."""

    def test_flat_csv_rows_carry_network_meshcore(self):
        rows = heimdall.parse_meshmapper_text(SAMPLE)
        self.assertTrue(rows)
        self.assertTrue(all(r["network"] == "meshcore" for r in rows))

    def test_sectioned_csv_rows_carry_network_meshcore(self):
        rows = heimdall.parse_meshmapper_text(SECTIONED_CSV)
        self.assertTrue(rows)
        self.assertTrue(all(r["network"] == "meshcore" for r in rows))

    def test_offline_json_rows_carry_network_meshcore(self):
        rows = heimdall.parse_offline_json_obj(json.loads(OFFLINE_JSON))
        self.assertTrue(rows)
        self.assertTrue(all(r["network"] == "meshcore" for r in rows))

    def test_build_record_sets_network_meshcore(self):
        rec = heimdall._build_record("0ce8fe4fb8e5ea2c", "REPEATER", "0CE8",
                                     1.0, 2.0, None, None, "t")
        self.assertEqual(rec["network"], "meshcore")


class PathHopsWireFieldTests(unittest.TestCase):
    """LOCOSP's 2026-08-12 contract: send `path_hops` when the capture has
    it, used as proposed to decide whether a sighting may move a node's
    position; its absence never rejects a sighting, so it must be omitted
    entirely (not sent as null) rather than guessed. Same pattern already
    used for `public_key`. The MeshMapper flat 'Copy CSV' header carries
    both `path_length` and `path_hops` columns; packed TX/DISC sections and
    offline-JSON pings carry neither."""

    def test_present_when_the_flat_csv_row_has_it(self):
        rows = heimdall.parse_meshmapper_text(SAMPLE)
        self.assertEqual(rows[0]["path_hops"], "29|1F|60|AE|08|77|79|E4")
        self.assertEqual(rows[1]["path_hops"],
                         "37|AB|54|61|60|AE|98|31|47|A1|19")

    def test_path_length_included_alongside_it(self):
        rows = heimdall.parse_meshmapper_text(SAMPLE)
        self.assertEqual(rows[0]["path_length"], 8)
        self.assertEqual(rows[1]["path_length"], 11)

    def test_omitted_rather_than_null_from_packed_sections(self):
        # TX/DISC packed sections have no path_hops/path_length column at all.
        rows = heimdall.parse_meshmapper_text(SECTIONED_CSV)
        self.assertTrue(all("path_hops" not in r for r in rows))
        self.assertTrue(all("path_length" not in r for r in rows))

    def test_omitted_rather_than_null_from_offline_json(self):
        rows = heimdall.parse_offline_json_obj(json.loads(OFFLINE_JSON))
        self.assertTrue(all("path_hops" not in r for r in rows))
        self.assertTrue(all("path_length" not in r for r in rows))

    def test_build_record_omits_path_hops_when_not_given(self):
        rec = heimdall._build_record("0ce8fe4fb8e5ea2c", "REPEATER", "0CE8",
                                     1.0, 2.0, None, None, "t")
        self.assertNotIn("path_hops", rec)
        self.assertNotIn("path_length", rec)

    def test_build_record_includes_path_hops_when_given(self):
        rec = heimdall._build_record("0ce8fe4fb8e5ea2c", "REPEATER", "0CE8",
                                     1.0, 2.0, None, None, "t",
                                     path_hops="29|1F|60|AE", path_length=4)
        self.assertEqual(rec["path_hops"], "29|1F|60|AE")
        self.assertEqual(rec["path_length"], 4)


class RoleKeptAsCapturedTests(unittest.TestCase):
    """LOCOSP's 2026-08-12 contract: send the role exactly as captured, the
    server keeps it verbatim and maps it internally. A single-letter
    MeshMapper marker Heimdall doesn't recognise must never be silently
    coerced into DEFAULT_NODE_TYPE ("REPEATER"), that would misrepresent
    a captured role Heimdall simply hasn't pinned down the full name for
    yet. Absence of any marker is the only case where a default is used,
    since there is genuinely nothing captured to preserve."""

    def test_known_marker_still_maps_to_repeater(self):
        rec = heimdall._node_token_to_record("910E(R)(-6.00)", "t", 0.0, 0.0)
        self.assertEqual(rec["node_type"], "REPEATER")

    def test_unrecognised_marker_is_kept_verbatim_not_coerced(self):
        # 'C' has never been confirmed, but it was captured, so it must ride
        # through rather than being silently overwritten with "REPEATER".
        rec = heimdall._node_token_to_record("1234(C)(-6.00)", "t", 0.0, 0.0)
        self.assertEqual(rec["node_type"], "C")

    def test_no_marker_at_all_falls_back_to_default(self):
        # TX tokens never carry a marker; there's nothing captured to keep.
        rec = heimdall._node_token_to_record("0CE8(-0.25)", "t", 0.0, 0.0)
        self.assertEqual(rec["node_type"], heimdall.DEFAULT_NODE_TYPE)

    def test_build_record_does_not_force_case_on_node_type(self):
        # _build_record used to .upper() every node_type unconditionally;
        # that is itself a normalisation of a captured value and must stop.
        rec = heimdall._build_record("0ce8fe4fb8e5ea2c", "Repeater", "0CE8",
                                     1.0, 2.0, None, None, "t")
        self.assertEqual(rec["node_type"], "Repeater")


_DISCOVERED_DDL = """
CREATE TABLE discovered_contacts (
  id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  public_key BLOB NOT NULL UNIQUE, type INTEGER NOT NULL,
  flags INTEGER NOT NULL, out_path_len INTEGER NOT NULL,
  out_path BLOB NOT NULL, adv_name TEXT NOT NULL,
  last_advert INTEGER NOT NULL, adv_lat INTEGER NOT NULL,
  adv_lon INTEGER NOT NULL, last_mod INTEGER NOT NULL,
  advert_path BLOB NULL, advert_path_len INTEGER NULL)
"""

_CONTACTS_DDL = """
CREATE TABLE contacts (
  id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  public_key BLOB NOT NULL UNIQUE, type INTEGER NOT NULL,
  flags INTEGER NOT NULL, out_path_len INTEGER NOT NULL,
  out_path BLOB NOT NULL, adv_name TEXT NOT NULL,
  last_advert INTEGER NOT NULL, adv_lat INTEGER NOT NULL,
  adv_lon INTEGER NOT NULL, last_mod INTEGER NOT NULL,
  repeater_admin_password TEXT NULL, room_password TEXT NULL,
  last_message_sent_or_received_at INTEGER NULL,
  custom_name TEXT NULL, draft_message_text TEXT NULL)
"""


def _key(seed: str) -> bytes:
    """A 32-byte key blob, as the app stores it: BLOB, not hex text."""
    return bytes.fromhex((seed * 64)[:64])


def _make_db(discovered=(), contacts=()) -> Path:
    """Build a synthetic MeshCore app database.

    Synthetic rather than a trimmed copy of a real dump on purpose: a real one
    carries other operators' node positions, and `examples/` already zeroes
    coordinates for the same reason.
    """
    path = Path(tempfile.mkdtemp()) / "meshcore.db"
    conn = sqlite3.connect(path)
    conn.executescript(_DISCOVERED_DDL + ";" + _CONTACTS_DDL)
    for row in discovered:
        conn.execute(
            "INSERT INTO discovered_contacts (public_key,type,flags,"
            "out_path_len,out_path,adv_name,last_advert,adv_lat,adv_lon,"
            "last_mod,advert_path,advert_path_len) "
            "VALUES (:public_key,:type,0,-1,x'00',:adv_name,:last_advert,"
            ":adv_lat,:adv_lon,0,:advert_path,:advert_path_len)", row)
    for row in contacts:
        conn.execute(
            "INSERT INTO contacts (public_key,type,flags,out_path_len,"
            "out_path,adv_name,last_advert,adv_lat,adv_lon,last_mod,"
            "custom_name) VALUES (:public_key,:type,0,-1,x'00',:adv_name,"
            ":last_advert,:adv_lat,:adv_lon,0,:custom_name)", row)
    conn.commit()
    conn.close()
    return path


def _row(seed="a", type_=2, name="R1", when=None, lat=12_345_678,
         lon=-98_765_432, advert_path=None, advert_path_len=None,
         custom_name=None):
    return {
        "public_key": _key(seed), "type": type_, "adv_name": name,
        "last_advert": int(time.time()) - 3600 if when is None else when,
        "adv_lat": lat, "adv_lon": lon, "advert_path": advert_path,
        "advert_path_len": advert_path_len, "custom_name": custom_name,
    }


class MeshcoreDbTests(unittest.TestCase):
    """The app database is a strict superset of its own JSON export, by a
    wide margin on a real capture, so it is the format that actually gets a
    mesh onto the map."""

    def test_blob_public_key_becomes_a_hex_node_id(self):
        # The app stores public_key as a BLOB. The first cut of this parser
        # passed it to _clean_pubkey, which stringifies bytes to "b'\\xaa..'",
        # fails its own hex check, and dropped every row silently. This
        # is that bug: it must never come back as an empty parse.
        db = _make_db(discovered=[_row(seed="aa")])
        recs = heimdall.parse_meshcore_db(db)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["node_id"], "a" * 16)
        self.assertEqual(recs[0]["public_key"], "a" * 64)

    def test_coordinates_use_the_one_million_scale(self):
        # Synthetic digits rather than a real node's advertised position, for
        # the same reason examples/ zeroes its coordinates. 1e7 would put
        # every one of these nodes in the Gulf of Guinea.
        db = _make_db(discovered=[_row(lat=12_345_678, lon=-98_765_432)])
        rec = heimdall.parse_meshcore_db(db)[0]
        self.assertAlmostEqual(rec["lat"], 12.345678, places=6)
        self.assertAlmostEqual(rec["lon"], -98.765432, places=6)

    def test_type_integers_map_to_confirmed_roles(self):
        db = _make_db(discovered=[
            _row(seed="1", type_=1), _row(seed="2", type_=2),
            _row(seed="3", type_=3), _row(seed="4", type_=4)])
        got = {r["node_id"][0]: r["node_type"]
               for r in heimdall.parse_meshcore_db(db)}
        self.assertEqual(got, {"1": "COMPANION", "2": "REPEATER",
                               "3": "ROOM_SERVER", "4": "SENSOR"})

    def test_unknown_type_integer_is_dropped_not_defaulted(self):
        # Defaulting an unseen role to REPEATER is how a thousand rows get
        # mislabelled quietly; a role we have not confirmed is not ours to
        # name.
        db = _make_db(discovered=[_row(seed="b", type_=9)])
        self.assertEqual(heimdall.parse_meshcore_db(db), [])

    def test_hop_count_decodes_from_packed_advert_path_len(self):
        # out_path_len is -1 on every row of the reference dump, but
        # advert_path_len packs hops in the low 6 bits and bytes-per-hop
        # minus one in the top two. All three widths appear in real data.
        db = _make_db(discovered=[
            _row(seed="c", advert_path=b"\x01" * 5, advert_path_len=5),
            _row(seed="d", advert_path=b"\x01" * 12, advert_path_len=70),
            _row(seed="e", advert_path=b"\x01" * 18, advert_path_len=134)])
        got = {r["node_id"][0]: (r["path_hops"], r["path_length"])
               for r in heimdall.parse_meshcore_db(db)}
        self.assertEqual(got["c"], (5, 5))    # 1 byte per hop
        self.assertEqual(got["d"], (6, 12))   # 2 bytes per hop
        self.assertEqual(got["e"], (6, 18))   # 3 bytes per hop

    def test_advert_path_len_that_fails_the_identity_yields_no_hops(self):
        # If len(blob) != hops * bytes_per_hop the packing is not the shape
        # this decode was proven against, so it must report nothing rather
        # than a plausible number.
        db = _make_db(discovered=[
            _row(seed="c", advert_path=b"\x01" * 7, advert_path_len=70)])
        rec = heimdall.parse_meshcore_db(db)[0]
        self.assertNotIn("path_hops", rec)
        self.assertNotIn("path_length", rec)

    def test_contacts_rows_have_no_hop_count(self):
        db = _make_db(contacts=[_row(seed="f")])
        rec = heimdall.parse_meshcore_db(db)[0]
        self.assertNotIn("path_hops", rec)

    def test_absurd_last_advert_is_dropped_not_clamped(self):
        # A year-2083 first_seen would outrank every genuine sighting of that
        # node forever; clamping to now does the same damage more quietly.
        far_future = int(time.time()) + (86_400 * 365)
        db = _make_db(discovered=[
            _row(seed="c", when=far_future),
            _row(seed="d", when=118_803),  # 1970, seen in the real dump
            _row(seed="e")])
        recs = heimdall.parse_meshcore_db(db)
        self.assertEqual([r["node_id"][0] for r in recs], ["e"])

    def test_mild_clock_skew_ahead_is_tolerated(self):
        db = _make_db(discovered=[_row(when=int(time.time()) + 3600)])
        self.assertEqual(len(heimdall.parse_meshcore_db(db)), 1)

    def test_no_gps_fix_is_dropped(self):
        db = _make_db(discovered=[_row(seed="c", lat=0, lon=0), _row(seed="d")])
        recs = heimdall.parse_meshcore_db(db)
        self.assertEqual([r["node_id"][0] for r in recs], ["d"])

    def test_half_zero_fix_is_dropped(self):
        # One zero coordinate is still no fix; the server rejects it as no_gps.
        db = _make_db(discovered=[
            _row(seed="c", lat=0, lon=-117_226_370),
            _row(seed="e", lat=33_700_000, lon=0),
            _row(seed="d")])
        recs = heimdall.parse_meshcore_db(db)
        self.assertEqual([r["node_id"][0] for r in recs], ["d"])

    def test_int32_max_no_fix_sentinel_is_dropped(self):
        # An unset int32 coordinate arrives as INT32_MAX. Scaled by 1e6 it is
        # 2147.483647: nonzero, so a zero check lets it through, and the
        # server accepted one and mapped it at longitude 2147 (issue #11).
        db = _make_db(discovered=[
            _row(seed="c", lon=2_147_483_647),
            _row(seed="e", lat=2_147_483_647),
            _row(seed="d")])
        recs = heimdall.parse_meshcore_db(db)
        self.assertEqual([r["node_id"][0] for r in recs], ["d"])

    def test_since_days_gates_a_stale_back_catalogue(self):
        old = int(time.time()) - (86_400 * 40)
        db = _make_db(discovered=[_row(seed="c", when=old), _row(seed="d")])
        self.assertEqual(len(heimdall.parse_meshcore_db(db)), 2)
        recent = heimdall.parse_meshcore_db(db, since_days=20)
        self.assertEqual([r["node_id"][0] for r in recent], ["d"])

    def test_union_across_tables_keeps_the_newer_sighting(self):
        # Both orderings, because the tables are read in a fixed order: a
        # test that only puts the fresh row in the table read second passes
        # even when the newest-wins comparison is removed entirely, which is
        # exactly what the first version of this test did.
        old = int(time.time()) - (86_400 * 10)
        new = int(time.time()) - 60
        for label, discovered_when, contacts_when in (
                ("fresh in contacts", old, new),
                ("fresh in discovered", new, old)):
            with self.subTest(label):
                db = _make_db(
                    discovered=[_row(seed="c", name="d", when=discovered_when)],
                    contacts=[_row(seed="c", name="c", when=contacts_when)])
                recs = heimdall.parse_meshcore_db(db)
                self.assertEqual(len(recs), 1)
                expected = "c" if contacts_when == new else "d"
                self.assertEqual(recs[0]["name"], expected)
                self.assertEqual(
                    recs[0]["first_seen"],
                    heimdall._format_first_seen(
                        heimdall.datetime.datetime.fromtimestamp(
                            new, heimdall.datetime.timezone.utc
                        ).strftime("%Y-%m-%dT%H:%M:%S")))

    def test_custom_name_is_never_uploaded(self):
        # custom_name is the operator's private label for someone else's
        # node, not something that node broadcast.
        db = _make_db(contacts=[_row(seed="c", name="Example Advertised Name",
                                     custom_name="PRIVATE local label")])
        rec = heimdall.parse_meshcore_db(db)[0]
        self.assertEqual(rec["name"], "Example Advertised Name")
        self.assertNotIn("PRIVATE", json.dumps(rec))

    def test_db_has_no_rssi(self):
        # These tables record that a node was heard and where it claimed to
        # be, not how strongly. A zero would read as a real measurement.
        db = _make_db(discovered=[_row()])
        self.assertIsNone(heimdall.parse_meshcore_db(db)[0]["rssi"])

    def test_dispatch_detects_sqlite_by_magic_not_extension(self):
        # The app shares its database out under whatever name the operator
        # saves it as, so the sniff has to lead.
        db = _make_db(discovered=[_row()])
        renamed = db.with_suffix(".json")
        db.rename(renamed)
        recs, fmt = heimdall.parse_file(renamed)
        self.assertEqual(fmt, "meshcore-app-db")
        self.assertEqual(len(recs), 1)

    def test_records_clear_the_servers_own_gates(self):
        db = _make_db(discovered=[_row()])
        recs = heimdall.parse_meshcore_db(db)
        self.assertEqual(heimdall.predict_server_rejects(recs), [])

    def test_sqlite3_is_not_imported_at_module_top_level(self):
        # Pyodide unvendors sqlite3 exactly as it does ssl. A top-level
        # `import sqlite3` in heimdall.py does not merely disable database
        # support in the web frontend, it kills the whole page on import
        # before any format can be parsed, CSV and JSON included. Confirmed
        # empirically against the pinned Pyodide 0.26.4. So the import stays
        # inside the parser.
        src = (ROOT / "heimdall.py").read_text(encoding="utf-8")
        top_level = [ln for ln in src.splitlines()
                     if ln.startswith("import ") or ln.startswith("from ")]
        self.assertNotIn("import sqlite3", top_level)
        self.assertIn("import sqlite3", src)  # still imported, just lazily

    def test_web_frontend_loads_the_sqlite3_package(self):
        # The other half of the same guard: heimdall.py importing it lazily
        # keeps the page alive, but the database format only actually works
        # in the browser if the runtime loads the package.
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertRegex(app, r'loadPackage\(\[[^\]]*"sqlite3"')

    def test_missing_sqlite3_reports_a_reason_and_spares_text_formats(self):
        # Simulate the unvendored runtime: the database input must fail with
        # an explanation rather than an ImportError traceback, and the text
        # parsers must keep working.
        real_import = builtins.__import__

        def no_sqlite(name, *a, **kw):
            if name == "sqlite3":
                raise ModuleNotFoundError("No module named 'sqlite3'")
            return real_import(name, *a, **kw)

        db = _make_db(discovered=[_row()])
        csv_path = Path(tempfile.mkdtemp()) / "flat.csv"
        csv_path.write_text(
            "timestamp,repeater_id,snr,rssi,latitude,longitude\n"
            "2026-07-02T18:39:27,0CE8,0.5,-101,48.7,2.07\n", encoding="utf-8")

        with unittest.mock.patch.object(builtins, "__import__", no_sqlite):
            with self.assertRaises(ValueError) as ctx:
                heimdall.parse_meshcore_db(db)
            self.assertIn("sqlite3", str(ctx.exception))
            records, fmt = heimdall.parse_file(csv_path)
        self.assertEqual(fmt, "meshmapper-csv")
        self.assertEqual(len(records), 1)

    def test_non_sqlite_db_extension_still_falls_through_to_text(self):
        path = Path(tempfile.mkdtemp()) / "notreally.db"
        path.write_text("timestamp,repeater_id,snr,rssi,latitude,longitude\n"
                        "2026-07-02T18:39:27,0CE8,0.5,-101,48.7,2.07\n",
                        encoding="utf-8")
        _, fmt = heimdall.parse_file(path)
        self.assertEqual(fmt, "meshmapper-csv")


if __name__ == "__main__":
    unittest.main(verbosity=2)
