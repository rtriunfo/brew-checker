"""Drive the Textual TUI headlessly against the fake `brew` machine.

The CLI tests (test_cli.py) cover the engine through the real subprocess CLI.
These cover the TUI's own boundary: the actual App bootstrap, its compute
helpers going straight through `core.reconcile()` (the shared single home), and
a Textual Pilot session rendering the Reconcile view against the fake machine.

Skips cleanly when `textual` isn't installed, so the plain
`python3 -m unittest discover`-style default run still works without it
(CLI/tests test_cli.py require only the stdlib).
"""

import importlib.util
import os
import pathlib
import shutil
import tempfile
import unittest

from . import fakebrew

try:
    import textual  # noqa: F401
except ImportError:
    textual = None  # type: ignore

_HERE = pathlib.Path(__file__).resolve().parent.parent
_TUI = _HERE / "brew-checker-tui.py"


@unittest.skipIf(textual is None, "textual not installed; skipping TUI tests")
class TUIBoundaryTest(unittest.IsolatedAsyncioTestCase):
    """Imports the real TUI against the fake-brew environment and drives it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.apps_dir = fakebrew.make_apps_dir(self.tmp)
        env = fakebrew.make_env(self.tmp, self.apps_dir)

        # The TUI imports the engine by path; core reads APP_DIRS/BACKUP_DIR/PATH
        # at import time, so the fake env must be in os.environ before importing.
        self._saved = {k: os.environ.get(k)
                       for k in ("BREW_CHECKER_APP_DIRS", "HOME", "PATH")}
        os.environ["BREW_CHECKER_APP_DIRS"] = env["BREW_CHECKER_APP_DIRS"]
        os.environ["HOME"] = env["HOME"]
        os.environ["PATH"] = env["PATH"]

        spec = importlib.util.spec_from_file_location("brew_checker_tui_test", _TUI)
        assert spec is not None and spec.loader is not None
        self.tui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tui)

    def tearDown(self):
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def test_compute_state_delegates_to_core_reconcile(self):
        # The TUI's scan helper must go through the engine's single reconcile().
        missing, untracked, unknown = self.tui.compute_state()
        self.assertEqual(missing, [("misscask", ["MissingGame.app"])])
        self.assertEqual(untracked, ["UntrackedApp.app"])
        self.assertEqual(unknown, ["untrustedcask"])

    async def test_pilot_renders_reconcile_view(self):
        app = self.tui.BrewCheckerTUI()

        def row_count(table):
            try:
                return table.row_count
            except Exception:
                return -1

        async with app.run_test(size=(110, 30)) as pilot:
            # Default initial view is "casks"; it should populate via fake brew.
            self.assertEqual(app.view, "casks")
            for _ in range(30):
                await pilot.pause()
                if app.table.loading is False and row_count(app.table) > 0:
                    break
            self.assertGreater(row_count(app.table), 0)

            # Drive to the Reconcile view and wait for its worker to finish.
            app.action_goto_reconcile()
            for _ in range(30):
                await pilot.pause()
                if app.view == "reconcile" and app.table.loading is False:
                    break

            self.assertEqual(app.view, "reconcile")
            # The real reconcile path (TUI -> core.reconcile -> fake brew).
            self.assertEqual(app._missing, [("misscask", ["MissingGame.app"])])
            self.assertEqual(app._untracked, ["UntrackedApp.app"])
            self.assertEqual(app._unknown, ["untrustedcask"])
            self.assertGreater(row_count(app.table), 0)


if __name__ == "__main__":
    unittest.main()