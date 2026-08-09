from dataclasses import replace

import pytest

from agf_orchestrator.learning_evidence import LearningEvidence, OutcomeStatus, evidence_hash
from agf_orchestrator.learning_pilot import LearningPilot, LearningPilotError

PROJECT = "project-efc8e8ef7be7050b"


def evidence(observation, delta, observed_at="2026-08-10T12:00:00Z"):
    item = LearningEvidence(
        "1.0", f"learning-{observation}", PROJECT, f"observation-{observation}",
        "subject-codex", OutcomeStatus.SUCCESS, delta, "pilot:test", observed_at, "0" * 64,
    )
    return replace(item, content_sha256=evidence_hash(item))


def test_learning_pilot_proves_summary_proposal_restart_and_rollback():
    report = LearningPilot().run(
        (evidence("001", 1), evidence("002", 2)),
        project_id=PROJECT, now="2026-08-10T12:00:01Z",
    )
    assert report.restart_verified
    assert report.rollback_verified
    assert [event.name for event in report.events] == [
        "evidence", "summary", "proposal", "restart", "rollback",
    ]


def test_learning_pilot_is_deterministic():
    records = (evidence("001", 1), evidence("002", 2))
    kwargs = {"project_id": PROJECT, "now": "2026-08-10T12:00:01Z"}
    assert LearningPilot().run(records, **kwargs) == LearningPilot().run(records, **kwargs)


def test_learning_pilot_fails_closed_on_stale_evidence_or_wrong_project():
    with pytest.raises(LearningPilotError, match="stale"):
        LearningPilot().run(
            (evidence("001", 1, "2026-08-08T12:00:00Z"),),
            project_id=PROJECT, now="2026-08-10T12:00:00Z",
        )
    with pytest.raises(LearningPilotError, match="project binding"):
        LearningPilot().run(
            (evidence("001", 1),), project_id="project-other",
            now="2026-08-10T12:00:00Z",
        )
