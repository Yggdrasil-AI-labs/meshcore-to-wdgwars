"""Security-posture regression tests.

Like its sibling wigle-to-wdgwars (and unlike adsb-to-wdgwars / Muninn, which
had 21 SonarCloud SAST findings to remediate), Heimdall had nothing to fix
when its CI quality gate was added: the scheduler renderers shell-quote every
argument (including the user-supplied CSV path), the schedule argv keeps the
API key off the command line, and `save_key` refuses to write through a symlink
and creates the key file with mode 600. See SECURITY-FINDINGS.md for the review.

These tests LOCK IN that posture so a future refactor can't quietly regress it.
All tests are pure / filesystem-local: nothing uploads, installs a real
scheduler entry, or touches the network.

Run: HEIMDALL_TEST_ALLOW_LIVE_KEY=1 python -m unittest tests.test_security
"""
from __future__ import annotations
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import heimdall  # noqa: E402


PY = "/usr/bin/python3"
SCRIPT = Path("/opt/heimdall/heimdall.py")


class ShellQuotingTests(unittest.TestCase):
    """_shell_quote is what stands between an awkward CSV path and a broken
    (or injectable) systemd/cron command line."""

    def test_plain_token_unquoted(self):
        self.assertEqual(heimdall._shell_quote("nightly.csv"), "nightly.csv")

    def test_empty_becomes_quoted_empty(self):
        self.assertEqual(heimdall._shell_quote(""), "''")

    def test_metacharacters_are_quoted(self):
        for bad in ("a;b", "a b", "a$(b)", "a`b`", "a|b", "a&b", "a>b"):
            q = heimdall._shell_quote(bad)
            self.assertNotEqual(q, bad, f"{bad!r} must be quoted")
            self.assertTrue(q.startswith("'"), f"{bad!r} -> {q!r}")

    def test_embedded_single_quote_is_escaped(self):
        self.assertEqual(heimdall._shell_quote("a'b"), "'a'\"'\"'b'")


class RendererCsvPathQuotingTests(unittest.TestCase):
    """Heimdall's scheduler interpolates a user-supplied CSV path into the
    daily command, so that path must always be shell-quoted."""

    def test_cron_quotes_metachar_csv_path(self):
        evil = Path("/data/mesh/$(reboot).csv")
        line = heimdall.render_cron_line("03:00", evil, PY, SCRIPT)
        self.assertIn(heimdall._shell_quote(str(evil)), line)
        self.assertNotIn(f" {evil} ", line)  # never a bare token

    def test_systemd_quotes_metachar_csv_path(self):
        evil = Path("/data/mesh dir/nightly.csv")
        units = heimdall.render_systemd_units("03:00", evil, PY, SCRIPT)
        self.assertIn(heimdall._shell_quote(str(evil)), units["service"])

    def test_time_is_validated_before_rendering(self):
        for bad in ("24:00", "$(reboot)", "3:0;reboot", "abc"):
            with self.assertRaises(ValueError, msg=bad):
                heimdall.render_cron_line(bad, Path("/data/x.csv"), PY, SCRIPT)


class ScheduleArgvSecretTests(unittest.TestCase):
    """The scheduled command must read the saved key from disk, never embed it
    on the command line (the unit file / crontab / schtasks output are all
    readable by other local processes)."""

    def test_argv_never_carries_key_flags(self):
        argv = heimdall._schedule_argv(Path("/data/mesh/nightly.csv"))
        self.assertNotIn("--key", argv)
        self.assertNotIn("--api-key", argv)
        self.assertNotIn("--save-key", argv)

    def test_argv_includes_the_csv_path(self):
        csv = Path("/data/mesh/nightly.csv")
        argv = heimdall._schedule_argv(csv)
        # str(Path(...)) so the assertion matches the host's path separator.
        self.assertIn(str(csv), [str(a) for a in argv])

    def test_rendered_units_contain_no_key_flags(self):
        units = heimdall.render_systemd_units(
            "03:00", Path("/data/x.csv"), PY, SCRIPT)
        line = heimdall.render_cron_line(
            "03:00", Path("/data/x.csv"), PY, SCRIPT)
        for blob in (units["service"], units["timer"], line):
            self.assertNotIn("--key", blob)
            self.assertNotIn("--api-key", blob)


class SaveKeyTests(unittest.TestCase):
    """save_key is the only writer of the API key file."""

    def test_refuses_to_write_through_symlink(self):
        with tempfile.TemporaryDirectory() as d, \
                tempfile.TemporaryDirectory() as other:
            target = Path(other).resolve() / "real.key"
            link = Path(d).resolve() / "api.key"
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError, AttributeError):
                self.skipTest("symlink creation not permitted on this host")
            with mock.patch.object(heimdall, "_key_path", return_value=link):
                with self.assertRaises(SystemExit):
                    heimdall.save_key("s3cret")
            self.assertFalse(target.exists(),
                             "secret was written through the symlink")

    def test_writes_content_stripped_with_newline(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "api.key"
            with mock.patch.object(heimdall, "_key_path", return_value=p):
                heimdall.save_key("  s3cret  ")
            self.assertEqual(p.read_text(), "s3cret\n")

    @unittest.skipIf(os.name == "nt", "POSIX file mode not enforced on Windows")
    def test_mode_is_owner_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "api.key"
            with mock.patch.object(heimdall, "_key_path", return_value=p):
                heimdall.save_key("s3cret")
            mode = stat.S_IMODE(p.stat().st_mode)
            self.assertEqual(mode & 0o077, 0,
                             f"secret file is group/other-accessible: {oct(mode)}")


if __name__ == "__main__":
    unittest.main()
