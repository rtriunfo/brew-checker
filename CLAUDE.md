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
  `installed_formulae`/`installed_taps` and `owned_apps`/`untracked_apps`) and
  the `--export`/`--snapshot`/`--diff`/`--diff-snapshots` CLI actions.
  `diff_snapshots(a, b)` is the pure snapshot-to-snapshot diff (neither side is
  the live machine — `{kind: (only_in_a, only_in_b)}`, apps only when both record
  them); `report_snapshot_diff(before, after)` prints it chronologically
  (ADDED = in after, REMOVED = in before) for the `--diff-snapshots BEFORE AFTER`
  CLI action. A snapshot (schema 2) records taps,
  formulae, casks, **and** `apps` — the untracked `.app` bundles on disk
  (`untracked_apps()` = `present_apps() − owned_apps()`), a read-only *log* only:
  brew can't install/remove them, so `diff_backup`/`report_diff` surface app
  differences but never as restore candidates. Recording `apps` makes
  `build_backup` incur the bulk `brew info` call the reconcile uses.
  `load_backup` stays backward-compatible with schema-1 (no `apps`) files.
  Snapshots live in `BACKUP_DIR` (`~/.brew-checker/backups/`); `list_backups()`
  (tolerant — skips foreign JSON) and `default_backup_path()` (timestamped name)
  back the TUI picker. `store_snapshot()` is the deduping save shared by the
  `--snapshot` CLI action and the TUI's `e`: if the inventory matches the latest
  **same-host** backup (`latest_backup(host)` + `snapshots_match`, comparing
  taps/formulae/casks/apps as sets) it's a **no-op on disk** — only an mtime bump
  (invisible to git) — otherwise it writes a new timestamped snapshot and leaves
  older ones as history (it never auto-deletes). Returns `(path, created)`;
  `--snapshot` exits `10` when a new backup was written, `0` when nothing changed.
  The no-op-on-unchanged behaviour is what lets `snapshot-sync.sh` (below) commit
  only on real changes and stay safe alongside TUI-created snapshots.

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

The UNTRACKED list (and its `apps` record in a snapshot) is deliberately never
filtered by an ignore-list, even though the same handful of App Store / Setapp
apps show up on every run. It's kept complete on purpose: a snapshot doubles as
a full reference of everything that was on the machine, which is exactly what
you'd want to reconstruct app inventory on a new Mac — brew can't install those
apps, but you still want to know they existed. Don't add ignore/allowlist
filtering to `untracked_apps()`/`present_apps()`.

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
  rather than a blank table. Schema-2 backups append their untracked-apps log at
  the end as non-selectable (key `None`) info rows tagged MISSING (recorded, gone)
  or INSTALLED (still present), excluded from the "to install" count; there's no
  EXTRA for apps (see `diff_backup`). The backup is chosen with `l` (`action_load` →
  `BackupPickerScreen`, an
  `OptionList` modal over `core.list_backups()`); the picker auto-opens on
  entering the view with nothing loaded, and a launch-arg path bypasses it. In
  the picker, `space` toggles multi-select and `d` deletes the selected
  snapshots (confirmed via `ConfirmScreen`). Because `push_screen_wait` requires
  a worker context, the delete action delegates to a `@work` method
  (`_delete_selected`) rather than awaiting inline — same reason `_pick_backup`
  is a worker. `U`
  reuses `action_upgrade`, dispatching to `_run_restore` (taps missing taps, then
  `brew install --formula/--cask` the selection); `e` (`action_export` →
  `_do_export`, a worker) saves a snapshot via `core.store_snapshot` (deduped
  against the latest same-host backup) off the UI thread and loads the result.
  `c` (`action_compare` → `_pick_compare`, a worker) enters a **compare sub-mode**:
  it picks a second snapshot (via the same `BackupPickerScreen`, loaded file
  excluded) and sets `_compare_path`, so `_refresh_backup` renders
  `core.diff_snapshots` via `_populate_compare` instead of the backup-vs-machine
  diff — showing **only differences** as chronological ADDED/REMOVED (ordered by
  `meta.date`, neutral "only loaded/picked" labels when dates are missing). All
  compare rows are info-only (key `None`); the `_comparing` property gates the
  footer (hides `U`). Compare mode is cleared on view switch, on loading a fresh
  backup (`l`), and on export (`e`). `h` (`action_history`) enters a similar
  **history sub-mode** (`_history_mode`, mutually exclusive with compare):
  `_refresh_backup` calls `compute_history()`, which loads every snapshot in
  `core.list_backups()` (sorted oldest-first by `meta.date`) and pairs each with
  `core.diff_snapshots(prev, cur)` against the one before it (`None` for the
  first/baseline), then `_populate_history` renders one info-only row per
  snapshot — date, host, item counts, and a compact delta (e.g. `+2c -1f`) versus
  the previous snapshot. `_comparing` (despite the name) also covers history
  mode for the purposes of hiding `U` in the footer, since neither sub-mode has
  anything installable. History mode is cleared the same places compare mode is
  (view switch, `l`, `e`) plus on entering compare mode, and vice versa.
  Timeline rows are keyed `hist:<idx>` (an index into the cached
  `_history_rows`, not an install target — `action_toggle_select` ignores this
  prefix) so Enter (`DataTable.RowSelected` → `on_data_table_row_selected`) can
  **drill into one step's actual item-level diff**: `_history_drill` records
  which index is drilled into, and `_populate_history_detail` re-renders that
  one step's already-computed delta as ADDED/REMOVED rows, same styling as
  `_populate_compare` (the earliest/baseline snapshot, whose `delta` is `None`,
  is instead framed as its whole inventory being "added"). Pressing `h` again
  while drilled in steps back to the cached timeline (`_populate_history`)
  without recomputing — `action_history` is a toggle, not a re-fetch, whenever
  a drill is active. Both the drill-in (`_populate_history_detail`) and a
  lighter always-on preview (`_render_history_preview`) share one helper,
  `_history_step_delta(idx)`, that returns `(delta, prev_tag, tag)` for a step
  (synthesizing the baseline case once, not twice). The preview fires from
  `on_data_table_row_highlighted` — `DataTable`'s built-in row-cursor event,
  posted on every arrow-key move — and writes the highlighted step's
  added/removed item names straight into the log panel. This is the one place
  in the app that calls `log_widget.clear()`: everywhere else `log_widget` is
  an append-only action log (scan/command output), but a preview that fires on
  every arrow press must replace its own content instead of spamming, so the
  clear is tightly guarded to `view == "backup" and _history_mode and
  _history_drill is None` (the timeline overview only — never during compare
  mode, a drilled-in detail, or any other view).
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

# Cron wrapper: snapshot, then commit+push the store to git only if it changed
./snapshot-sync.sh                # store = git repo at ~/.brew-checker

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
- `snapshot-sync.sh` is the cron wrapper. It never decides whether anything
  changed — it runs `brew-checker --snapshot` (which no-ops on disk when nothing
  changed) then lets git decide: `git add -A` + `git diff --cached --quiet` →
  commit + push (to `origin`, if set) only on a real change. This keeps it correct
  alongside TUI-created snapshots (an uncommitted TUI file is just committed next
  run) and depends on `store_snapshot`'s no-op-on-unchanged contract — don't
  reintroduce per-run file churn without revisiting this. It sets its own
  cron-safe `PATH` and uses a `mkdir` lock (no `flock` on macOS).
