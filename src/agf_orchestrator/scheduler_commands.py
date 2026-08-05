"""Bounded scheduler command and audit surface over the persistent state store."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .scheduler_models import SchedulerState, SchedulerStatus
from .scheduler_store import SchedulerStore, SchedulerStoreError


class SchedulerCommandError(RuntimeError):
    """Raised when a scheduler command cannot be applied safely."""


class SchedulerCommand(StrEnum):
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    STATUS = "status"
    AUDIT = "audit"


@dataclass(frozen=True)
class SchedulerCommandResult:
    command: SchedulerCommand
    state: SchedulerState
    changed: bool
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command.value,
            "state": self.state.to_dict(),
            "changed": self.changed,
            "evidence": list(self.evidence),
        }


class SchedulerCommandService:
    """Apply only the bounded lifecycle commands defined by the scheduler policy."""

    _TARGETS = {
        SchedulerCommand.START: SchedulerStatus.RUNNING,
        SchedulerCommand.PAUSE: SchedulerStatus.PAUSED,
        SchedulerCommand.RESUME: SchedulerStatus.RUNNING,
        SchedulerCommand.CANCEL: SchedulerStatus.CANCELLED,
    }

    def __init__(self, store: SchedulerStore):
        self.store = store

    def execute(self, command: SchedulerCommand) -> SchedulerCommandResult:
        try:
            command = SchedulerCommand(command)
        except ValueError as exc:
            raise SchedulerCommandError("scheduler command is invalid") from exc
        try:
            current = self.store.load()
            if command in {SchedulerCommand.STATUS, SchedulerCommand.AUDIT}:
                return self._read_result(command, current)
            target = self._TARGETS[command]
            if current.status is target:
                return SchedulerCommandResult(
                    command, current, False, self._evidence(command, current)
                )
            if not self._allowed_command(command, current.status):
                raise SchedulerCommandError(
                    f"command {command.value} is not allowed from {current.status.value}"
                )
            updated = self.store.transition(target)
            return SchedulerCommandResult(command, updated, True, self._evidence(command, updated))
        except SchedulerStoreError as exc:
            if isinstance(exc, SchedulerCommandError):
                raise
            raise SchedulerCommandError(str(exc)) from exc

    @staticmethod
    def _allowed_command(command: SchedulerCommand, status: SchedulerStatus) -> bool:
        allowed = {
            SchedulerCommand.START: {SchedulerStatus.CREATED},
            SchedulerCommand.PAUSE: {SchedulerStatus.RUNNING},
            SchedulerCommand.RESUME: {
                SchedulerStatus.PAUSED, SchedulerStatus.BLOCKED, SchedulerStatus.HUMAN_REQUIRED,
            },
            SchedulerCommand.CANCEL: set(SchedulerStatus) - {
                SchedulerStatus.COMPLETED, SchedulerStatus.FAILED, SchedulerStatus.CANCELLED,
            },
        }
        return status in allowed[command]

    @staticmethod
    def _read_result(command: SchedulerCommand, state: SchedulerState) -> SchedulerCommandResult:
        label = "status snapshot" if command is SchedulerCommand.STATUS else "audit snapshot"
        return SchedulerCommandResult(
            command, state, False, (label, f"event_sequence={state.event_sequence}")
        )

    @staticmethod
    def _evidence(command: SchedulerCommand, state: SchedulerState) -> tuple[str, ...]:
        return (
            f"command={command.value}",
            f"status={state.status.value}",
            f"event_sequence={state.event_sequence}",
        )
