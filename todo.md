# brew-checker — work tracker

Status of the architecture-fix work as of 2026-08-15. Items below are what is
**required** to close out the effort; each has an owner, acceptance check, and a
`[ ]`/`[x]` marker. The completed items are done and committed (see the commit
log); the remaining ones are gated behind a human decision or a real machine.

## Done (committed)

- [x] **Extract a single `reconcile()` engine function**
  - What: the MISSING/UNTRACKED/UNINSPECTABLE detection algorithm now has one
    home (`reconcile()` in `brew-checker.py`); both the CLI `main()` and the
    TUI's `compute_state()` call it (the third duplicate copy is gone).
  - Check: 15 engine unit tests + CLI JSON/report integration tests; TUI Pilot
    test confirms delegation (all green).
- [x] **Clean library boundary in the engine**
  - `load_backup()` raises a typed `BrewCheckerError` (not `SystemExit`);
    `write_backup()` never prints to stdout (add `backup_json()`); `cask_apps()`
    no longer logs to stderr; ANSI colours resolved lazily (no import side effects).
  - Check: unit tests + CLI `--diff` bad-backup error path (rc 2, no traceback);
    `--export -` prints JSON to stdout intentionally.
- [x] **Add a test suite (stdlib `unittest`)**
  - `tests/test_engine.py` (15), `tests/test_cli.py` (12, hermetic fake‑brew CLI),
    `tests/test_tui.py` (4, headless Textual Pilot). `tests/fakebrew.py` is the
    shared fake-brew harness. 31/31 green in the venv; plain `python3` skips the
    4 textual-dependent tests. `BREW_CHECKER_APP_DIRS` env override added as a
    test seam.
  - Check: `python3 -m unittest discover -s tests -t .` and
    `.venv/bin/python -m unittest discover -s tests -t .`.
- [x] **TUI cut-over + mode-state consolidation**
  - TUI uses `core.reconcile()`/`core.BrewCheckerError`; backup
    compare/history/drill resets routed through one `_reset_backup_mode()`.
  - Check: Pilot tests (reconcile, casks upgrade, backup export); binary rebuilt.
- [x] **Rebuild `dist/brew-checker-tui` after every engine/TUI change** checks
    its own `--version`.

## Open — need a human decision (`gated`, not forgotten)

- [ ] **Decompose the `BrewCheckerTUI` god-class (optional)** — the 1200-line App
  class currently holds every view, render path, action, and worker. Recommended
  structural step: split view-specific methods (reconcile / casks / formulae /
  backup) into focused mixins or per-view classes to shrink the monolith and
  reduce cross-cutting state. This was originally tagged optional and is a
  behavior‑risk refactor, so it needs explicit go-ahead rather than autonomous
  execution.
  - Gate: user confirms they want the structural split.
  - Guardrail available: the headless Pilot suite covers Reconcile, casks-upgrade,
    and backup export.

## Requires a real machine (cannot validate headlessly)

- [ ] **Live terminal validation** — run `./run-tui.sh` (and the rebuilt
  `dist/brew-checker-tui`) on a real TTY with Homebrew installed; verify the
  Reconcile report against real casks and that state-changing actions
  (reinstall / drop / upgrade / restore) work.
  - Why: headless tests exercise all compute/backend paths but not real Textual
    rendering or the suspend-to-terminal `sudo` prompt path.
- [ ] **Suspend-to-terminal (`sudo`) path** — some casks invoke `sudo` mid-install
  and need the real TTY; confirm the prompt is reachable and resumed properly.

## Key one-liners for local dev

```sh
python3 -m unittest discover -s tests -t .          # engine + CLI (via fake brew)
.venv/bin/python -m unittest discover -s tests -t . # + Textual Pilot tests
./build-tui.sh                                       # rebuild dist/brew-checker-tui
```

**Relevant commits**: `d353d5d`, `4cc4c65`, `b7b4601`, `05845b8`, `cbc2e5a`.