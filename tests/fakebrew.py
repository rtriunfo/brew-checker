"""Shared hermetic fake-`brew` harness for tests.

Builds a fake `brew` executable and a controlled environment (fake
`BREW_CHECKER_APP_DIRS`, fake HOME) so tests can run the real `brew-checker.py`
CLI and the Textual TUI against a deterministic "machine" without touching a
real Homebrew install.
"""

import json
import os

# The machine the fake brew describes.
CASKS = ["basecask", "misscask", "noapp", "untrustedcask"]
FORMULAE = ["formula1", "formula2"]
TAPS = ["rtriunfo/brew-checker", "homebrew/cask"]
# `.app` bundles present on disk in the fake app dir.
DISK_APPS = ["BaseCask.app", "UntrackedApp.app"]

# The `artifacts` each inspectable cask owns (untrustedcask is never inspected).
CASK_ARTIFACTS = {
    "basecask": [{"app": ["BaseCask.app"]}],
    "misscask": [{"app": ["MissingGame.app"]}],
    "noapp": [],
}

_FAKE_BREW_SHIM = """import sys

args = sys.argv[1:]
first = args[0] if args else ""

if first == "list":
    verbose = "--versions" in args
    if "--cask" in args:
        names = __CASKS__
    elif "--formula" in args:
        names = __FORMULAE__
    else:
        sys.exit(99)
    if verbose:
        sys.stdout.write("\\n".join(n + " 1.0" for n in names) + "\\n")
    else:
        sys.stdout.write("\\n".join(names) + "\\n")
    sys.exit(0)

elif first == "leaves" and "--installed-on-request" in args:
    sys.stdout.write("\\n".join(__FORMULAE__) + "\\n")
    sys.exit(0)

elif first == "tap" and len(args) == 1:
    sys.stdout.write("\\n".join(__TAPS__) + "\\n")
    sys.exit(0)

elif first == "outdated" and "--cask" in args:
    import json as _json
    print(_json.dumps({"casks": []}))
    sys.exit(0)

elif first == "outdated" and "--formula" in args:
    import json as _json
    print(_json.dumps({"formulae": []}))
    sys.exit(0)

elif first == "info" and "--json=v2" in args:
    tokens = [a for a in args[1:] if not a.startswith("-")]
    # Simulate an untrusted tap: refuse the whole batch, naming the offending
    # token EXACTLY in stderr so the engine's substring isolation finds it,
    # drops it, and successfully retries the rest.
    bad = [t for t in tokens if "untrusted" in t]
    if bad:
        sys.stderr.write("Error: Cask '" + bad[0] + "' was not found in tap\\n")
        sys.exit(9)
    import json as _json
    casks = [{"token": t, "artifacts": __CASK_ARTIFACTS__.get(t, [])} for t in tokens]
    print(_json.dumps({"casks": casks}))
    sys.exit(0)

else:
    sys.stderr.write("unexpected brew call: " + repr(args) + "\\n")
    sys.exit(99)
"""


def _build_fake_brew_source():
    src = _FAKE_BREW_SHIM
    src = src.replace("__CASKS__", repr(CASKS))
    src = src.replace("__FORMULAE__", repr(FORMULAE))
    src = src.replace("__TAPS__", repr(TAPS))
    src = src.replace("__CASK_ARTIFACTS__", json.dumps(CASK_ARTIFACTS))
    return src


def write_fake_brew(bin_dir):
    """Write the fake `brew` executable into bin_dir and return its path."""
    os.makedirs(bin_dir, exist_ok=True)
    path = os.path.join(bin_dir, "brew")
    with open(path, "w") as f:
        f.write("#!/usr/bin/env python3\n" + _build_fake_brew_source())
    os.chmod(path, 0o755)
    return path


def make_env(tmp, apps_dir):
    """A process env pointing at the fake machine (brew on PATH, fake app dir)."""
    bin_dir = os.path.join(tmp, "bin")
    write_fake_brew(bin_dir)
    env = dict(os.environ)
    env["BREW_CHECKER_APP_DIRS"] = apps_dir
    env["HOME"] = tmp
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    return env


def make_apps_dir(tmp):
    """Create a fake Applications folder with the expected .app bundles."""
    apps_dir = os.path.join(tmp, "Applications")
    os.makedirs(apps_dir)
    for appname in DISK_APPS:
        os.makedirs(os.path.join(apps_dir, appname))
    return apps_dir