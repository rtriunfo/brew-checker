# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/), with versions matching
the `VERSION` constant in `brew-checker.py`.

## [Unreleased]

### Architecture (engine + TUI)

- **Single detection home.** The MISSING/UNTRACKED/UNINSPECTABLE reconciliation
  algorithm now lives in one place, `reconcile()` in `brew-checker.py`, called by
  both the CLI's `main()` and the TUI's `compute_state()`. It was previously
  duplicated in three throwaway copies (engine helpers, the CLI report, and the
  TUI), which could drift. `owned_apps()` / `untracked_apps()` are now thin
  wrappers over it.
- **Clean engine/library boundary.** The reusable module no longer prints or
  exits as a side effect of pure work:
  - `load_backup()` raises a typed `BrewCheckerError` instead of `SystemExit`.
  - `write_backup()` never writes to stdout; callers that want stdout use the new
    `backup_json()`. `--export -` now routes through it explicitly.
  - `cask_apps()` no longer writes progress to stderr.
  - ANSI colour codes are resolved lazily in the CLI (`_set_colours`) instead of
    at import time, so importing the module has no import side effects.
  - The CLI entry point turns `BrewCheckerError` into a clean `error:` line and a
    non-zero exit (no raw tracebacks).
- **TUI uses the shared engine.** `compute_state()` calls `core.reconcile()` and
  catches `core.BrewCheckerError`; it no longer re-derives the detection logic.
- **TUI backup-mode state consolidated.** The compare/history/drill flags are
  cleared through a single `_reset_backup_mode()` method instead of ad hoc
  resets in six places (view switch, load, compare, history, export), removing a
  drift risk where the footer/`U` binding could become inconsistent with the
  active mode.
- **`BREW_CHECKER_APP_DIRS` override.** The Applications-folder list is now
  configurable via the environment (colon-separated) so the CLI can be driven
  hermetic in tests/scripts; unset behaves exactly as before.

### Tests

- Added a stdlib `unittest` suite (no brew, no textual required for most of it):
  - `tests/test_engine.py` — unit tests for `reconcile()`, snapshot/snapshot-diff,
    backup-diff, backup loading, `store_snapshot` de-dup, `snapshots_match`.
  - `tests/test_cli.py` — hermetic end-to-end tests that run the real
    `brew-checker.py` as a subprocess against a fake `brew` shim
    (`tests/fakebrew.py`), covering the untrusted-tap retry path, `-m`/`-u`
    focused modes, `--json` schema, `--export` (file/stdout), `--diff`,
    `--diff-snapshots`, bad-backup error handling, and snapshot exit codes.
  - `tests/test_tui.py` — headless Textual Pilot sessions driving the real TUI
    app (reconcile render, cask upgrades, backup export).
- Added the shared fake-`brew` harness in `tests/fakebrew.py`.

## Earlier (already in the tree)

This changelog is being introduced during the "reconcile()" refactor; the
feature history below is captured from the existing commit log.

- `e5d615f` — Show a formatted date column in the backup history view.
- `efd5a73` — Add a Quick start section to the README.
- `326d361` — Add a descriptive release body (install command + asset
  explanation).
- `96e6600` — Fix release workflow: tolerate no-op version bump on first release.
- `c6d7e4e` — Add Homebrew distribution (version constant + automated release
  pipeline).
- `63ce96e` — Add `--json` report output and the TUI snapshot history view.
- `620d3c7` — Add snapshot-to-snapshot compare (`--diff-snapshots` + TUI compare
  mode).
- `47b32e8` — Add `snapshot-sync.sh` cron wrapper; make unchanged snapshots a
  no-op.
- `143fe2b` — Add de-duplicated store snapshots (`--snapshot`, shared by TUI
  export).
- `41e8f76` — Record untracked apps in backups as a read-only log.