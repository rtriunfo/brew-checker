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

## Backup & restore

Snapshot everything you've explicitly installed so it can be recreated on another
Mac. The backup is a small JSON file listing your **taps**, **explicitly-installed
formulae** (`brew leaves --installed-on-request` — dependencies are omitted since
they come back automatically), and **casks**.

```sh
brew-checker --export > brew-backup.json   # write a backup (or: --export FILE)
brew-checker --snapshot                    # save into the store, deduped (see below)
brew-checker --diff brew-backup.json       # what the backup has that this Mac lacks (and vice versa)
```

`--diff` prints two lists per category: **MISSING** (in the backup, not installed
here — the restore candidates) and **EXTRA** (installed here, absent from the
backup). Exit code is `1` when anything is missing, `0` when in sync.

`--snapshot` is the automation-friendly save: it writes a snapshot into the store
(`~/.brew-checker/backups/`) but **deduplicates against the latest backup from the
same host** — if nothing has changed since then it's a no-op on disk (no new file,
nothing rewritten), so a daily cron job doesn't accumulate identical snapshots.
Exit code is `10` when a new backup was written (something changed) and `0` when
nothing changed; it prints `created <path>` or `unchanged <path>` to stdout. See
[Automating snapshots](#automating-snapshots-cron--git) below for the cron + git
wrapper.

Exporting and diffing are read-only — the CLI never installs anything. To
actually **install** the missing items, open the backup in the TUI's backup view
(below), which lets you select and install them (adding any needed taps first).

### The backup store

A snapshot records the machine's explicit **taps, formulae, and casks** (which
the backup view can reinstall) plus a read-only **log of untracked `.app`
bundles** — apps on disk that no cask owns (App Store apps, hand-installed
`.app`s, …). brew-checker can't install or remove those apps; the list is there
so a snapshot doubles as a record of what was on the machine, viewable from
another Mac. (Recording it runs the same `brew info` scan as the reconcile
report, so writing a snapshot takes a couple of seconds.)

The TUI keeps its snapshots in `~/.brew-checker/backups/` so they accumulate and
can be browsed. In the backup view, press `e` to save a snapshot there — this
uses the same dedup as `--snapshot`, so saving when nothing has changed since your
last same-host backup does nothing rather than adding a duplicate.
Press `l` to open a picker listing every saved backup (host, date,
formula/cask/tap/app counts) — press enter to load and diff one. In the picker,
`space` toggles a checkmark on one or more backups and `d` deletes the selected
ones from the store (after a confirmation). The picker opens automatically when
you enter the backup view with nothing loaded. (A file passed on launch,
`run-tui.sh <file>`, bypasses the store and loads that file directly.)

### Automating snapshots (cron + git)

`snapshot-sync.sh` turns the store into a version-controlled, off-machine backup:
it takes a snapshot and, **only when the inventory actually changed**, commits and
pushes `~/.brew-checker` to a (private) git remote. It's designed to run
unattended from cron.

It doesn't judge "did anything change?" itself — it lets git decide. `--snapshot`
is a no-op on disk when nothing changed, so the working tree stays clean and the
script commits only on a real change. This also makes it safe alongside the TUI:
a snapshot you save manually with `e` is just an uncommitted file that the next
run picks up and commits. Unchanged runs do nothing; a failed push (offline, auth)
is kept locally and retried next run. Activity is logged to
`~/.brew-checker/snapshot-sync.log`.

**One-time setup** — create the private remote once (the recurring script never
creates repos):

```sh
./snapshot-sync.sh                        # first run: creates the repo + initial commit
cd ~/.brew-checker
gh repo create brew-checker-backups --private --source=. --remote=origin --push
# …or manually:
#   git remote add origin git@github.com:<you>/brew-checker-backups.git
#   git push -u origin main
```

**Schedule it** — e.g. daily at 09:00 (`crontab -e`):

```cron
0 9 * * * /Users/<you>/dev/brew-checker/snapshot-sync.sh
```

> **Push auth must be non-interactive.** cron can't type a passphrase, so the
> `origin` remote needs an SSH key loaded in an agent, or a `gh`/credential-helper
> token — otherwise pushes fail (commits still accumulate locally). The script sets
> a cron-safe `PATH` itself, so `brew`/`python3`/`brew-checker` resolve.

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
./run-tui.sh                     # launches the TUI using the venv, from anywhere
./run-tui.sh brew-backup.json    # …with a backup loaded in the backup view
```

The TUI has four views, switched with `1`–`4` or cycled with `v`:

- **Reconcile** (default) — the MISSING / UNTRACKED lists described above.
- **Cask versions & upgrades** — every installed cask with its installed version,
  which ones are outdated (and the latest version), and upgrade actions. By
  default it uses `brew outdated --cask` (the same set `brew upgrade` would
  touch); press `g` for greedy mode, which also flags auto-updating casks. Note
  brew can't report a "latest" for `:latest` / auto-updating casks, so those show
  as up to date.
- **Formula versions & upgrades** — the same view for installed formulae, backed
  by `brew outdated --formula`. Select outdated formulae and press `U` to upgrade
  them. (Greedy mode is cask-only, so it doesn't apply here.)
- **Backup & restore** — browse saved backups (see [The backup
  store](#the-backup-store) above) and compare one against this machine. Press
  `l` to pick a backup (the picker auto-opens if none is loaded), or pass a file
  on launch. It lists the backup's **whole inventory**, one row per item, tagged
  **INSTALLED** (in both — so a backup matching this Mac shows a full green list),
  **MISSING** (in the backup, not installed here), or **EXTRA** (installed here,
  not in the backup). MISSING rows sort to the top of each section and are
  selectable — select them and press `U` to install (any taps the backup needs
  are added first). Untracked apps recorded in the snapshot are listed at the end
  tagged **INSTALLED** (still on disk) or **MISSING** (recorded but no longer on
  disk), but they're **info-only and never selectable** — brew can't install or
  remove `.app`s. (There's no EXTRA for apps: the machine's full app set includes
  cask-owned apps that were never in the untracked log, so a machine-side "extra"
  wouldn't be meaningful.) Press `e` to save a snapshot of this machine into the
  store (deduped against your latest same-host backup — an unchanged save is a
  no-op rather than adding a duplicate).

### Keys

| key     | view      | action                                                    |
|---------|-----------|-----------------------------------------------------------|
| `1`     | all       | go to Casks view                                         |
| `2`     | all       | go to Formulae view                                      |
| `3`     | all       | go to Reconcile view                                     |
| `4`     | all       | go to Backup view                                        |
| `v`     | all       | cycle Casks → Formulae → Reconcile → Backup              |
| `/`     | all       | filter rows by search text (Enter to close, Esc to clear) |
| `^p`    | all       | open command palette (type to jump to a view)             |
| `space` | all       | select / deselect the current row                         |
| `f5`    | all       | rescan the current view                                   |
| `q`     | all       | quit                                                      |
| `r`     | reconcile | reinstall selected MISSING casks (`brew reinstall --cask`) |
| `d`     | reconcile | drop selected MISSING casks (`brew uninstall --cask`)      |
| `i`     | reconcile | search + install a cask for selected UNTRACKED apps       |
| `m`     | reconcile | show / hide the MISSING group                             |
| `u`     | reconcile | show / hide the UNTRACKED group                           |
| `U`     | upgrades  | upgrade selected casks/formulae (`brew upgrade`)          |
| `g`     | casks     | toggle greedy (include auto-updating casks)              |
| `U`     | backup    | install selected MISSING items (`brew install`, tapping first) |
| `l`     | backup    | open the picker to load a saved backup                    |
| `e`     | backup    | save a snapshot into the store (deduped by same-host)   |

The footer only shows the keys relevant to the active view.

When you run an action, the TUI **suspends itself and hands the terminal to
brew**, then resumes when it's done (press Enter to return). This is deliberate:
some casks run `sudo` mid-install (e.g. removing a launchctl helper) and prompt
for your password — that prompt needs the real terminal, so brew's output for
these commands appears in the terminal rather than the side log pane.

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
