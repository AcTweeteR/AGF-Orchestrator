import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_task_dependencies import prepared

from agf_orchestrator.completion_audit import audit_session_completion
from agf_orchestrator.session_manager import SessionManagerError
from agf_orchestrator.session_models import SessionStatus


def test_integrated_work_does_not_invent_objective_acceptance(tmp_path, monkeypatch):
    _, state, manager, session, _, _ = prepared(tmp_path, monkeypatch, single=True)
    before = manager.get(session.session_id).to_dict()
    report = audit_session_completion(session.session_id, state_dir=state)
    assert report["integration"]["status"] == "VERIFIED"
    assert len(report["integration"]["evidence"]) == 1
    assert report["status"] == "HUMAN_REQUIRED"
    assert report["objective_completed"] is False
    assert report["objective_approval"] == "UNKNOWN"
    assert report["criterion_acceptance"] == "UNKNOWN"
    assert manager.get(session.session_id).to_dict() == before
    assert audit_session_completion(session.session_id, state_dir=state) == report


@pytest.mark.parametrize("tamper", [False, True])
def test_missing_or_tampered_work_cannot_pass_integration_audit(tmp_path, monkeypatch, tamper):
    _, state, _, session, _, _ = prepared(tmp_path, monkeypatch)
    if tamper:
        Path(session.plan_path).write_text("{}")
    report = audit_session_completion(session.session_id, state_dir=state)
    assert report["status"] == "BLOCKED"
    assert report["integration"]["status"] == "UNVERIFIED"
    assert not report["objective_completed"]


def test_silently_removing_unfinished_task_does_not_complete_plan(tmp_path, monkeypatch):
    _, state, manager, session, plan, _ = prepared(tmp_path, monkeypatch)
    payload = plan.to_dict()
    payload["tasks"] = payload["tasks"][:1]
    path, sha = manager.store.write_artifact(
        session.session_id, "dropped-task.json", json.dumps(payload) + "\n",
    )
    session.plan_path = path
    session.artifact_hashes["plan"] = sha
    manager.store.save(session)
    report = audit_session_completion(session.session_id, state_dir=state)
    assert report["integration"]["status"] == "UNVERIFIED"
    assert report["status"] == "BLOCKED"


def test_public_status_actor_and_caller_evidence_cannot_mint_completion(tmp_path, monkeypatch):
    _, _, manager, session, _, _ = prepared(tmp_path, monkeypatch, single=True)
    session.status = SessionStatus.PR_READY
    manager.store.save(session)
    for actor in ("SYSTEM", "DIRECTOR", "REVIEWER", "COMPLIANCE", "HUMAN"):
        with pytest.raises(SessionManagerError, match="Objective acceptance"):
            manager.transition(session.session_id, SessionStatus.COMPLETED, actor=actor,
                               evidence_refs=["all checks passed"], summary="done")
    assert manager.get(session.session_id).status is SessionStatus.PR_READY


def test_reusing_task_id_cannot_hide_historical_acceptance_criteria(tmp_path, monkeypatch):
    _, state, _, session, _, _ = prepared(
        tmp_path, monkeypatch, single=True,
        previous_criteria=["Original requirement includes subtraction as well as addition"],
    )
    report = audit_session_completion(session.session_id, state_dir=state)
    assert report["status"] == "BLOCKED"
    assert report["integration"]["status"] == "UNVERIFIED"


def test_completion_audit_cli_returns_non_success_without_mutating_session(tmp_path, monkeypatch):
    _, state, manager, session, _, _ = prepared(tmp_path, monkeypatch, single=True)
    before = manager.get(session.session_id).to_dict()
    result = subprocess.run(
        [sys.executable, "-m", "agf_orchestrator.cli", "session", "audit-completion",
         "--session", session.session_id, "--json"],
        env={**os.environ, "AGF_STATE_DIR": str(state),
             "PYTHONPATH": str(Path("src").resolve())},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stdout)["status"] == "HUMAN_REQUIRED"
    assert manager.get(session.session_id).to_dict() == before
