import json

import pytest

from agf_orchestrator.project_models import Project, ProjectPolicy, ProjectStatus
from agf_orchestrator.resilience import (
    DiagnosticStatus,
    ResilienceError,
    bind_workspace,
    build_evidence_archive,
    derive_scorecard,
    doctor,
)
from agf_orchestrator.session_models import Session, SessionStatus
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
