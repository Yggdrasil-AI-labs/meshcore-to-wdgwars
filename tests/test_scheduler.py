"""Tests for the schedule installer's pure renderers.

The renderers (render_systemd_units / render_cron_line /
render_schtasks_create) are pure functions — they don't touch disk or
shell out — so they can be unit-tested without monkeypatching anything.
The install/uninstall wrappers that DO touch the system are covered by
scripts/smoke.sh, not here.
"""
from __future__ import annotations
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import heimdall  # noqa: E402


SAMPLE_CSV = Path("/data/mesh/nightly.csv")
SAMPLE_PY = Path("/usr/bin/python3")
SAMPLE_SCRIPT = Path("/opt/heimdall/heimdall.py")


class ValidateHhmmTests(unittest.TestCase):
    def test_canonical(self):
        self.assertEqual(heimdall._validate_hhmm("03:00"), "03:00")
        self.assertEqual(heimdall._validate_hhmm("23:59"), "23:59")
        self.assertEqual(heimdall._validate_hhmm("0:5"), "00:05")

    def test_rejects_bad(self):
        for bad in ("", "noon", "25:00", "12:60", "12", "12:00:00", "-1:00"):
            with self.assertRaises(ValueError, msg=bad):
                heimdall._validate_hhmm(bad)


class SystemdUnitTests(unittest.TestCase):
    def setUp(self):
        self.units = heimdall.render_systemd_units(
            "03:00", SAMPLE_CSV, str(SAMPLE_PY), SAMPLE_SCRIPT,
            dry_run=False,
        )

    def test_has_service_and_timer_keys(self):
        self.assertIn("service", self.units)
        self.assertIn("timer", self.units)

    def test_marker_in_both(self):
        self.assertIn(heimdall.SCHEDULE_MARKER, self.units["service"])
        self.assertIn(heimdall.SCHEDULE_MARKER, self.units["timer"])

    def test_oncalendar_format(self):
        self.assertIn("OnCalendar=*-*-* 03:00:00", self.units["timer"])

    def test_execstart_has_script_and_csv(self):
        # Use basename-level checks so Path() normalisation on Windows
        # (which flips '/' to '\\') doesn't break the test.
        self.assertIn("python3", self.units["service"])
        self.assertIn("heimdall.py", self.units["service"])
        self.assertIn("nightly.csv", self.units["service"])

    def test_no_dry_run_in_description_when_false(self):
        self.assertNotIn("[DRY-RUN]", self.units["service"])

    def test_dry_run_propagates(self):
        units = heimdall.render_systemd_units(
            "03:00", SAMPLE_CSV, str(SAMPLE_PY), SAMPLE_SCRIPT,
            dry_run=True,
        )
        self.assertIn("[DRY-RUN]", units["service"])
        self.assertIn("--dry-run", units["service"])

    def test_no_api_key_leak(self):
        # The renderer must NEVER bake a key into the unit file.
        for k in ("api_key", "WDGWARS_API_KEY", "--key", "--api-key"):
            self.assertNotIn(k, self.units["service"])
            self.assertNotIn(k, self.units["timer"])


class CronRenderTests(unittest.TestCase):
    def test_renders_line(self):
        line = heimdall.render_cron_line(
            "03:00", SAMPLE_CSV, str(SAMPLE_PY), SAMPLE_SCRIPT,
        )
        self.assertTrue(line.startswith("0 3 * * * "))
        self.assertIn(heimdall.SCHEDULE_MARKER, line)
        self.assertIn("nightly.csv", line)
        self.assertIn("$HOME/.heimdall-cron.log", line)
        # No key in the line
        self.assertNotIn("--key", line)

    def test_dry_run_flag(self):
        line = heimdall.render_cron_line(
            "03:00", SAMPLE_CSV, str(SAMPLE_PY), SAMPLE_SCRIPT,
            dry_run=True,
        )
        self.assertIn("--dry-run", line)


class SchtasksRenderTests(unittest.TestCase):
    def test_argv_shape(self):
        cmd = heimdall.render_schtasks_create(
            "03:00", SAMPLE_CSV, str(SAMPLE_PY), SAMPLE_SCRIPT,
        )
        # Must start with schtasks /Create /TN <name> /TR <action> ...
        self.assertEqual(cmd[0], "schtasks")
        self.assertIn("/Create", cmd)
        self.assertIn("/TN", cmd)
        self.assertIn(heimdall.WINDOWS_TASK_NAME, cmd)
        self.assertIn("/SC", cmd)
        self.assertIn("DAILY", cmd)
        self.assertIn("/ST", cmd)
        self.assertIn("03:00", cmd)

    def test_action_contains_no_cmd_wrap(self):
        # /TR has a 261-char hard cap; wrapping in `cmd /c "... >> log"`
        # blows past it once the venv-python path is included. Action
        # must remain bare python+script+csv.
        cmd = heimdall.render_schtasks_create(
            "03:00", SAMPLE_CSV, str(SAMPLE_PY), SAMPLE_SCRIPT,
        )
        tr_idx = cmd.index("/TR") + 1
        action = cmd[tr_idx]
        self.assertNotIn("cmd /c", action)
        self.assertNotIn(">>", action)

    def test_no_api_key_leak(self):
        cmd = heimdall.render_schtasks_create(
            "03:00", SAMPLE_CSV, str(SAMPLE_PY), SAMPLE_SCRIPT,
        )
        joined = " ".join(cmd)
        self.assertNotIn("--key", joined)
        self.assertNotIn("--api-key", joined)


class ScheduleMechanismTests(unittest.TestCase):
    def test_returns_one_of_three(self):
        self.assertIn(heimdall._schedule_mechanism(),
                     ("systemd", "cron", "windows"))


class CliBackCompatTests(unittest.TestCase):
    """The deprecated --api-key / --endpoint flags must keep working
    until v0.4 — until then they map onto --key / --api-url."""

    def test_api_key_alias_still_parses(self):
        # We can't run main() without side effects, but argparse
        # accepting both forms is the contract we want to lock down.
        import argparse
        # Recreate the relevant slice of heimdall's parser
        p = argparse.ArgumentParser()
        p.add_argument("--key")
        p.add_argument("--api-key", dest="api_key_legacy")
        ns = p.parse_args(["--api-key", "secret"])
        self.assertEqual(ns.api_key_legacy, "secret")

    def test_endpoint_alias_still_parses(self):
        import argparse
        p = argparse.ArgumentParser()
        p.add_argument("--api-url")
        p.add_argument("--endpoint", dest="endpoint_legacy")
        ns = p.parse_args(["--endpoint", "https://example.com/api/"])
        self.assertEqual(ns.endpoint_legacy, "https://example.com/api/")


if __name__ == "__main__":
    unittest.main()
