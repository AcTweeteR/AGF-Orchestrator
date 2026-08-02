"""Typed results for controlled task execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class ExecutionStatus(StrEnum):
    DRY_RUN = "DRY_RUN"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


@dataclass(frozen=True)
class ExecutionResult:
    execution_id: str
    plan_id: str
    task_id: str
    adapter: str
    started_at: str
    finished_at: str
    repository: str
    branch: str
    command_summary: str
    exit_code: int | None
    status: ExecutionStatus
    files_changed: list[str]
    validations_requested: list[str]
    stdout_summary: str
    stderr_summary: str
    evidence: list[str]
    blocking_issues: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
