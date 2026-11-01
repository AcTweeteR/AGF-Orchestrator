"""Install a separate AGF CLI for testing governed Free-First dispatch."""

from __future__ import annotations

import shlex
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    python = Path.home() / "Documents/AGF/.venv/bin/python"
    if not python.is_file():
        raise SystemExit("existing AGF environment is unavailable")
    launcher = Path.home() / ".local/bin/agf-governed"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(
        "#!/bin/sh\n"
        + "export PYTHONPATH="
        + shlex.quote(str(root / "src") + ":" + str(root))
        + "\n"
        + "exec "
        + shlex.quote(str(python))
        + ' -m agf_orchestrator.cli "$@"\n'
    )
    launcher.chmod(0o755)
    print("installed", launcher)


if __name__ == "__main__":
    main()
