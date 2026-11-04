"""Install a loopback-only, user-level OmniRoute service for the AGF pilot."""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
from pathlib import Path

LABEL = "com.actweeter.agf-omniroute"
PLIST = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
BIN = Path.home() / ".local/share/agf-omniroute/bin/omniroute"
STATE = Path.home() / ".local/state/agf-free-first"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=("install", "status", "uninstall"))
    args = p.parse_args()
    target = f"gui/{os.getuid()}/{LABEL}"
    if args.command == "status":
        result = subprocess.run(["launchctl", "print", target], capture_output=True, text=True)
        print(result.stdout[:1200] or result.stderr[:1200])
        return
    if args.command == "uninstall":
        subprocess.run(["launchctl", "bootout", target], capture_output=True)
        PLIST.unlink(missing_ok=True)
        print("uninstalled")
        return
    if not BIN.is_file():
        raise SystemExit("OmniRoute is not installed in the isolated AGF prefix")
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    STATE.mkdir(parents=True, exist_ok=True)
    data = {
        "Label": LABEL,
        "ProgramArguments": [
            str(BIN),
            "serve",
            "--no-open",
            "--no-tray",
        ],
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "OMNIROUTE_SERVER_HOST": "127.0.0.1",
            "PORT": "20128",
            "OMNIROUTE_MEMORY_MB": "2048",
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": str(STATE / "omniroute.stdout.log"),
        "StandardErrorPath": str(STATE / "omniroute.stderr.log"),
    }
    PLIST.write_bytes(plistlib.dumps(data))
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST)], capture_output=True, text=True
    )
    if result.returncode and "already" not in result.stderr.lower():
        raise SystemExit(result.stderr)
    print(f"installed {LABEL}, loopback-only: 127.0.0.1:20128")


if __name__ == "__main__":
    main()
