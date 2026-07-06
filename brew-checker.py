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
import json
import os
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


def parse_args():
    p = argparse.ArgumentParser(
        description="Reconcile installed Homebrew casks against the Applications folders.",
        epilog="With no flag, both sections are shown. Pass a flag to show only that one.",
    )
    p.add_argument("-m", "--missing", action="store_true",
                   help="show only casks whose .app is missing from disk")
    p.add_argument("-u", "--untracked", action="store_true",
                   help="show only apps on disk with no owning cask")
    args = p.parse_args()
    # Default (no flag) = show everything.
    if not (args.missing or args.untracked):
        args.missing = args.untracked = True
    return args


def main(args):
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
