#!/usr/bin/env python3
"""Render the Homebrew formula template with a release's version and checksums.

Plain stdlib, no dependencies — used by the release workflow to produce
Formula/brew-checker.rb for the rtriunfo/homebrew-brew-checker tap. Uses
plain string replacement (not str.format) so the template's Ruby string
interpolation (#{bin}) is left untouched.
"""

import argparse
import pathlib

_TEMPLATE_PATH = pathlib.Path(__file__).resolve().parent.parent / "homebrew" / "brew-checker.rb.tmpl"


def render(version, cli_sha, tui_sha):
    text = _TEMPLATE_PATH.read_text()
    text = text.replace("{VERSION}", version)
    text = text.replace("{CLI_SHA}", cli_sha)
    text = text.replace("{TUI_SHA}", tui_sha)
    return text


def main():
    p = argparse.ArgumentParser(description="Render Formula/brew-checker.rb from the template.")
    p.add_argument("--version", required=True, help="release version, e.g. 1.2.3 (no leading v)")
    p.add_argument("--cli-sha", required=True, help="sha256 of the brew-checker.py release asset")
    p.add_argument("--tui-sha", required=True, help="sha256 of the brew-checker-tui release asset")
    p.add_argument("--out", required=True, help="path to write the rendered formula to")
    args = p.parse_args()

    rendered = render(args.version, args.cli_sha, args.tui_sha)
    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered)


if __name__ == "__main__":
    main()
