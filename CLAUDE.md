# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A read-only reconciler between installed Homebrew **casks** and the `.app`
bundles actually present in `/Applications` and `~/Applications`. It reports
mismatches in both directions (MISSING casks / UNTRACKED apps); it never
installs, removes, or modifies anything. See `README.md` for the full user-facing
behavior and the meaning of MISSING / UNTRACKED / UNINSPECTABLE.

## Architecture

Two files, deliberately split:

- **`brew-checker.py`** — the CLI and the *detection engine*. Pure Python
  stdlib, zero dependencies. This file must stay dependency-free and standalone;
  do not import `textual` or anything third-party into it. Core flow (`main`):
  `installed_casks()` → `cask_apps()` (maps cask token → expected `.app`
  basenames) → compared against `present_apps()` to produce MISSING/UNTRACKED.
  Progress logging goes to **stderr** (`log()`); the report goes to stdout;
  colours auto-strip when stdout isn't a TTY. Exit code 1 when anything is
  missing, 0 when clean. Also hosts the read-only **backup** engine
  (`build_backup`/`write_backup`/`load_backup`/`diff_backup`, plus
  `installed_formulae`/`installed_taps`) and the `--export`/`--diff` CLI actions.
  Snapshots live in `BACKUP_DIR` (`~/.brew-checker/backups/`); `list_backups()`
  (tolerant — skips foreign JSON) and `default_backup_path()` (timestamped name)
  back the TUI picker.

- **`brew-checker-tui.py`** — an interactive [Textual](https://textual.textualize.io/)
  app that *reuses* the engine. It imports `brew-checker.py` by path via
  `importlib.util.spec_from_file_location` (the hyphenated filename isn't a valid
  module name), exposing it as `core`. It calls `core.installed_casks`,
  `core.cask_apps`, `core.present_apps`, etc. `textual` is the only third-party
  dependency in the whole project, and only the TUI uses it.

### Key engine detail

`cask_apps()` does **one** bulk `brew info --cask --json=v2` call for speed
(brew has slow startup). If a cask from an untrusted tap makes that call fail, it
parses the offending token(s) out of stderr, drops them, and retries the rest —
so one bad cask can't blank the report and it stays at ~a couple of brew calls
total. Dropped tokens are returned as "uninspectable" rather than vanishing.

### TUI structure

- Four views switched with `1`–`4` or cycled with `v` (`_VIEWS`): **Reconcile**
  (MISSING/UNTRACKED), **Cask versions & upgrades**, **Formula versions &
  upgrades**, and **Backup & restore**. Direct navigation uses `action_goto_view`;
  both paths funnel through `_switch_to_view`. The command palette (`ctrl+p`,
  Textual's built-in) includes "Go to: …" entries via `get_system_commands`. The
  two upgrade views share `compute_upgrades(kind, greedy)` (backed by `brew
  outdated --cask/--formula`); greedy mode is cask-only. Row-key prefixes
  distinguish rows (`m:`/`u:` reconcile, `c:` casks, `f:` formulae, `bf:`/`bc:`
  backup-missing formula/cask).
- **Live filter** (`/` binding): an `Input` widget (id=`search`) docks at the
  bottom, hidden by default. Typing filters the `DataTable` in real-time via
  `_render_table`, which reads `_all_rows` (built by each `_populate*` method)
  and skips rows whose `searchable_text` doesn't contain the query
  (case-insensitive). `Enter` hides the bar but keeps the filter; `Esc` clears
  and hides it. Switching views clears the filter.
- **Backup view** renders a loaded backup's full inventory against the machine
  (via the engine's `diff_backup` plus the backup's own item lists): one row per
  item tagged INSTALLED (info-only), MISSING (selectable/installable, sorted
  first), or EXTRA (info-only) — so a matching backup still shows its whole list
  rather than a blank table. The backup is chosen with `l` (`action_load` →
  `BackupPickerScreen`, an
  `OptionList` modal over `core.list_backups()`); the picker auto-opens on
  entering the view with nothing loaded, and a launch-arg path bypasses it. `U`
  reuses `action_upgrade`, dispatching to `_run_restore` (taps missing taps, then
  `brew install --formula/--cask` the selection); `e` (`action_export`) writes a
  fresh timestamped snapshot into `BACKUP_DIR` and loads it.
- Row keys are prefixed (e.g. `missing:`, `untracked:`) so `_selected(prefix)`
  can filter selections; `check_action` gates which key bindings show per view.
- **State-changing brew commands run in a suspended terminal**, not in the log
  pane: `_run_in_terminal` uses `self.suspend()` so brew gets the real TTY. This
  is intentional — some casks invoke `sudo` mid-install and need to prompt for a
  password. All commands are shown and confirmed via `ConfirmScreen` first.

## Commands

```sh
# Run the CLI (stdlib only, no setup)
./brew-checker.py                 # or: brew-checker  (if symlinked onto PATH)

# TUI — one-time setup
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Run the TUI (uses the venv, works from any dir)
./run-tui.sh

# Build the single-file executable (bundles textual via shiv)
.venv/bin/pip install -r requirements-dev.txt   # one-time: build tooling
./build-tui.sh                                    # -> dist/brew-checker-tui
```

There is no test suite, linter, or CI configured. `dist/` is git-ignored — commit
source and rebuild, never check in the binary.

**Always rebuild `dist/brew-checker-tui` after changing the TUI.** The user tests
against that bundled binary, so a source edit that isn't rebuilt won't show up
when they run it. After any change to `brew-checker-tui.py` (or the engine it
imports), run `./build-tui.sh` before reporting the change as ready. The binary
stays git-ignored — this is purely so the local artifact matches the source.

## Conventions / gotchas

- Keep `brew-checker.py` importable by path: `build-tui.sh` copies it to a
  staging dir under its hyphenated name (loaded by path) while copying the TUI to
  `brew_checker_tui.py` (an import-safe name) for shiv's entry point. Renaming
  either file means updating `build-tui.sh` and the `_CORE_PATH` lookup in the
  TUI.
- Any change touching the report format or the MISSING/UNTRACKED/UNINSPECTABLE
  semantics should be reflected in `README.md`, which documents them in detail.
