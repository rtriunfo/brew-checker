#!/usr/bin/env bash
# Build a single self-contained executable of the TUI (textual baked in).
# Output: dist/brew-checker-tui  — copy it anywhere and run it; only needs python3.
set -euo pipefail
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$dir"

if [[ ! -x .venv/bin/shiv ]]; then
  echo "shiv not found. Run:  .venv/bin/pip install -r requirements-dev.txt" >&2
  exit 1
fi

# Stage the two source files under import-safe names. The engine keeps its
# hyphenated filename (it's loaded by path, not imported), while the TUI is
# copied to an importable module name for shiv's entry point.
staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
cp brew-checker.py "$staging/brew-checker.py"
cp brew-checker-tui.py "$staging/brew_checker_tui.py"

mkdir -p dist
.venv/bin/shiv \
  --output-file dist/brew-checker-tui \
  --site-packages "$staging" \
  --entry-point brew_checker_tui:main \
  --python '/usr/bin/env python3' \
  --compressed \
  textual

echo "Built dist/brew-checker-tui"
