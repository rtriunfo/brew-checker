"""End-to-end CLI integration tests.

These run the real `brew-checker.py` as a subprocess against a *fake* `brew`
shim on PATH and a controlled `BREW_CHECKER_APP_DIRS` / `HOME`, so they exercise
the actual public CLI interface (argparse, stdout/stderr, exit codes) without
touching a real Homebrew install. They rely on the engine's
`BREW_CHECKER_APP_DIRS` env override for hermetic app-folder scans.

Run with:  python3 -m unittest discover -s tests -t .
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

from . import fakebrew

_ENGINE = pathlib.Path(__file__).resolve().parent.parent / "brew-checker.py"

CASKS = fakebrew.CASKS
FORMULAE = fakebrew.FORMULAE
TAPS = fakebrew.TAPS
DISK_APPS = fakebrew.DISK_APPS


class CLIFixture(unittest.TestCase):
    """Runs the real CLI binary against a fake brew in a temp HOME/app dir."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.apps_dir = fakebrew.make_apps_dir(self.tmp)
        self.env = fakebrew.make_env(self.tmp, self.apps_dir)

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(_ENGINE), *args],
            capture_output=True, text=True, env=self.env)

    def helper_write(self, name, content):
        p = os.path.join(self.tmp, name)
        with open(p, "w") as f:
            f.write(content)
        return p

    def snapshot(self, formulae=None, casks=None, apps=None):
        return {
            "schema": 2,
            "meta": {"host": "testhost", "date": "2026-01-01T00:00:00"},
            "taps": ["brewtap"],
            "formulae": formulae if formulae is not None else ["formula1"],
            "casks": casks if casks is not None else ["basecask"],
            "apps": apps if apps is not None else DISK_APPS,
        }


class ReconcileInterfaceTest(CLIFixture):
    def test_version(self):
        p = self.run_cli("--version")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "brew-checker 0.1.0")

    def test_default_report_has_both_sections_and_missing_exits_1(self):
        p = self.run_cli()
        self.assertEqual(p.returncode, 1)          # something is missing
        self.assertIn("MISSING", p.stdout)
        self.assertIn("MissingGame.app", p.stdout) # misscask's app is gone
        self.assertIn("UNTRACKED", p.stdout)
        self.assertIn("UntrackedApp.app", p.stdout)
        self.assertIn("untrustedcask", p.stdout)   # reported, not silently dropped
        self.assertNotIn("BaseCask.app", p.stdout) # present app -> not listed

    def test_minus_m_focused(self):
        p = self.run_cli("-m")
        self.assertEqual(p.returncode, 1)
        self.assertIn("MISSING", p.stdout)
        self.assertNotIn("UNTRACKED", p.stdout)

    def test_minus_u_focused(self):
        p = self.run_cli("-u")
        # -u shows only untracked, but exit code still reflects MISSING casks.
        self.assertEqual(p.returncode, 1)
        self.assertIn("UNTRACKED", p.stdout)
        self.assertNotIn("MISSING", p.stdout)

    def test_json_schema(self):
        p = self.run_cli("--json")
        self.assertEqual(p.returncode, 1)
        data = json.loads(p.stdout)
        self.assertEqual(data["casks_installed"], len(CASKS))
        self.assertEqual(data["missing"],
                         [{"token": "misscask", "apps": ["MissingGame.app"]}])
        self.assertEqual(data["untracked"], ["UntrackedApp.app"])
        self.assertEqual(data["no_app_casks"], 1)               # "noapp"
        self.assertEqual(data["uninspectable"], ["untrustedcask"])


class BackupFlowTest(CLIFixture):
    def test_export_to_file_writes_nothing_to_stdout(self):
        out = os.path.join(self.tmp, "backup.json")
        p = self.run_cli("--export", out)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "")  # file export leaves stdout clean
        with open(out) as f:
            data = json.load(f)
        self.assertEqual(sorted(data["formulae"]), sorted(FORMULAE))
        self.assertEqual(sorted(data["taps"]), sorted(TAPS))
        self.assertEqual(sorted(data["casks"]), sorted(CASKS))
        # `apps` is the untracked subset only (BaseCask.app is cask-owned).
        self.assertEqual(sorted(data["apps"]), ["UntrackedApp.app"])

    def test_export_to_stdout_via_dash(self):
        p = self.run_cli("--export", "-")
        self.assertEqual(p.returncode, 0)
        data = json.loads(p.stdout)  # JSON on stdout is the intended behaviour
        self.assertIn("casks", data)

    def test_diff_against_machine(self):
        # A backup with a formula this machine lacks -> a restore candidate.
        backup = self.helper_write("b.json", json.dumps(
            self.snapshot(formulae=["phantom-formula"])))
        p = self.run_cli("--diff", backup)
        self.assertEqual(p.returncode, 1)
        self.assertIn("MISSING formula", p.stdout)
        self.assertIn("phantom-formula", p.stdout)

    def test_diff_snapshots_identical_exits_0(self):
        a = self.helper_write("a.json", json.dumps(self.snapshot()))
        b = self.helper_write("b.json", json.dumps(self.snapshot()))
        p = self.run_cli("--diff-snapshots", a, b)
        self.assertEqual(p.returncode, 0)
        self.assertIn("identical", p.stdout)

    def test_diff_snapshots_different_exits_1(self):
        a = self.helper_write("a.json", json.dumps(self.snapshot()))
        b = self.helper_write("b.json",
                              json.dumps(self.snapshot(formulae=["newform"])))
        p = self.run_cli("--diff-snapshots", a, b)
        self.assertEqual(p.returncode, 1)
        self.assertIn("ADDED", p.stdout)
        self.assertIn("REMOVED", p.stdout)

    def test_bad_backup_file_errors_cleanly(self):
        p = self.run_cli("--diff", os.path.join(self.tmp, "nope.json"))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("error", p.stderr.lower())

    def test_snapshot_dedup_exit_codes(self):
        first = self.run_cli("--snapshot")
        self.assertEqual(first.returncode, 10)             # created
        self.assertTrue(first.stdout.startswith("created "))
        backup_dir = os.path.join(self.tmp, ".brew-checker", "backups")
        self.assertEqual(len(os.listdir(backup_dir)), 1)

        second = self.run_cli("--snapshot")
        self.assertEqual(second.returncode, 0)            # unchanged (no-op)
        self.assertTrue(second.stdout.startswith("unchanged "))
        self.assertEqual(len(os.listdir(backup_dir)), 1)  # no new file


if __name__ == "__main__":
    unittest.main()