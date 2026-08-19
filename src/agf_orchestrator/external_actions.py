"""Single guarded boundary for campaign-triggered external actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


class ExternalActionError(RuntimeError):
    """Raised when a campaign attempts an ungoverned external effect."""


@dataclass(frozen=True)
class ExternalActionRequest:
    action: str
    project_id: str
    session_id: str
    risk: str
    payload: dict[str, Any]

    @classmethod
    def from_payload(
        cls, payload: object, *, project_id: str, session_id: str
    ) -> "ExternalActionRequest":
        if not isinstance(payload, dict):
            raise ExternalActionError("external action must be an object")
        action = payload.get("action")
        risk = payload.get("risk")
        details = payload.get("payload", {})
        if not isinstance(action, str) or not action or not isinstance(risk, str):
            raise ExternalActionError("external action identity is invalid")
        if not isinstance(details, dict):
            raise ExternalActionError("external action payload is invalid")
        return cls(action, project_id, session_id, risk, details)


class ExternalActionExecutor:
    """Execute only after the caller supplies the canonical authority gate."""

    def __init__(
        self,
        authorize: Callable[[ExternalActionRequest], None] | None = None,
        execute: Callable[[ExternalActionRequest], str] | None = None,
    ):
        self._authorize = authorize
        self._execute = execute

    def execute_authorized(self, request: ExternalActionRequest) -> str:
        if self._authorize is None or self._execute is None:
            raise ExternalActionError("external action executor is not authority-bound")
        self._authorize(request)
        return self._execute(request)
