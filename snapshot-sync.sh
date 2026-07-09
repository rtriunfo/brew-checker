#!/usr/bin/env bash
# Take a brew-checker snapshot and, if the inventory actually changed, commit &
# push the backup store to git. Designed to be run unattended from cron.
#
# The store (~/.brew-checker) is a git repo; this script is the automation layer.
# It never decides "did anything change?" itself — it lets git decide:
#   1. `brew-checker --snapshot` updates the store (a no-op on disk when nothing
#      changed, so the working tree stays clean; a new file when it did change —
#      including snapshots you saved manually from the TUI).
#   2. `git add -A` + `git diff --cached --quiet` → commit & push only when the
#      staged tree differs from HEAD. Unchanged runs produce no commit.
# This makes it inherently safe alongside the TUI: a snapshot you saved there is
# just an uncommitted file that this script picks up and commits on its next run.
#
# One-time setup (create the private remote once), then a cron entry — see the
# "Automating snapshots" section of README.md.
set -u

# cron runs with a bare PATH, so Homebrew's brew/python3 and the brew-checker
# symlink in ~/.local/bin wouldn't be found. Put the likely locations up front.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

STORE="$HOME/.brew-checker"
LOG="$STORE/snapshot-sync.log"
LOCK="$STORE/.snapshot-sync.lock"   # a directory: mkdir is atomic (flock isn't on macOS)

mkdir -p "$STORE/backups"

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"; }

# Prevent overlapping runs. The trap clears the lock on any normal exit.
if ! mkdir "$LOCK" 2>/dev/null; then
  log "another run is in progress ($LOCK exists) — skipping"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# Resolve the CLI: prefer the installed `brew-checker`, fall back to the copy
# sitting next to this script (so it works straight from a checkout too).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BC="$(command -v brew-checker || true)"
[[ -z "$BC" ]] && BC="$SCRIPT_DIR/brew-checker.py"
if [[ ! -x "$BC" ]]; then
  log "cannot find an executable brew-checker (looked for it on PATH and at $BC)"
  exit 1
fi

# First run: turn the store into a git repo on branch main and ignore our own
# noise (the log and lock). Existing repos are left untouched.
if [[ ! -d "$STORE/.git" ]]; then
  git init -q "$STORE"
  git -C "$STORE" symbolic-ref HEAD refs/heads/main
  printf '%s\n' "snapshot-sync.log" ".snapshot-sync.lock/" >"$STORE/.gitignore"
  log "initialised git repo in $STORE (branch main)"
fi

# 1. Update the store. Exit 10 = new snapshot written, 0 = unchanged; either is
#    success here (git, not this code, decides whether to commit). Anything else
#    is a real failure — leave git alone.
out="$("$BC" --snapshot 2>>"$LOG")"; rc=$?
if [[ $rc -ne 0 && $rc -ne 10 ]]; then
  log "brew-checker --snapshot failed (exit $rc) — leaving git untouched"
  exit "$rc"
fi
log "snapshot: ${out:-<no output>} (exit $rc)"

# 2. Let git decide whether anything is worth committing.
git -C "$STORE" add -A
if git -C "$STORE" diff --cached --quiet; then
  log "no inventory change — nothing to commit"
  exit 0
fi

git -C "$STORE" commit -q -m "snapshot: $(hostname -s) $(date '+%Y-%m-%d %H:%M')"
log "committed changes"

# 3. Push if a remote is configured. A failed push (offline, auth) is not fatal —
#    the commit is safe locally and the next run will push it.
if git -C "$STORE" remote get-url origin >/dev/null 2>&1; then
  if git -C "$STORE" push -q origin HEAD 2>>"$LOG"; then
    log "pushed to origin"
  else
    log "push to origin failed — commit kept locally, will retry next run"
  fi
else
  log "no 'origin' remote — commit kept locally (see README one-time setup)"
fi
