"""Shared validation-command safety and target-aware executable resolution."""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

SHELL_CONTROL_TOKENS = {";", "&&", "||", "&", "|", ">", "<"}
EXECUTABLE_ALIASES = {"python": ("python3",)}


def _resolve_executable(argv: list[str], repository_root: Path) -> str | None:
    executable = Path(argv[0])
    if "/" in argv[0]:
        candidate = (
            (repository_root / executable).resolve()
            if not executable.is_absolute()
            else executable.resolve()
        )
        if (
            repository_root in candidate.parents
            and candidate.is_file()
            and os.access(candidate, os.X_OK)
        ):
            return str(candidate)
        return None

    resolved = shutil.which(argv[0])
    if resolved is not None:
        return resolved
    for alias in EXECUTABLE_ALIASES.get(argv[0], ()):
        resolved = shutil.which(alias)
        if resolved is not None:
            return resolved
    return None


def validate_commands(commands: list[str], repository_root: str) -> list[str]:
    if not commands:
        raise ValueError("validation commands must not be empty")
    parsed: list[str] = []
    root = Path(repository_root).resolve()
    for command in commands:
        if not command.strip() or any(token in command for token in ("`", "$(", "\n", "\r")):
            raise ValueError(f"validation command contains shell control syntax: {command}")
        try:
            lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
            lexer.whitespace_split = True
            tokens = list(lexer)
            argv = shlex.split(command, posix=True)
        except ValueError as exc:
            raise ValueError(f"invalid validation command: {exc}") from exc
        if not argv or any(token in SHELL_CONTROL_TOKENS for token in tokens):
            raise ValueError(f"validation command contains shell control syntax: {command}")
        resolved = _resolve_executable(argv, root)
        if resolved is None:
            raise ValueError(f"validation executable cannot be resolved: {argv[0]}")
        if "/" in argv[0] or resolved == shutil.which(argv[0]):
            parsed.append(command)
        else:
            alias = next(
                alias
                for alias in EXECUTABLE_ALIASES.get(argv[0], ())
                if shutil.which(alias) == resolved
            )
            parsed.append(shlex.join([alias, *argv[1:]]))
    return parsed
