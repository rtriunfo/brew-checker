#!/usr/bin/env python3
"""
brew-checker — reconcile installed Homebrew casks against the Applications folders.

Two things it surfaces:
  1. MISSING  — a cask is installed (brew thinks so) but its .app is gone from disk.
                These are the discrepancies you want to clean up (e.g. `brew uninstall --cask <name>`
                or `brew reinstall --cask <name>` to restore the app).
  2. UNTRACKED — a .app in your Applications folder that no installed cask owns.
                These are candidates to migrate to a cask so brew can manage them.

Read-only. Nothing is modified; it only reports.
"""

import argparse
import datetime
import json
import os
import socket
import subprocess
import sys

APP_DIRS = ["/Applications", os.path.expanduser("~/Applications")]

# ANSI colours (skipped when stdout isn't a TTY)
if sys.stdout.isatty():
    RED, GREEN, YELLOW, DIM, BOLD, RESET = (
        "\033[31m", "\033[32m", "\033[33m", "\033[2m", "\033[1m", "\033[0m",
    )
else:
    RED = GREEN = YELLOW = DIM = BOLD = RESET = ""


def log(msg, end="\n"):
    """Progress feedback on stderr so it stays out of redirected report output."""
    print(msg, end=end, file=sys.stderr, flush=True)


def run(cmd, check=True):
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def installed_casks():
    out = run(["brew", "list", "--cask"]).stdout.split()
    return sorted(out)


def installed_formulae():
    """Explicitly-installed formulae only (leaves), excluding pulled-in deps."""
    out = run(["brew", "leaves", "--installed-on-request"]).stdout.split()
    return sorted(out)


def installed_taps():
    out = run(["brew", "tap"]).stdout.split()
    return sorted(out)


# --- backup / restore ------------------------------------------------------
# A backup is a portable snapshot of what you explicitly installed, so it can be
# recreated on another machine. It's plain data (JSON) — writing one doesn't
# touch brew; only the TUI's restore actually installs anything.
SCHEMA = 2

# Where the TUI keeps its snapshots so they accumulate and can be browsed.
BACKUP_DIR = os.path.expanduser("~/.brew-checker/backups")


def default_backup_path():
    """A timestamped path in the backup store (time in the name avoids clashes)."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    return os.path.join(BACKUP_DIR, f"brew-backup-{socket.gethostname()}-{stamp}.json")


def list_backups():
    """Every readable backup in BACKUP_DIR, newest first.

    Returns [(path, meta, n_formulae, n_casks, n_taps, n_apps), …].
    Unreadable/foreign JSON files are skipped (this is tolerant, unlike
    load_backup which hard-exits).
    """
    if not os.path.isdir(BACKUP_DIR):
        return []
    entries = []
    for name in os.listdir(BACKUP_DIR):
        if not name.endswith(".json"):
            continue
        path = os.path.join(BACKUP_DIR, name)
        try:
            with open(path) as f:
                obj = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(obj, dict) or "formulae" not in obj or "casks" not in obj:
            continue
        entries.append((path, obj.get("meta", {}),
                        len(obj["formulae"]), len(obj["casks"]),
                        len(obj.get("taps", [])), len(obj.get("apps", []))))
    entries.sort(key=lambda e: os.path.getmtime(e[0]), reverse=True)
    return entries


def snapshots_match(a, b):
    """True if two backups have identical inventories (ignoring meta host/date).

    Compares taps/formulae/casks/apps as sets — order and duplicates don't matter.
    """
    return all(set(a.get(k, [])) == set(b.get(k, []))
               for k in ("taps", "formulae", "casks", "apps"))


def latest_backup(host=None):
    """The newest store backup as (path, obj), optionally restricted to one host.

    Returns None when the store has no matching backup. Skips files that turn
    unreadable between listing and loading (tolerant, like list_backups).
    """
    for path, meta, *_ in list_backups():  # newest first
        if host is not None and meta.get("host") != host:
            continue
        try:
            with open(path) as f:
                return path, json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
    return None


def store_snapshot(backup=None):
    """Save a snapshot into the store, deduped against the latest same-host backup.

    Writes `backup` (or a freshly built one) to a new timestamped path. If its
    inventory matches the latest existing backup from the same host, the old file
    is removed so the fresh one simply replaces it (same content, refreshed date
    and filename) — this keeps automated/repeated runs from piling up identical
    snapshots. Returns (path, created): created is False when an unchanged backup
    replaced its predecessor, True when a genuinely new snapshot was kept.
    """
    if backup is None:
        backup = build_backup()
    prev = latest_backup(host=backup.get("meta", {}).get("host"))
    path = default_backup_path()
    write_backup(backup, path)
    if prev is not None and snapshots_match(backup, prev[1]):
        prev_path = prev[0]
        if os.path.abspath(prev_path) != os.path.abspath(path):
            try:
                os.remove(prev_path)
            except OSError:
                pass  # leaving a stale duplicate is harmless; the new one stands
        return path, False
    return path, True


def build_backup():
    """Snapshot the current machine's explicit formulae, casks, taps, and the
    untracked .app bundles on disk.

    The `apps` list is a read-only *log* of applications no cask owns (App Store
    apps, hand-installed .apps, …). brew-checker can't install or remove them; it
    records them so a snapshot doubles as a record of what was on the machine.
    Computing it needs the same bulk `brew info` call the reconcile report uses,
    so this is slower than a taps/formulae/casks-only snapshot.
    """
    return {
        "schema": SCHEMA,
        "meta": {
            "host": socket.gethostname(),
            "date": datetime.datetime.now().isoformat(),
        },
        "taps": installed_taps(),
        "formulae": installed_formulae(),
        "casks": installed_casks(),
        "apps": untracked_apps(),
    }


def write_backup(obj, path=None):
    """Write a backup as JSON to `path`, or to stdout when path is None/'-'."""
    text = json.dumps(obj, indent=2)
    if path in (None, "-"):
        print(text)
    else:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w") as f:
            f.write(text + "\n")


def load_backup(path):
    """Read and validate a backup file. Exits with a clear message on bad input."""
    try:
        with open(path) as f:
            obj = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"cannot read backup {path}: {e}")
    if not isinstance(obj, dict) or "formulae" not in obj or "casks" not in obj:
        raise SystemExit(f"{path} doesn't look like a brew-checker backup "
                         "(missing 'formulae'/'casks').")
    return obj


def diff_backup(backup):
    """Compare a backup against what's installed now.

    Returns {kind: (missing, extra)} for kind in taps/formulae/casks/apps, where
    `missing` = in the backup but not present here and `extra` = present here but
    absent from the backup. For taps/formulae/casks `missing` items are restore
    candidates; for `apps` the diff is informational only — those .apps aren't
    brew-managed, so `missing` just means "recorded but no longer on disk". The
    apps `extra` isn't meaningful (it's the machine's *full* app set minus the
    backup's untracked subset, so it includes cask-owned apps) — callers ignore it.
    """
    current = {
        "taps": set(installed_taps()),
        "formulae": set(installed_formulae()),
        "casks": set(installed_casks()),
        "apps": present_apps(),
    }
    result = {}
    for kind, have in current.items():
        want = set(backup.get(kind, []))
        result[kind] = (sorted(want - have), sorted(have - want))
    return result


def _parse_apps(cask):
    apps = []
    for art in cask.get("artifacts", []):
        if isinstance(art, dict) and "app" in art:
            apps += [a for a in art["app"] if isinstance(a, str)]
    return apps


def cask_apps(tokens):
    """Return ({token: [app basenames]}, [uninspectable tokens]).

    Tries one bulk `brew info` call; if that fails (e.g. an untrusted third-party
    tap refuses to load), falls back to querying each cask individually so one bad
    cask can't blank the whole report. Tokens that still can't be read are returned
    separately so they show up as 'unknown' rather than silently vanishing.
    """
    if not tokens:
        return {}, []

    # One bulk `brew info` is far faster than per-cask calls (brew is slow to start).
    # A single unreadable cask (e.g. an untrusted tap) makes the whole call fail, so
    # we drop the offending token named in the error and retry — a couple of calls
    # total instead of one per cask.
    remaining, unknown = list(tokens), []
    while remaining:
        log(f"  querying {len(remaining)} casks via brew… ", end="")
        bulk = run(["brew", "info", "--cask", "--json=v2", *remaining], check=False)
        if bulk.returncode == 0:
            log("ok")
            data = json.loads(bulk.stdout)
            return {c["token"]: _parse_apps(c) for c in data["casks"]}, unknown

        # Figure out which cask brew refused, drop it, and retry the rest in bulk.
        bad = [t for t in remaining if t in bulk.stderr]
        if not bad:
            log("failed (unrecognised error)")
            return {}, remaining  # can't isolate the culprit — give up on the batch
        log(f"skipping {', '.join(bad)}")
        unknown += bad
        remaining = [t for t in remaining if t not in bad]

    return {}, unknown


def present_apps():
    """Set of .app basenames present across the Applications folders."""
    found = set()
    for d in APP_DIRS:
        if os.path.isdir(d):
            found.update(e for e in os.listdir(d) if e.endswith(".app"))
    return found


def app_exists(app):
    return any(os.path.exists(os.path.join(d, app)) for d in APP_DIRS)


def owned_apps(tokens=None):
    """Set of every .app basename an installed cask is responsible for.

    Uses the same bulk `brew info` path as the reconcile report; tokens brew
    can't inspect are skipped (their apps just won't count as owned).
    """
    if tokens is None:
        tokens = installed_casks()
    mapping, unknown = cask_apps(tokens)
    owned = set()
    for token in tokens:
        if token not in unknown:
            owned.update(mapping.get(token, []))
    return owned


def untracked_apps():
    """Sorted .app basenames on disk that no installed cask owns (a log only)."""
    return sorted(present_apps() - owned_apps())


def parse_args():
    p = argparse.ArgumentParser(
        description="Reconcile installed Homebrew casks against the Applications folders.",
        epilog="With no flag, both sections are shown. Pass a flag to show only that one.",
    )
    p.add_argument("-m", "--missing", action="store_true",
                   help="show only casks whose .app is missing from disk")
    p.add_argument("-u", "--untracked", action="store_true",
                   help="show only apps on disk with no owning cask")
    p.add_argument("--export", nargs="?", const="-", metavar="FILE",
                   help="write a backup of installed formulae/casks/taps as JSON "
                        "(to FILE, or stdout if omitted)")
    p.add_argument("--snapshot", action="store_true",
                   help="save a snapshot into the store (~/.brew-checker/backups), "
                        "deduped against the latest same-host backup: refreshes "
                        "that file's date if nothing changed, else writes a new one "
                        "(exit 0 = unchanged, 10 = new backup written)")
    p.add_argument("--diff", metavar="FILE",
                   help="show what a backup FILE has that this machine doesn't (and vice versa)")
    args = p.parse_args()
    # Default (no flag) = show everything.
    if not (args.missing or args.untracked):
        args.missing = args.untracked = True
    return args


def report_diff(backup):
    """Print the backup ⇄ machine diff. Returns True if anything is missing."""
    meta = backup.get("meta", {})
    tag = f"{meta.get('host', '?')} · {meta.get('date', '?')}"
    print(f"{BOLD}Backup ⇄ this machine{RESET}  {DIM}(backup: {tag}){RESET}\n")
    diff = diff_backup(backup)
    any_missing = False
    labels = {"taps": "taps", "formulae": "formulae", "casks": "casks"}
    for kind in ("taps", "formulae", "casks"):
        missing, extra = diff[kind]
        any_missing = any_missing or bool(missing)
        print(f"{BOLD}{RED}MISSING {labels[kind]} — in backup, not installed here "
              f"({len(missing)}){RESET}")
        for name in missing:
            print(f"  {RED}✗{RESET} {name}")
        print(f"{BOLD}{YELLOW}EXTRA {labels[kind]} — installed here, not in backup "
              f"({len(extra)}){RESET}")
        for name in extra:
            print(f"  {YELLOW}?{RESET} {name}")
        print()
    # Untracked apps are a log, not restore candidates — only "in backup, gone
    # now" is meaningful (the machine's full app set includes cask-owned apps that
    # were never in this untracked list, so a machine-side "extra" isn't useful).
    if "apps" in backup:
        app_missing, _ = diff["apps"]
        print(f"{BOLD}{RED}APPS in backup, not on disk now — untracked, not "
              f"restorable ({len(app_missing)}){RESET}")
        for name in app_missing:
            print(f"  {RED}✗{RESET} {name}")
        print()
    if any_missing:
        print(f"{DIM}Install the missing items from the TUI's backup view: "
              f"run-tui.sh <backup.json>{RESET}")
    return any_missing


def main(args):
    # Backup / diff modes short-circuit the reconcile report.
    if args.export is not None:
        log("Building backup…")
        write_backup(build_backup(), args.export)
        if args.export not in (None, "-"):
            log(f"  wrote {args.export}")
        sys.exit(0)
    if args.snapshot:
        log("Building snapshot…")
        path, created = store_snapshot()
        # One machine-readable line to stdout for scripts; detail/exit code convey
        # whether anything changed (10) or the existing file was just refreshed (0).
        if created:
            log(f"  changes detected — wrote {path}")
            print(f"created {path}")
            sys.exit(10)
        log(f"  no changes — refreshed {path}")
        print(f"unchanged {path}")
        sys.exit(0)
    if args.diff is not None:
        log("Diffing backup against this machine…")
        sys.exit(1 if report_diff(load_backup(args.diff)) else 0)

    # "Focused" mode = user asked for exactly one section; drop the header/footers
    # so the output is just that list.
    focused = args.missing ^ args.untracked

    log("Listing installed casks…")
    tokens = installed_casks()
    log(f"  {len(tokens)} casks installed")
    mapping, unknown = cask_apps(tokens)
    log("Scanning Applications folders…")

    missing = []          # (token, [missing apps])
    owned_apps = set()    # every .app any installed cask is responsible for
    no_app_casks = []     # casks that install no .app (fonts, CLIs, drivers…)

    for token in tokens:
        if token in unknown:
            continue
        apps = mapping.get(token, [])
        if not apps:
            no_app_casks.append(token)
            continue
        owned_apps.update(apps)
        gone = [a for a in apps if not app_exists(a)]
        if gone:
            missing.append((token, gone))

    untracked = sorted(present_apps() - owned_apps)

    # ---- report ----
    if not focused:
        print(f"{BOLD}Homebrew cask ⇄ Applications reconciliation{RESET}")
        print(f"{DIM}{len(tokens)} casks installed · checking {', '.join(APP_DIRS)}{RESET}\n")

    if args.missing:
        print(f"{BOLD}{RED}MISSING — cask installed but app not on disk ({len(missing)}){RESET}")
        if missing:
            for token, gone in sorted(missing):
                print(f"  {RED}✗{RESET} {token:<28} {DIM}→ {', '.join(gone)}{RESET}")
            print(f"  {DIM}Fix: brew reinstall --cask <name>   (restore)"
                  f"   |   brew uninstall --cask <name>   (drop){RESET}")
        else:
            print(f"  {GREEN}none — every app-installing cask has its app{RESET}")
        print()

    if args.untracked:
        print(f"{BOLD}{YELLOW}UNTRACKED — app on disk with no owning cask ({len(untracked)}){RESET}")
        if untracked:
            for app in untracked:
                print(f"  {YELLOW}?{RESET} {app}")
            print(f"  {DIM}Consider: brew search --cask <name>  then  brew install --cask <name>{RESET}")
        else:
            print(f"  {GREEN}none{RESET}")
        print()

    if not focused:
        print(f"{DIM}({len(no_app_casks)} casks install no .app — fonts/CLIs/drivers — not checked){RESET}")

        if unknown:
            print(f"\n{BOLD}{YELLOW}UNINSPECTABLE — couldn't read cask (untrusted tap?) ({len(unknown)}){RESET}")
            for token in unknown:
                print(f"  {YELLOW}!{RESET} {token}")
            print(f"  {DIM}These aren't checked. Trust with: brew trust <tap>{RESET}")

    # exit non-zero if there are discrepancies, so it's usable in automation
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    try:
        main(parse_args())
    except subprocess.CalledProcessError as e:
        print(f"error running: {' '.join(e.cmd)}\n{e.stderr}", file=sys.stderr)
        sys.exit(2)
