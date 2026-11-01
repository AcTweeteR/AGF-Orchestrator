"""Install a user-level launchd periodic worker for AGF's free-first pilot."""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "com.actweeter.agf-free-first"
DEST = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
SOURCE = Path(__file__).with_name("agf_chat.py").resolve()
RUNTIME = Path.home() / ".local/share/agf-free-first/agf_chat.py"
STATE = Path.home() / ".local/state/agf-free-first"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("install", "status", "uninstall"))
    args = parser.parse_args()
    target = f"gui/{os.getuid()}/{LABEL}"
    if args.command == "status":
        print(
            subprocess.run(["launchctl", "print", target], capture_output=True, text=True).stdout[
                :1600
            ]
        )
        return
    if args.command == "uninstall":
        subprocess.run(["launchctl", "bootout", target], capture_output=True)
        DEST.unlink(missing_ok=True)
        print("uninstalled")
        return
    STATE.mkdir(parents=True, exist_ok=True)
    RUNTIME.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE, RUNTIME)
    shutil.copy2(SOURCE.with_name("agf_full.py"), RUNTIME.with_name("agf_full.py"))
    launcher = Path.home() / ".local/bin/agf-chat"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("#!/bin/sh\nexec " + sys.executable + " " + str(RUNTIME) + ' "$@"\n')
    launcher.chmod(0o755)
    DEST.parent.mkdir(parents=True, exist_ok=True)
    spec = {
        "Label": LABEL,
        "ProgramArguments": [
            sys.executable,
            str(RUNTIME),
            "--state-dir",
            str(STATE),
            "worker",
        ],
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
        "RunAtLoad": True,
        "StartInterval": 60,
        "ProcessType": "Background",
        "StandardOutPath": str(STATE / "worker.stdout.log"),
        "StandardErrorPath": str(STATE / "worker.stderr.log"),
    }
    DEST.write_bytes(plistlib.dumps(spec))
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(DEST)], capture_output=True, text=True
    )
    if result.returncode:
        if "already" not in result.stderr.lower():
            raise SystemExit(result.stderr)
    print(f"installed {LABEL}, every 60 seconds; plist={DEST}")


if __name__ == "__main__":
    main()
