from pathlib import Path

import pytest

from agf_orchestrator import validation_commands


def test_python_command_uses_python3_when_python_is_unavailable(monkeypatch, tmp_path: Path):
    python3 = "/opt/test/bin/python3"

    def which(name: str):
        return python3 if name == "python3" else None

    monkeypatch.setattr(validation_commands.shutil, "which", which)

    assert validation_commands.validate_commands(
        ["python -m pytest -q"], str(tmp_path)
    ) == ["python3 -m pytest -q"]


def test_python_command_keeps_exact_interpreter_when_available(monkeypatch, tmp_path: Path):
    python = "/opt/test/bin/python"
    python3 = "/opt/test/bin/python3"

    def which(name: str):
        return {"python": python, "python3": python3}.get(name)

    monkeypatch.setattr(validation_commands.shutil, "which", which)

    assert validation_commands.validate_commands(
        ["python -m pytest"], str(tmp_path)
    ) == ["python -m pytest"]


def test_unknown_executable_remains_blocked(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(validation_commands.shutil, "which", lambda _name: None)

    with pytest.raises(ValueError, match="cannot be resolved: missing-tool"):
        validation_commands.validate_commands(["missing-tool --version"], str(tmp_path))
