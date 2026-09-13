import sys
from pathlib import Path

import pytest

from agf_orchestrator import validation_commands


def test_python_command_uses_running_governed_interpreter(tmp_path: Path):
    assert validation_commands.validate_commands(
        ["python -m pytest -q"], str(tmp_path)
    ) == [f"{sys.executable} -m pytest -q"]


def test_python3_command_uses_running_governed_interpreter(tmp_path: Path):
    assert validation_commands.validate_commands(
        ["python3 -m pytest"], str(tmp_path)
    ) == [f"{sys.executable} -m pytest"]


@pytest.mark.parametrize("command", ["pytest -q", "ruff check ."])
def test_python_module_entry_point_must_use_governed_interpreter(
    monkeypatch, tmp_path: Path, command: str,
):
    monkeypatch.setattr(validation_commands.shutil, "which", lambda _: "/bin/tool")
    with pytest.raises(ValueError, match="cannot be resolved"):
        validation_commands.validate_commands([command], str(tmp_path))


def test_unknown_executable_remains_blocked(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(validation_commands.shutil, "which", lambda _name: None)

    with pytest.raises(ValueError, match="cannot be resolved: missing-tool"):
        validation_commands.validate_commands(["missing-tool --version"], str(tmp_path))
