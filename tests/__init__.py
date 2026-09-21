"""Heimdall test suite.

Safety net: refuse to start the test process if a live WDGWars API
key is configured at the canonical Heimdall path. Tests that exercise
upload paths read the same key as production runs, so a stray test
invocation can post synthetic data to LOCOSP's prod. Mirrors the
guard added to Muninn after the 2026-06-01 phantom-aircraft incident.

To run tests with a real key present, opt in:

    HEIMDALL_TEST_ALLOW_LIVE_KEY=1 python -m unittest discover tests/
"""
from __future__ import annotations
import os
import sys
from pathlib import Path


def _check_live_key_guard() -> None:
    if os.environ.get("HEIMDALL_TEST_ALLOW_LIVE_KEY") == "1":
        return
    # Mirror heimdall._config_dir(), without importing heimdall, so the
    # guard can never be skipped by an import-time error in the module
    # under test. (Heimdall itself is pure stdlib, no gungnir here.)
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(
            Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(
            Path.home() / ".config")
    key_file = Path(base) / "heimdall" / "api.key"
    if not key_file.exists():
        return
    sys.stderr.write(
        "\n"
        "================================================================\n"
        " Heimdall test suite: live API key detected, refusing to run.\n"
        "================================================================\n"
        f" Found: {key_file}\n"
        "\n"
        " Tests that exercise the upload path will read this key and\n"
        " post synthetic data to LOCOSP's production endpoint.\n"
        "\n"
        " To run tests anyway:\n"
        "\n"
        "     HEIMDALL_TEST_ALLOW_LIVE_KEY=1 python -m unittest discover tests/\n"
        "\n"
        " To run tests with no key risk:\n"
        "\n"
        f"     mv {key_file} {key_file}.bak\n"
        "================================================================\n"
        "\n"
    )
    sys.exit(2)


_check_live_key_guard()


def _isolate_holds_state() -> None:
    """Keep the already-sent holds out of the operator's real config dir.

    The live-key guard above stops the suite posting to the real account.
    This stops it WRITING state into it, which is a separate hole and it
    was open the moment the holds gate landed: the first test to upload
    recorded its fixture node_ids into the operator's own holds file, the
    next test's nodes were then held, and its upload never happened. The
    failure looked like a bug in the code under test and survived between
    runs, which is the worst combination.

    Reset per test, because holds persist by design and one test's upload
    must not decide another's outcome.
    """
    import atexit
    import shutil
    import tempfile
    import unittest

    try:
        import gungnir.holds as holds
    except Exception:
        return  # gate inactive here; nothing to isolate

    tmp = tempfile.mkdtemp(prefix="heimdall-tests-holds-")
    atexit.register(shutil.rmtree, tmp, True)
    holds._path = lambda tool: Path(tmp) / f"{tool}-holds.json"

    _real_run = unittest.TestCase.run

    def _run_with_clean_state(self, *a, **kw):
        for stale in Path(tmp).glob("*.json"):
            try:
                stale.unlink()
            except OSError:
                pass
        return _real_run(self, *a, **kw)

    unittest.TestCase.run = _run_with_clean_state


_isolate_holds_state()
