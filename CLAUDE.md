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
  missing, 0 when clean.

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

- Three views cycled with `v` (`_VIEWS`): **Reconcile** (MISSING/UNTRACKED),
  **Cask versions & upgrades**, and **Formula versions & upgrades**. Both upgrade
  views share `compute_upgrades(kind, greedy)` (backed by `brew outdated
  --cask/--formula`); greedy mode is cask-only. Row-key prefixes distinguish them
  (`m:`/`u:` reconcile, `c:` casks, `f:` formulae).
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
