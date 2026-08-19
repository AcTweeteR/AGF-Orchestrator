import pytest

from agf_orchestrator.external_actions import (
    ExternalActionError,
    ExternalActionExecutor,
    ExternalActionRequest,
)


def request():
    return ExternalActionRequest.from_payload(
        {"action": "merge", "risk": "LOW", "payload": {"pr": 1}},
        project_id="project-ai-fund", session_id="session-ai-fund",
    )


def test_executor_fail_closed_without_authority_binding():
    with pytest.raises(ExternalActionError, match="not authority-bound"):
        ExternalActionExecutor().execute_authorized(request())


def test_executor_requires_authorization_before_execution():
    calls = []

    def authorize(item):
        calls.append("authorize")

    def execute(item):
        calls.append("execute")
        return "merged"

    assert ExternalActionExecutor(authorize, execute).execute_authorized(request()) == "merged"
    assert calls == ["authorize", "execute"]


def test_denied_authorization_never_executes():
    executed = []

    def deny(item):
        raise ExternalActionError("policy blocked")

    with pytest.raises(ExternalActionError, match="policy blocked"):
        ExternalActionExecutor(deny, lambda item: executed.append(True)).execute_authorized(
            request()
        )
    assert executed == []
