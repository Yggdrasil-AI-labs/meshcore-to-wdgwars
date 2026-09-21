"""The already-sent gate, and the constraints that shape it here.

Heimdall is the one family member that does not depend on gungnir. The
2026-06-03 audit kept its transport inlined so this single file ships as
both a CLI and a Pyodide page with zero runtime dependencies, and gungnir
imports `ssl` at module scope, which Pyodide unvendors. v0.8.0 already took
the live Pages deploy down with an import of that shape.

So the gate is optional and lazy: imported inside the functions that need
it, never at module scope, never in the browser, and any failure leaves the
gate off rather than breaking an upload. The tests below pin all four of
those properties, because each one is load-bearing and none is obvious from
reading the call sites.

Run: HEIMDALL_TEST_ALLOW_LIVE_KEY=1 python -m pytest tests/test_holds_gate.py
"""
from __future__ import annotations

import json
import sys
import time
import unittest
from unittest import mock

import heimdall

# CI installs no gungnir, deliberately: Heimdall's zero-dependency install
# is the property the 2026-06-03 audit protects, and a CI run without the
# library is what proves it still holds. So the gate's behaviour tests skip
# there, while the tests that assert the gate stays OUT of the way always
# run -- those are the ones that matter in exactly that environment.
HAS_GUNGNIR = heimdall.holds_available()
needs_gungnir = unittest.skipUnless(
    HAS_GUNGNIR, "gungnir not installed; the gate is inactive here")


def node(node_id: str, **kw):
    rec = {"node_id": node_id.lower(), "node_type": "client",
           "name": node_id, "lat": 41.4, "lon": -82.1, "rssi": -80,
           "first_seen": "2026-09-21 10:00:00", "type": "MESHCORE",
           "network": "meshcore"}
    rec.update(kw)
    return rec


class LazyImportTests(unittest.TestCase):
    """gungnir must never be imported at module scope, and never at all in
    the browser."""

    def test_gungnir_is_not_a_module_level_import(self):
        src = (heimdall.__file__ and open(heimdall.__file__,
                                          encoding="utf-8").read()) or ""
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import gungnir", "from gungnir")):
                self.assertTrue(
                    line.startswith((" ", "\t")),
                    f"gungnir imported at module scope: {line!r}. Pyodide "
                    f"unvendors ssl, which gungnir imports at import time.")

    def test_the_browser_never_reaches_gungnir(self):
        with mock.patch.object(heimdall.sys, "platform", "emscripten"):
            self.assertTrue(heimdall._in_browser())
            self.assertIsNone(heimdall._holds())
            self.assertFalse(heimdall.holds_available())

    def test_pyodide_in_modules_also_counts_as_browser(self):
        with mock.patch.dict(sys.modules, {"pyodide": mock.Mock()}):
            self.assertTrue(heimdall._in_browser())

    def test_a_broken_gungnir_leaves_the_gate_off_quietly(self):
        # Broad on purpose: this must never be why an upload fails.
        def boom(*a, **kw):
            raise RuntimeError("gungnir exploded")

        with mock.patch.dict(sys.modules):
            sys.modules.pop("gungnir.holds", None)
            with mock.patch("builtins.__import__", side_effect=boom):
                self.assertIsNone(heimdall._holds())


@needs_gungnir
class FilterTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.nodes = [node("0CE8"), node("910E")]

    def test_no_gungnir_is_a_pass_through(self):
        with mock.patch.object(heimdall, "_holds", return_value=None):
            out, dropped = heimdall.filter_already_sent(self.nodes, self.now)
        self.assertEqual((out, dropped), (self.nodes, 0))

    def test_held_nodes_are_dropped(self):
        heimdall.record_sent_nodes([node("0CE8")], self.now, imported=1)
        out, dropped = heimdall.filter_already_sent(self.nodes, self.now)
        self.assertEqual(dropped, 1)
        self.assertEqual([n["node_id"] for n in out], ["910e"])

    def test_all_held_returns_an_empty_list(self):
        heimdall.record_sent_nodes(self.nodes, self.now, imported=1)
        out, dropped = heimdall.filter_already_sent(self.nodes, self.now)
        self.assertEqual((out, dropped), ([], 2))

    def test_an_expired_hold_does_not_drop(self):
        heimdall.record_sent_nodes(self.nodes, self.now, imported=1)
        import gungnir.holds as holds
        later = self.now + holds.SENT_TTL + 1
        out, dropped = heimdall.filter_already_sent(self.nodes, later)
        self.assertEqual(dropped, 0)

    def test_an_empty_capture_is_left_alone(self):
        self.assertEqual(heimdall.filter_already_sent([], self.now), ([], 0))


@needs_gungnir
class RecordTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.nodes = [node("0CE8")]
        import gungnir.holds as holds
        self.holds = holds

    def test_nothing_imported_earns_the_long_hold(self):
        heimdall.record_sent_nodes(self.nodes, self.now, imported=0)
        state = self.holds.load(heimdall.HOLDS_TOOL)
        self.assertGreater(state["0CE8"], self.now + self.holds.SENT_TTL)

    def test_something_imported_keeps_the_short_hold(self):
        heimdall.record_sent_nodes(self.nodes, self.now, imported=3)
        state = self.holds.load(heimdall.HOLDS_TOOL)
        self.assertLessEqual(state["0CE8"], self.now + self.holds.SENT_TTL)

    def test_an_unknown_total_is_not_treated_as_zero(self):
        # The distinction the whole family got wrong once: a total we could
        # not read must not earn a day-long hold on an unconfirmed payload.
        heimdall.record_sent_nodes(self.nodes, self.now, imported=None)
        state = self.holds.load(heimdall.HOLDS_TOOL)
        self.assertLessEqual(state["0CE8"], self.now + self.holds.SENT_TTL)

    def test_node_ids_are_held_case_insensitively(self):
        # Records carry node_id lower-cased; holds key on upper. A mismatch
        # would mean nothing was ever suppressed.
        heimdall.record_sent_nodes([node("abcd")], self.now, imported=1)
        out, dropped = heimdall.filter_already_sent([node("ABCD")], self.now)
        self.assertEqual((out, dropped), ([], 1))

    def test_no_gungnir_records_nothing_and_does_not_raise(self):
        with mock.patch.object(heimdall, "_holds", return_value=None):
            heimdall.record_sent_nodes(self.nodes, self.now, imported=0)


class NoGungnirContractTests(unittest.TestCase):
    """Runs everywhere, including CI where gungnir is absent. This is the
    environment the zero-dependency install has to keep working in."""

    def test_filter_is_a_pass_through_without_the_library(self):
        nodes = [node("0CE8")]
        with mock.patch.object(heimdall, "_holds", return_value=None):
            self.assertEqual(heimdall.filter_already_sent(nodes, time.time()),
                             (nodes, 0))

    def test_recording_is_a_no_op_without_the_library(self):
        with mock.patch.object(heimdall, "_holds", return_value=None):
            heimdall.record_sent_nodes([node("0CE8")], time.time(), 0)

    def test_holds_available_answers_honestly(self):
        self.assertIsInstance(heimdall.holds_available(), bool)


@needs_gungnir
class MainWiringTests(unittest.TestCase):
    """Driving main(), because everything above calls the gate directly and
    proves nothing about whether main() reaches it in the right places.

    Both cases below survived a mutation run against the unit tests alone.
    """

    def _main(self, body: str, status: int = 200, extra=()):
        import contextlib
        import io

        from tests.test_heimdall import SECTIONED_CSV, _write_tmp

        csv_path = _write_tmp(SECTIONED_CSV, ".csv")
        err, out = io.StringIO(), io.StringIO()
        with mock.patch.object(heimdall, "upload",
                               return_value=[(status, body)]) as up, \
             mock.patch.object(heimdall, "load_key", return_value="k"), \
             contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            rc = heimdall.main([str(csv_path), "--no-version-check", *extra])
        return rc, err.getvalue(), up

    def _all_seen(self):
        return json.dumps({"meshcore_imported": 0,
                           "meshcore_already_seen": 2,
                           "meshcore_rejected": 0})

    def test_a_repeat_run_skips_the_upload_entirely(self):
        _, _, first = self._main(self._all_seen())
        self.assertEqual(first.call_count, 1)
        rc, err, second = self._main(self._all_seen())
        self.assertEqual(rc, 0)
        self.assertEqual(second.call_count, 0)
        self.assertIn("nothing new to send", err)

    def test_dry_run_is_never_suppressed(self):
        # A dry run reports what WOULD be sent. Holds must not change that
        # answer, or the preview stops matching the real run.
        self._main(self._all_seen())
        _, _, up = self._main(self._all_seen(), extra=["--dry-run"])
        self.assertEqual(up.call_count, 1,
                         "--dry-run must still reach the upload path")

    def test_a_dry_run_records_nothing(self):
        self._main(self._all_seen(), extra=["--dry-run"])
        _, _, up = self._main(self._all_seen())
        self.assertEqual(up.call_count, 1,
                         "a dry run must not hold back the real run after it")

    def test_a_failed_upload_records_nothing(self):
        rc, _, _ = self._main('{"error":"nope"}', status=500)
        self.assertEqual(rc, 1)
        _, _, retry = self._main(self._all_seen())
        self.assertEqual(retry.call_count, 1,
                         "a failed upload must be retried, not held back")


if __name__ == "__main__":
    unittest.main()
