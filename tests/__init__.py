"""Heimdall test suite.

Safety net: refuse to start the test process if a live WDGoWars API
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
    # Mirror heimdall._config_dir() — without importing heimdall (which
    # depends on gungnir, may not be present in a minimal test env).
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
