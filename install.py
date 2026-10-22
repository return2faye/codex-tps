#!/usr/bin/env python3
"""Install the Codex TPS plugin and its local shell command."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


MARKER = "# codex-tps installer managed wrapper"


def wrapper_text(launcher):
    return f"#!/bin/sh\n{MARKER}\nexec {shlex.quote(str(launcher))} \"$@\"\n"


def check_command(path):
    if path.is_symlink():
        raise RuntimeError(f"Refusing to replace existing symlink: {path}")
    if path.exists() and not path.read_bytes().startswith(f"#!/bin/sh\n{MARKER}\n".encode()):
        raise RuntimeError(f"Refusing to replace existing command: {path}")


def install(repo=None, home=None, which=shutil.which, run=subprocess.run):
    repo = Path(repo or Path(__file__).resolve().parent).resolve()
    home = Path(home or Path.home())
    destination = home / ".local" / "bin" / "codex-tps"

    codex, uv = which("codex"), which("uv")
    if not codex or not uv:
        missing = ", ".join(name for name, path in (("codex", codex), ("uv", uv)) if not path)
        raise RuntimeError(f"Required command not found on PATH: {missing}")
    check_command(destination)

    run([codex, "plugin", "marketplace", "add", str(repo)], check=True)
    result = run([codex, "plugin", "add", "codex-tps@codex-tps-dev", "--json"],
                 check=True, capture_output=True, text=True)
    try:
        installed_path = Path(json.loads(result.stdout)["installedPath"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError("Codex did not return an installedPath for the plugin") from exc
    launcher = installed_path / "bin" / "codex-tps"
    if not launcher.is_file():
        raise RuntimeError(f"Installed shell launcher is missing: {launcher}")

    check_command(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(wrapper_text(launcher))
    destination.chmod(0o755)
    return destination


def main():
    try:
        destination = install()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"codex-tps install failed: {exc}", file=sys.stderr)
        return 1
    print(f"Installed shell command: {destination}")
    if str(destination.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        print('Add it to PATH with: export PATH="$HOME/.local/bin:$PATH"')
    print("Restart Codex Desktop, then open the Codex TPS panel or invoke codex_tps_open.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
