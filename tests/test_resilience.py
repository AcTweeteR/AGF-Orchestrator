import json
from dataclasses import replace

import pytest

import agf_orchestrator.resilience as resilience
from agf_orchestrator.project_models import Project, ProjectPolicy, ProjectStatus
from agf_orchestrator.resilience import (
    DiagnosticStatus,
    ResilienceError,
    bind_workspace,
    build_evidence_archive,
    derive_scorecard,
    doctor,
)
from agf_orchestrator.session_models import Session, SessionEvent, SessionStatus
from agf_orchestrator.session_store import SessionStore


def project(tmp_path):
    return Project("project-1", "alpha", str(tmp_path / "repo"),
                   "https://github.com/example/alpha.git", "main", "a" * 40,
                   "2026-08-24T00:00:00Z", "2026-08-24T00:00:00Z",
                   ProjectStatus.ACTIVE, ProjectPolicy())


def session():
    return Session("session-1", "project-1", "goal", "t", "t", "a" * 40,
                   "READY", SessionStatus.READY)


def test_workspace_binding_rejects_wrong_project(tmp_path):
    p = project(tmp_path)
    with pytest.raises(ResilienceError, match="does not match"):
        bind_workspace(p, repository_root=str(tmp_path / "other"),
                       origin_url=p.origin_url, target_sha=p.current_head_sha)


def test_doctor_unknown_without_workspace_and_archive_is_deterministic(tmp_path):
    p = project(tmp_path)
    store = SessionStore(tmp_path / "state")
    s = session()
    store.write_artifact(s.session_id, "plan.json", json.dumps({"ok": True}) + "\n")
    s.artifact_hashes["plan"] = store.artifact_hash(
        str(store.artifacts_dir / s.session_id / "plan.json")
    )
    findings = doctor(s, store)
    assert findings[0].status is DiagnosticStatus.UNKNOWN
    archive = build_evidence_archive(s, store)
    assert archive == build_evidence_archive(s, store)
    assert archive["scorecard"]["terminal"] is False
    assert p.project_id == s.project_id


def test_archive_rejects_secret_shaped_evidence(tmp_path):
    store = SessionStore(tmp_path / "state")
    s = session()
    store.write_artifact(s.session_id, "secret.json", '{"api_key": "do-not-store"}\n')
    with pytest.raises(ResilienceError, match="secret-shaped"):
        build_evidence_archive(s, store)


def test_scorecard_is_evidence_derived(tmp_path):
    scorecard = derive_scorecard(session())
    assert scorecard.event_count == 0
    assert scorecard.artifact_count == 0
    assert scorecard.evidence_count == 0
    assert scorecard.terminal is False


def valid_session_with_lineage():
    s = session()
    s.events.append(SessionEvent(
        "event-1", "operation-1", "2026-08-24T00:00:00Z", s.session_id,
        "PLANNING_TO_READY", "PLANNING", "READY", "session started", [], [], "DIRECTOR",
    ))
    return s


def test_lineage_requires_real_valid_transition_and_rejects_tamper(tmp_path):
    s = valid_session_with_lineage()
    store = SessionStore(tmp_path / "state")
    assert doctor(s, store)[-1].status is DiagnosticStatus.PASS
    s.events[0] = replace(s.events[0], session_id="session-foreign")
    assert doctor(s, store)[-1].status is DiagnosticStatus.FAIL


def test_lineage_rejects_reordering_and_replay(tmp_path):
    s = valid_session_with_lineage()
    s.events.append(replace(
        s.events[0], event_id="event-2", operation_id="operation-2",
        timestamp="2026-08-24T00:01:00Z", from_status="READY",
        to_status="EXECUTING", event_type="READY_TO_EXECUTING",
    ))
    store = SessionStore(tmp_path / "state")
    assert doctor(s, store)[-1].status is DiagnosticStatus.FAIL
    s.events[1] = replace(s.events[1], operation_id="operation-1")
    assert doctor(s, store)[-1].status is DiagnosticStatus.FAIL


def test_archive_rejects_traversal_and_symlink_escape(tmp_path):
    store = SessionStore(tmp_path / "state")
    bad = Session(
        "../foreign", "project-1", "goal", "t", "t", "a" * 40,
        "READY", SessionStatus.READY,
    )
    with pytest.raises(ResilienceError):
        build_evidence_archive(bad, store)
    outside = tmp_path / "outside"
    outside.mkdir()
    store.artifacts_dir.mkdir(parents=True)
    (store.artifacts_dir / "session-1").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ResilienceError):
        build_evidence_archive(session(), store)
    (store.artifacts_dir / "session-1").unlink()
    directory = store.artifacts_dir / "session-1"
    directory.mkdir()
    (outside / "foreign.json").write_text("{}\n")
    (directory / "foreign.json").symlink_to(outside / "foreign.json")
    with pytest.raises(ResilienceError):
        build_evidence_archive(session(), store)


def test_archive_checks_size_before_read_and_bounds_aggregate(tmp_path, monkeypatch):
    monkeypatch.setattr(resilience, "_MAX_ARCHIVE_BYTES", 6)
    store = SessionStore(tmp_path / "state")
    s = session()
    store.write_artifact(s.session_id, "large.json", "1234567")
    with pytest.raises(ResilienceError, match="byte limit"):
        build_evidence_archive(s, store)
    (store.artifacts_dir / s.session_id / "large.json").unlink()
    store.write_artifact(s.session_id, "first.json", "1234")
    store.write_artifact(s.session_id, "second.json", "5678")
    with pytest.raises(ResilienceError, match="byte limit"):
        build_evidence_archive(s, store)
