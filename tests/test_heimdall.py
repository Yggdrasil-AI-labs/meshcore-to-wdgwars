"""Smoke tests for Heimdall v0.2.

Parser tests cover the Heimdall-specific work (MeshMapper CSV → mesh
schema). Envelope/transport correctness lives in gungnir's tests.

Run: python -m unittest tests/test_heimdall.py
"""
from __future__ import annotations

import base64
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


class EnvelopeShimTests(unittest.TestCase):
    """heimdall.build_envelope() is a thin shim over gungnir for back-
    compat — verify shape + meshcore-slot population. Cryptographic
    correctness is gungnir's responsibility (proven by its own tests
    + the byte-identical Muninn parity test)."""

    def _sample_node(self):
        return [{"timestamp": "t", "node_id": "E4", "type": "repeater",
                 "name": "", "lat": 0.0, "lon": 0.0, "rssi": -100.0, "snr": 1.0}]

    def test_envelope_shape(self):
        env = heimdall.build_envelope(self._sample_node(), "test-key")
        self.assertIn("data", env)
        self.assertIn("nonce", env)
        self.assertIn("sig", env)
        self.assertEqual(len(env["nonce"]), 16)
        self.assertEqual(len(env["sig"]), 64)

    def test_meshcore_slot_populated_aircraft_empty(self):
        """Heimdall fills meshcore_nodes, leaves aircraft empty —
        the inverse of Muninn. Encoded by gungnir.build_payload()."""
        env = heimdall.build_envelope(self._sample_node(), "test-key")
        decoded = json.loads(base64.b64decode(env["data"]).decode())
        self.assertEqual(decoded["aircraft"], [])
        self.assertEqual(decoded["networks"], [])
        self.assertEqual(len(decoded["meshcore_nodes"]), 1)


class VersionTests(unittest.TestCase):
    def test_version_string(self):
        self.assertIsInstance(heimdall.__version__, str)
        self.assertRegex(heimdall.__version__, r"^\d+\.\d+\.\d+")

    def test_version_is_v02(self):
        """Sanity check the bump landed."""
        self.assertTrue(heimdall.__version__.startswith("0.2"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
