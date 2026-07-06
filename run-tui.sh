#!/usr/bin/env bash
# Launch the interactive TUI using its virtualenv, from anywhere.
set -euo pipefail
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -x "$dir/.venv/bin/python" ]]; then
  echo "No venv found. Run:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
exec "$dir/.venv/bin/python" "$dir/brew-checker-tui.py" "$@"
