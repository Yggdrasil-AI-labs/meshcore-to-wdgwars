"""Tests for the v0.4.5 wrapper-refresh half of --update.

Background: the raw-download (ZIP install) update path historically
fetched only heimdall.py + requirements.txt, so a bug fix that lived in
run/setup/update .sh/.bat could never reach ZIP-installed users through
--update (family bug, first documented 2026-06-04). --update now
refreshes the six wrapper scripts too, from a hard-coded list. Same
shape as the Muninn / wigle-to-wdgwars implementations modulo naming.
"""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import heimdall


class RefreshWrappersTests(unittest.TestCase):
    def _tree_with(self, names: list[str]) -> tempfile.TemporaryDirectory:
        td = tempfile.TemporaryDirectory()
        for name in names:
            (Path(td.name) / name).write_text("old\n", encoding="utf-8")
        return td

    def test_refreshes_only_wrappers_present_on_disk(self) -> None:
        present = ["run.sh", "run.bat", "update.sh"]
        with self._tree_with(present) as td, \
             mock.patch.object(heimdall, "_fetch_raw", return_value=True) as fetch, \
             mock.patch("sys.stderr", new=io.StringIO()):
            heimdall._refresh_wrappers(Path(td))
        fetched = sorted(call.args[0] for call in fetch.call_args_list)
        self.assertEqual(fetched, sorted(present))

    def test_deleted_wrappers_are_not_replanted(self) -> None:
        with self._tree_with([]) as td, \
             mock.patch.object(heimdall, "_fetch_raw", return_value=True) as fetch, \
             mock.patch("sys.stderr", new=io.StringIO()):
            heimdall._refresh_wrappers(Path(td))
        fetch.assert_not_called()

    def test_one_failed_wrapper_does_not_stop_the_rest(self) -> None:
        present = ["run.sh", "run.bat", "setup.sh"]

        def fail_run_sh(name, dest):
            return name != "run.sh"

        err = io.StringIO()
        with self._tree_with(present) as td, \
             mock.patch.object(heimdall, "_fetch_raw", side_effect=fail_run_sh) as fetch, \
             mock.patch("sys.stderr", new=err):
            heimdall._refresh_wrappers(Path(td))
        self.assertEqual(len(fetch.call_args_list), 3)
        self.assertIn("run.sh not refreshed", err.getvalue())

    def test_sh_wrappers_get_exec_bit_on_posix(self) -> None:
        present = ["run.sh", "run.bat"]
        with self._tree_with(present) as td:
            # Build the paths before patching os.name: pathlib picks
            # PosixPath vs WindowsPath off os.name at instantiation.
            root = Path(td)
            expected_sh = root / "run.sh"
            with mock.patch.object(heimdall, "_fetch_raw", return_value=True), \
                 mock.patch("os.name", "posix"), \
                 mock.patch("os.chmod") as chmod, \
                 mock.patch("sys.stderr", new=io.StringIO()):
                heimdall._refresh_wrappers(root)
        chmod.assert_called_once_with(expected_sh, 0o700)

    def test_wrapper_list_is_the_full_six_pack(self) -> None:
        """The hard-coded list must cover every shipped wrapper, or the
        family bug quietly comes back for the missing one."""
        self.assertEqual(
            sorted(heimdall.WRAPPER_SCRIPTS),
            ["run.bat", "run.sh", "setup.bat", "setup.sh",
             "update.bat", "update.sh"],
        )


class UpdateFromRawCallsWrapperRefreshTests(unittest.TestCase):
    def _fake_urlopen_returning(self, text: str):
        body = mock.MagicMock()
        body.read.return_value = text.encode("utf-8")
        cm = mock.MagicMock()
        cm.__enter__.return_value = body
        return mock.patch("urllib.request.urlopen", return_value=cm)

    def test_already_latest_branch_refreshes_wrappers(self) -> None:
        same_version = f'__version__ = "{heimdall.__version__}"\n'
        with tempfile.TemporaryDirectory() as td, \
             self._fake_urlopen_returning(same_version), \
             mock.patch.object(heimdall, "_fetch_raw", return_value=True), \
             mock.patch.object(heimdall, "_refresh_wrappers") as refresh, \
             mock.patch.object(heimdall, "_pip_install_requirements"), \
             mock.patch("sys.stderr", new=io.StringIO()):
            rc = heimdall._update_from_raw(Path(td))
        self.assertEqual(rc, 0)
        refresh.assert_called_once()

    def test_updated_branch_refreshes_wrappers(self) -> None:
        newer = '__version__ = "999.0.0"\n'
        with tempfile.TemporaryDirectory() as td, \
             self._fake_urlopen_returning(newer), \
             mock.patch.object(heimdall, "_fetch_raw", return_value=True), \
             mock.patch.object(heimdall, "_refresh_wrappers") as refresh, \
             mock.patch.object(heimdall, "_pip_install_requirements"), \
             mock.patch("sys.stderr", new=io.StringIO()):
            rc = heimdall._update_from_raw(Path(td))
            # Read inside the block — the tempdir is gone after it.
            written = (Path(td) / "heimdall.py").read_text(encoding="utf-8")
        self.assertEqual(rc, 0)
        refresh.assert_called_once()
        # The new main script actually landed on disk.
        self.assertIn("999.0.0", written)


if __name__ == "__main__":
    unittest.main()
