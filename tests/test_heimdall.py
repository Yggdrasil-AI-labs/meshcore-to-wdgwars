"""Smoke tests for Heimdall v0.1.0.

Run: python -m unittest tests/test_heimdall.py
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import sys
import tempfile
import unittest
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
        self.assertEqual(first["timestamp"], "2026-05-21T13:20:11.520125")
        self.assertEqual(first["node_id"], "E4")
        self.assertEqual(first["type"], "repeater")
        self.assertEqual(first["name"], "")
        self.assertEqual(first["lat"], 0.0)
        self.assertEqual(first["lon"], 0.0)
        self.assertEqual(first["rssi"], -111.0)
        self.assertEqual(first["snr"], 5.75)

    def test_numeric_coercion(self):
        rows = heimdall.parse_meshmapper_csv(self.path)
        for r in rows:
            self.assertIsInstance(r["lat"], float)
            self.assertIsInstance(r["lon"], float)
            self.assertIsInstance(r["rssi"], float)
            self.assertIsInstance(r["snr"], float)

    def test_variable_width_node_ids(self):
        rows = heimdall.parse_meshmapper_csv(self.path)
        ids = [r["node_id"] for r in rows]
        self.assertIn("E4", ids)       # 2-hex
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
        self.assertEqual([r["node_id"] for r in rows], ["0CE8", "910E", "0CE8"])
        self.assertEqual([r["snr"] for r in rows], [-0.25, -6.0, 1.25])

    def test_disc_repeater_marker_maps_to_repeater(self):
        rows = heimdall.parse_meshmapper_csv(self.path)
        self.assertTrue(all(r["type"] == "repeater" for r in rows))

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
        self.assertEqual(rows[0]["node_id"], "0CE8")
        self.assertEqual(rows[1]["node_id"], "FB03")

    def test_disc_carries_real_rssi_and_snr(self):
        rows = heimdall.parse_offline_json(self.path)
        disc = rows[0]
        self.assertEqual(disc["rssi"], -98.0)
        self.assertEqual(disc["snr"], 2.25)
        self.assertEqual(disc["type"], "repeater")  # REPEATER lower-cased

    def test_rx_token_has_snr_but_no_rssi(self):
        rows = heimdall.parse_offline_json(self.path)
        rx = rows[1]
        self.assertEqual(rx["snr"], -8.0)
        self.assertIsNone(rx["rssi"])

    def test_epoch_timestamp_becomes_iso(self):
        rows = heimdall.parse_offline_json(self.path)
        self.assertTrue(rows[0]["timestamp"].startswith("2026-"))

    def test_dispatch_detects_json_by_extension(self):
        rows, fmt = heimdall.parse_file(self.path)
        self.assertEqual(fmt, "meshcore-offline-json")
        self.assertEqual(len(rows), 2)


class NodeTokenTests(unittest.TestCase):
    def test_disc_token_with_marker(self):
        rec = heimdall._node_token_to_record("910E(R)(-6.00)", "t", 0.0, 0.0)
        self.assertEqual(rec["node_id"], "910E")
        self.assertEqual(rec["type"], "repeater")
        self.assertEqual(rec["snr"], -6.0)

    def test_tx_token_without_marker(self):
        rec = heimdall._node_token_to_record("0CE8(-0.25)", "t", 0.0, 0.0)
        self.assertEqual(rec["node_id"], "0CE8")
        self.assertEqual(rec["snr"], -0.25)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
