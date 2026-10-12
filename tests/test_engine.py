"""Tests for the read-only brew-checker engine.

Run with:  python3 -m unittest discover -s tests -t .

These use only the stdlib (unittest + mock), no brew, no textual, so they run
on any machine with Python 3. The engine is loaded by path (the hyphenated
filename isn't a valid module name), the same way the TUI loads it — which also
means this suite guards that import path.
"""

import importlib.util
import itertools
import json
import os
import pathlib
import re
import shutil
import tempfile
import unittest
from unittest import mock

_HERE = pathlib.Path(__file__).resolve().parent.parent
_ENGINE = _HERE / "brew-checker.py"

_spec = importlib.util.spec_from_file_location("brew_checker_engine", _ENGINE)
assert _spec is not None and _spec.loader is not None
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


class ReconcileTest(unittest.TestCase):
    """The single detection path shared by CLI and TUI."""

    def setUp(self):
        # casks a/b/c are inspectable, "u" is uninspectable (untrusted tap).
        self.tokens = ["a", "b", "c", "u"]
        self.mapping = {"a": ["A.app"], "b": ["B.app"], "c": []}
        self.unknown = ["u"]
        self.present = {"B.app", "Untracked.app"}
        # A.app is gone from disk -> MISSING; B.app present -> owned.
        self.exists = {"B.app": True}

    def run_reconcile(self):
        with mock.patch.object(core, "cask_apps",
                               return_value=(self.mapping, list(self.unknown))), \
             mock.patch.object(core, "app_exists",
                               side_effect=lambda app: self.exists.get(app, False)), \
             mock.patch.object(core, "present_apps", return_value=self.present):
            return core.reconcile(self.tokens)

    def test_basic_missing_untracked_no_app(self):
        r = self.run_reconcile()
        self.assertEqual(r["casks_installed"], 4)
        self.assertEqual(r["missing"], [("a", ["A.app"])])  # a's app is gone
        self.assertEqual(r["untracked"], ["Untracked.app"])
        self.assertEqual(r["no_app_casks"], ["c"])          # c installs no .app
        self.assertEqual(r["uninspectable"], ["u"])
        # owned = every .app a cask is responsible for, present or not.
        self.assertEqual(r["owned_apps"], {"A.app", "B.app"})

    def test_present_app_not_missing(self):
        # "b" installs B.app which is present -> not missing, owned.
        r = self.run_reconcile()
        self.assertNotIn(("b", ["B.app"]), r["missing"])
        self.assertIn("B.app", r["owned_apps"])

    def test_gone_app_still_counted_owned_but_missing(self):
        # "a"'s app is missing, so it is both owned (cask responsible) and MISSING.
        r = self.run_reconcile()
        self.assertIn("A.app", r["owned_apps"])
        self.assertIn("A.app", r["missing"][0][1])

    def test_no_app_owned_but_not_missing(self):
        # "b" installs B.app which is present -> not missing, owned.
        r = self.run_reconcile()
        self.assertNotIn(("b", ["B.app"]), r["missing"])
        self.assertIn("B.app", r["owned_apps"])

    def test_reconcile_defaults_to_installed_casks(self):
        with mock.patch.object(core, "installed_casks", return_value=["x"]), \
             mock.patch.object(core, "cask_apps", return_value=({}, [])), \
             mock.patch.object(core, "present_apps", return_value=set()), \
             mock.patch.object(core, "app_exists", return_value=False):
            r = core.reconcile()  # tokens=None
        self.assertEqual(r["casks_installed"], 1)

    def test_owned_apps_and_untracked_apps_are_derived_from_reconcile(self):
        with mock.patch.object(core, "installed_casks", return_value=self.tokens), \
             mock.patch.object(core, "cask_apps",
                               return_value=(self.mapping, list(self.unknown))), \
             mock.patch.object(core, "app_exists",
                               side_effect=lambda a: self.exists.get(a, False)), \
             mock.patch.object(core, "present_apps", return_value=self.present):
            self.assertEqual(core.owned_apps(self.tokens), {"A.app", "B.app"})
            self.assertEqual(core.untracked_apps(), ["Untracked.app"])


class DiffSnapshotsTest(unittest.TestCase):
    def test_apps_omitted_when_either_side_is_schema1(self):
        a = {"taps": ["t1"], "formulae": ["f1"], "casks": ["c1"]}   # no apps key
        b = {"taps": ["t1", "t2"], "formulae": ["f2"], "casks": [],
             "apps": ["a1"]}
        d = core.diff_snapshots(a, b)
        self.assertNotIn("apps", d)  # one side has no apps -> don't report them
        # (only_in_a, only_in_b) — t1 is in both, so it appears in neither tuple.
        self.assertEqual(d["taps"], ([], ["t2"]))

    def test_apps_included_when_both_record_them(self):
        a = {"taps": [], "formulae": [], "casks": [], "apps": ["x.app"]}
        b = {"taps": [], "formulae": [], "casks": [], "apps": ["x.app", "y.app"]}
        d = core.diff_snapshots(a, b)
        self.assertIn("apps", d)
        self.assertEqual(d["apps"], ([], ["y.app"]))  # only_in_a, only_in_b


class DiffBackupTest(unittest.TestCase):
    def test_missing_and_extra_per_kind(self):
        backup = {"taps": ["t1"], "formulae": ["f1"], "casks": ["c1"],
                  "apps": ["A.app"]}
        with mock.patch.object(core, "installed_taps", return_value=["t1"]), \
             mock.patch.object(core, "installed_formulae", return_value=["f1", "f2"]), \
             mock.patch.object(core, "installed_casks", return_value=["c1", "c2"]), \
             mock.patch.object(core, "present_apps", return_value={"A.app", "X.app"}):
            d = core.diff_backup(backup)
        # tuples are (missing, extra)
        self.assertEqual(d["taps"], ([], []))
        self.assertEqual(d["formulae"], ([], ["f2"]))
        self.assertEqual(d["casks"], ([], ["c2"]))
        self.assertEqual(d["apps"], ([], ["X.app"]))  # apps extra is informational


class LoadBackupTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, name, content):
        p = os.path.join(self.dir, name)
        with open(p, "w") as f:
            f.write(content)
        return p

    def test_valid_backup(self):
        p = self.write("ok.json", json.dumps({"formulae": ["f1"], "casks": ["c1"]}))
        obj = core.load_backup(p)
        self.assertEqual(obj["formulae"], ["f1"])

    def test_invalid_json_raises_typed_error(self):
        p = self.write("bad.json", "not json")
        with self.assertRaises(core.BrewCheckerError):
            core.load_backup(p)

    def test_missing_keys_raises_typed_error(self):
        p = self.write("empty.json", json.dumps({"meta": {}}))
        with self.assertRaises(core.BrewCheckerError):
            core.load_backup(p)


class SnapshotStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.patch_dir = mock.patch.object(core, "BACKUP_DIR", self.dir)
        self.patch_dir.start()
        self.addCleanup(self.patch_dir.stop)

        self.backup = {
            "schema": core.SCHEMA,
            "meta": {"host": "testhost", "date": "2026-01-01T00:00:00"},
            "taps": ["t1"],
            "formulae": ["f1"],
            "casks": ["c1"],
            "apps": ["A.app"],
        }
        self.patch_build = mock.patch.object(core, "build_backup",
                                             return_value=self.backup)
        self.patch_build.start()
        self.addCleanup(self.patch_build.stop)

        self._counter = itertools.count(1)
        self.patch_path = mock.patch.object(
            core, "default_backup_path",
            side_effect=lambda: os.path.join(self.dir, f"b-{next(self._counter)}.json"))
        self.patch_path.start()
        self.addCleanup(self.patch_path.stop)

    def test_unchanged_snapshot_is_a_no_op(self):
        p1, created1 = core.store_snapshot()
        self.assertTrue(created1)
        before = os.listdir(self.dir)
        p2, created2 = core.store_snapshot()
        self.assertFalse(created2)          # same inventory -> no new file
        self.assertEqual(p1, p2)            # points back at the existing file
        self.assertEqual(os.listdir(self.dir), before)

    def test_changed_snapshot_writes_a_new_file(self):
        core.store_snapshot()
        core.build_backup.return_value = {**self.backup, "casks": ["c1", "c2"]}
        p2, created2 = core.store_snapshot()
        self.assertTrue(created2)           # inventory changed -> new snapshot kept
        self.assertEqual(len(os.listdir(self.dir)), 2)  # history preserved


class SnapshotsMatchTest(unittest.TestCase):
    def test_set_semantics_ignore_order_and_meta(self):
        a = {"taps": ["t1"], "formulae": ["f1"], "casks": ["c1"], "apps": ["a.app"]}
        b = {"taps": ["t1"], "formulae": ["f1", "f1"], "casks": [], "apps": []}
        self.assertTrue(core.snapshots_match(a, {**a, "meta": {"host": "x"}}))
        # Same sets but different membership -> not a match
        self.assertFalse(core.snapshots_match(a, b))


if __name__ == "__main__":
    unittest.main()

class PythonFloorTest(unittest.TestCase):
    """The supported-Python floor is declared in a few places; keep them in step."""

    def test_tui_floor_matches_engine(self):
        tui = (_HERE / "brew-checker-tui.py").read_text()
        floor = "_MIN_PYTHON = (%d, %d)" % core.MIN_PYTHON
        self.assertIn(floor, tui)

    def test_ruff_targets_the_floor(self):
        ruff = (_HERE / "ruff.toml").read_text()
        self.assertIn('target-version = "py%d%d"' % core.MIN_PYTHON, ruff)

    def test_ci_tests_the_floor_and_the_formula_python(self):
        workflow = (_HERE / ".github" / "workflows" / "test.yml").read_text()
        self.assertIn('"%d.%d"' % core.MIN_PYTHON, workflow)
        formula = (_HERE / "homebrew" / "brew-checker.rb.tmpl").read_text()
        pinned = re.search(r'depends_on "python@([\d.]+)"', formula).group(1)
        self.assertIn('"%s"' % pinned, workflow)

    def test_guard_rejects_older_python(self):
        with mock.patch("sys.version_info", (3, 8, 18)), \
                mock.patch("sys.version", "3.8.18 (default)"):
            with self.assertRaises(SystemExit) as cm:
                _spec.loader.exec_module(importlib.util.module_from_spec(_spec))
        self.assertEqual(str(cm.exception),
                         "brew-checker needs Python 3.9 or newer (this is 3.8.18)")
