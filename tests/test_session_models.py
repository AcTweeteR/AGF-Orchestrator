import pytest

from agf_orchestrator.session_models import Session, SessionStatus, session_from_dict


def test_state_machine_and_schema_round_trip():
    session = Session("s", "p", "goal", "t", "t", "sha", "READY", SessionStatus.READY)
    restored = session_from_dict(session.to_dict())
    assert restored.status is SessionStatus.READY


def test_unknown_session_schema_requires_human():
    session = Session("s", "p", "goal", "t", "t", "sha", "READY", SessionStatus.READY)
    payload = session.to_dict()
    payload["schema_version"] = "2.0"
    with pytest.raises(ValueError, match="HUMAN_REQUIRED"):
        session_from_dict(payload)
