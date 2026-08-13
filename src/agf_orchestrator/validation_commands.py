"""Shared validation-command safety and target-aware executable resolution."""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

SHELL_CONTROL_TOKENS = {";", "&&", "||", "&", "|", ">", "<"}


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
        executable = Path(argv[0])
        if "/" in argv[0]:
            candidate = (
                (root / executable).resolve()
                if not executable.is_absolute()
                else executable.resolve()
            )
            available = (
                root in candidate.parents
                and candidate.is_file()
                and os.access(candidate, os.X_OK)
            )
        else:
            available = shutil.which(argv[0]) is not None
        if not available:
            raise ValueError(f"validation executable cannot be resolved: {argv[0]}")
        parsed.append(command)
    return parsed
