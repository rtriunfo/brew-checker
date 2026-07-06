# brew-checker

Reconciles your installed Homebrew **casks** against the apps actually present in
your `/Applications` (and `~/Applications`) folders, and flags the mismatches in
both directions.

It exists to catch the situation where apps get lost/deleted but Homebrew still
thinks the cask is installed — plus the reverse, apps you installed by hand that
could be brought under brew's management.

**Read-only.** It only reports; it never installs, removes, or modifies anything.

## What it reports

- **MISSING** — a cask is installed (brew thinks so) but its `.app` is gone from
  disk. These are the discrepancies to clean up:
  - `brew reinstall --cask <name>` — restore the app, or
  - `brew uninstall --cask <name>` — drop the stale cask entry.
- **UNTRACKED** — a `.app` on disk that no installed cask owns. Candidates to
  migrate to a cask so brew can manage them:
  - `brew search --cask <name>` then `brew install --cask <name>`.
- **UNINSPECTABLE** — a cask brew refused to read (e.g. an untrusted third-party
  tap). Not checked; trust it with `brew trust <tap>` if you want it included.

Casks that install no `.app` (fonts, CLIs, drivers) are counted but skipped.

## Usage

```sh
brew-checker            # full report (both sections)
brew-checker -m         # --missing:   only casks whose .app is gone
brew-checker -u         # --untracked: only apps with no owning cask
brew-checker --help     # usage
```

With no flag, both sections are shown. Pass a single flag for a focused list
(header/footers dropped) that's easy to pipe:

```sh
brew-checker -m | grep vlc
brew-checker > report.txt        # colours auto-strip when not a terminal
```

Progress messages print to **stderr**, so they stay visible on screen without
polluting a redirected report. The exit code is `1` when anything is missing and
`0` when clean — handy for automation.

## Requirements

- **Homebrew** — the tool inspects your actual installed casks.
- **Python 3** — standard library only, no `pip install`, no virtualenv.

Both come with Apple's Command Line Tools, so any Mac that can run brew already
has everything this needs. There are no dependencies to manage.

## Install (run from anywhere)

The script is symlinked onto your `PATH` so `brew-checker` works from any
directory. It's a symlink, so edits to the source file take effect immediately —
no reinstall needed.

```sh
ln -sf "$PWD/brew-checker.py" ~/.local/bin/brew-checker
```

Re-run that one-liner if you ever move this project folder. (Ensure
`~/.local/bin` is on your `PATH`.)

Alternatively, just run it in place:

```sh
./brew-checker.py
```

## Interactive TUI (optional)

`brew-checker-tui.py` is a separate, interactive companion built on
[Textual](https://textual.textualize.io/). It shows the same MISSING / UNTRACKED
lists but lets you select rows and act on them — reinstalling casks, dropping
stale ones, or searching-and-installing a cask for an untracked app. Every brew
command is shown and confirmed before it runs, with live output in a side pane.

It reuses the read-only engine imported from `brew-checker.py` (which stays
untouched), and adds the only external dependency in this project: `textual`.

### Setup (one time)

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Run

```sh
./run-tui.sh            # launches the TUI using the venv, from anywhere
```

### Keys

| key     | action                                                        |
|---------|---------------------------------------------------------------|
| `space` | select / deselect the current row                             |
| `r`     | reinstall selected MISSING casks (`brew reinstall --cask`)     |
| `d`     | drop selected MISSING casks (`brew uninstall --cask`)          |
| `i`     | search + install a cask for selected UNTRACKED apps           |
| `m`     | show / hide the MISSING group                                 |
| `u`     | show / hide the UNTRACKED group                                |
| `f5`    | rescan                                                         |
| `q`     | quit                                                           |

The plain `brew-checker.py` remains fully standalone and dependency-free — the
TUI is purely additive.

### Single-file executable (no dependencies to manage)

> **Why not Docker?** This tool has to run natively on macOS — it drives your
> host's `brew` and modifies `/Applications`. A Docker container runs Linux and
> is sandboxed away from both, so it can't run this. Instead we bundle the one
> dependency (`textual`) into a single self-contained executable.

[shiv](https://shiv.readthedocs.io/) packages `textual` and both scripts into one
runnable file. The result needs only `python3` (already on any Mac with brew) —
no venv, no `pip install`.

```sh
# one-time: install the build tool
.venv/bin/pip install -r requirements-dev.txt

# build (re-run whenever you change the code)
./build-tui.sh                       # -> dist/brew-checker-tui

# run it from anywhere
dist/brew-checker-tui
ln -sf "$PWD/dist/brew-checker-tui" ~/.local/bin/brew-checker-tui   # optional: on PATH
```

The `dist/` artifact is git-ignored (it's a rebuildable 2–3 MB bundle); commit the
source and rebuild rather than checking the binary in.

## How it works

1. Lists installed casks (`brew list --cask`).
2. Reads each cask's `.app` artifact from `brew info --cask --json=v2` (one bulk
   call; if a cask from an untrusted tap makes the call fail, it drops just that
   cask and retries the rest — so it stays fast, ~a couple of brew calls total).
3. Checks each expected `.app` against the Applications folders → **MISSING**.
4. Lists every `.app` on disk that no cask owns → **UNTRACKED**.
