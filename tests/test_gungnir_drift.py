"""Make Heimdall's hand-porting debt announce itself.

Heimdall inlines its own HMAC transport and does not depend on gungnir
(module docstring, 2026-06-03 family audit). The cost the audit accepted
is that a transport fix landing in gungnir reaches this file only when a
human carries it over.

Nothing announced that debt. `check_deliberate_skip` shipped in gungnir
v0.1.4 to stop a server's "already uploaded recently" reply being reported
as a failed upload, Heimdall never got it, and gungnir's own README listed
Heimdall as a consumer, so the gap looked closed from both sides.

This test fails when gungnir moves past `GUNGNIR_RECONCILED_AT`. The fix
is not to silence it: read gungnir's changelog for the releases in
between, port anything that touches transport behaviour or decide in
writing that it does not apply, then bump the marker.

Skipped wherever gungnir is not installed, which includes CI. That is
deliberate and it is the weakness of this check: it only fires on a
machine that happens to have the library. It still beats nothing, which
is what was there before.

Run: HEIMDALL_TEST_ALLOW_LIVE_KEY=1 python -m pytest tests/test_gungnir_drift.py
"""
from __future__ import annotations

import unittest

import heimdall

try:
    import gungnir
    GUNGNIR_VERSION = getattr(gungnir, "__version__", "")
except Exception:
    GUNGNIR_VERSION = ""


def _parts(v: str) -> tuple[int, ...]:
    out = []
    for chunk in (v or "").lstrip("v").strip().split("."):
        digits = ""
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


class GungnirDriftTests(unittest.TestCase):
    @unittest.skipUnless(GUNGNIR_VERSION, "gungnir not installed here")
    def test_gungnir_has_not_moved_past_the_last_reconciliation(self):
        have, marked = _parts(GUNGNIR_VERSION), _parts(
            heimdall.GUNGNIR_RECONCILED_AT)
        self.assertTrue(marked, "GUNGNIR_RECONCILED_AT must be a version")
        self.assertLessEqual(
            have, marked,
            f"gungnir is at {GUNGNIR_VERSION} but Heimdall's transport was "
            f"last reconciled against {heimdall.GUNGNIR_RECONCILED_AT}. "
            f"Heimdall inlines its own transport, so nothing carries a "
            f"gungnir fix over automatically. Read gungnir's CHANGELOG for "
            f"the releases in between, port anything affecting transport "
            f"behaviour (or record why it does not apply), then bump "
            f"GUNGNIR_RECONCILED_AT. Do not just raise the marker.")

    def test_the_marker_is_parseable(self):
        # Runs everywhere: a marker nobody can parse is a check that
        # silently never fires, which is the failure mode this file exists
        # to prevent in the first place.
        self.assertTrue(_parts(heimdall.GUNGNIR_RECONCILED_AT),
                        "GUNGNIR_RECONCILED_AT is unparseable, so the drift "
                        "check can never fire")



class DeliberateSkipTests(unittest.TestCase):
    """Ported by hand from gungnir.diagnostics while reconciling v0.4.1.

    The server answers a payload it has already taken with 200, ok:true,
    every counter zero, and an explanation in the clear. Heimdall never
    had gungnir's bug of calling that a failed upload, but it did print
    its "gave no verdict" note, telling the operator the server refused
    to account for their nodes when it had accounted for them earlier.
    """

    def test_the_markers_match_gungnir(self):
        # Drift here is silent: a marker gungnir adds and Heimdall does
        # not will simply stop being recognised.
        try:
            from gungnir.diagnostics import DELIBERATE_SKIP_MARKERS
        except Exception:
            self.skipTest("gungnir not installed here")
        self.assertEqual(set(heimdall._DELIBERATE_SKIP_MARKERS),
                         set(DELIBERATE_SKIP_MARKERS))

    def test_recognises_the_servers_words(self):
        for field in ("info", "message", "note"):
            with self.subTest(field=field):
                self.assertTrue(heimdall._deliberate_skip(
                    {field: "This payload was already uploaded recently."}))

    def test_is_not_fooled_by_zero_counters_alone(self):
        # Zero counters with no explanation is a different thing and must
        # not be reported as a deliberate skip.
        self.assertIsNone(heimdall._deliberate_skip(
            {"ok": True, "meshcore_imported": 0, "meshcore_already_seen": 0}))

    def test_a_normal_response_is_not_a_skip(self):
        self.assertIsNone(heimdall._deliberate_skip(
            {"info": "imported 3 nodes"}))

if __name__ == "__main__":
    unittest.main()
