"""Exact Owner review proposals: unsigned, fail-closed and non-authorizing."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_owner_objective_publication import setup

from agf_orchestrator.objective_acceptance import content_hash
from agf_orchestrator.objective_models import objective_from_dict, objective_hash
from tools.objective_review_bundle import draft, prepare


def _approved(value):
    result = copy.deepcopy(value["component"])
    result["objective"]["status"] = "APPROVED"
    return result


def test_draft_is_bounded_to_real_session_and_contains_full_plan(tmp_path, monkeypatch):
    manager, project, session, store, _, previous = setup(tmp_path, monkeypatch)
    result = draft(session.session_id)
    assert result["status"] == "DRAFT_FOR_OWNER_REVIEW"
    assert result["authority_effect"] == "NONE"
    assert result["component"]["objective"]["status"] == "DRAFT"
    assert result["component"]["generation_id"] == "generation-3"
    assert result["component"]["project_id"] == project.project_id
    assert result["component"]["goal_sha256"] == content_hash(session.goal)
    assert result["component"]["objective_sha256"] == objective_hash(
        objective_from_dict(result["component"]["objective"])
    )
    assert result["target_sha"] == session.base_sha
    assert len(result["planned_tasks"]) == 1
    assert result["planned_tasks"][0]["allowed_paths"]
    assert result["planned_tasks"][0]["validation_commands"]
    assert {entry["method"] for entry in result["component"]["criteria"]} == {
        "validation", "human",
    }
    assert store.active(project.project_id) == previous
    assert not (tmp_path / ".agf-orchestrator" / "owner-objective-reviews").exists()


def test_draft_cannot_be_prepared_without_explicit_approved_content(
    tmp_path, monkeypatch,
):
    manager, project, session, store, _, previous = setup(tmp_path, monkeypatch)
    result = draft(session.session_id)
    with pytest.raises(ValueError, match="explicitly APPROVED"):
        prepare(session.session_id, result["component"])
    assert store.active(project.project_id) == previous


def test_approved_candidate_prepares_only_unsigned_review_and_is_idempotent(
    tmp_path, monkeypatch,
):
    manager, project, session, store, _, previous = setup(tmp_path, monkeypatch)
    component = _approved(draft(session.session_id))
    prepared = prepare(session.session_id, component)
    assert prepared["status"] == "UNSIGNED_PROPOSAL_PREPARED"
    assert prepared["authority_effect"] == "NONE"
    assert prepared["owner_review_required"] is True
    path = Path(prepared["proposal_path"])
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    persisted = json.loads(path.read_text())
    assert persisted["status"] == "PROPOSAL"
    assert persisted["authority_effect"] == "NONE"
    assert persisted["component"]["schema_version"] == "2.0"
    assert persisted["component"]["approved_plan_sha256"] == (
        prepared["approved_plan_sha256"]
    )
    assert content_hash(persisted) == prepared["proposal_sha256"]
    assert prepare(session.session_id, component) == prepared
    assert store.active(project.project_id) == previous


def test_exact_review_proposal_can_enter_existing_signed_publication(
    tmp_path, monkeypatch,
):
    from agf_orchestrator.objective_acceptance import read_objective_acceptance
    from tools.owner_objective_publication import publish

    manager, project, session, store, _, previous = setup(tmp_path, monkeypatch)
    component = _approved(draft(session.session_id))
    prepared = prepare(session.session_id, component)
    payload = json.loads(Path(prepared["proposal_path"]).read_text())
    first = publish(project.project_id, "owner-reviewed-real-proposal", payload)
    assert first["status"] == "PREPARED_NOT_ACTIVE"
    assert store.active(project.project_id) == previous
    verified = publish(
        project.project_id, "owner-reviewed-real-proposal", payload, action="verify",
    )
    assert verified["status"] == "READY_FOR_OWNER_ACTIVATION"
    activated = publish(
        project.project_id, "owner-reviewed-real-proposal", payload,
        action="activate", expected_manifest=verified["manifest_hash"],
    )
    assert activated["status"] == "ACTIVATED"
    acceptance = read_objective_acceptance(project, manager.get(session.session_id))
    assert acceptance.approved_plan_sha256 == prepared["approved_plan_sha256"]


@pytest.mark.parametrize(
    "field", ["project", "session", "goal", "generation", "method", "task", "validator"],
)
def test_changed_bindings_or_review_mapping_are_not_corrected_automatically(
    tmp_path, monkeypatch, field,
):
    manager, project, session, store, _, previous = setup(tmp_path, monkeypatch)
    component = _approved(draft(session.session_id))
    if field == "project":
        component["project_id"] = "project-ffffffffffffffff"
    elif field == "session":
        component["session_id"] = "session-" + "a" * 20
    elif field == "goal":
        component["goal_sha256"] = "a" * 64
    elif field == "generation":
        component["generation_id"] = "generation-99"
    elif field == "method":
        component["criteria"][0]["method"] = "unknown"
    elif field == "task":
        component["criteria"][0]["task_ids"] = ["task-does-not-exist"]
    else:
        component["criteria"][0]["validation_commands"] = ["rm -rf /"]
    with pytest.raises((ValueError, RuntimeError)):
        prepare(session.session_id, component)
    assert store.active(project.project_id) == previous


def test_immutable_unsigned_file_detects_conflict(tmp_path, monkeypatch):
    manager, project, session, store, _, previous = setup(tmp_path, monkeypatch)
    component = _approved(draft(session.session_id))
    first = prepare(session.session_id, component)
    path = Path(first["proposal_path"])
    path.write_text("malicious replacement", encoding="utf-8")
    with pytest.raises(ValueError, match="occupied"):
        prepare(session.session_id, component)
    assert store.active(project.project_id) == previous


def test_stale_or_executed_session_is_not_reused(tmp_path, monkeypatch):
    manager, project, session, store, _, previous = setup(tmp_path, monkeypatch)
    session.base_sha = "f" * 40
    manager.store.save(session)
    with pytest.raises((ValueError, RuntimeError)):
        draft(session.session_id)
    assert store.active(project.project_id) == previous


def test_cli_entry_rejects_untrusted_session_before_loading_state():
    repo = Path(__file__).resolve().parents[1]
    process = subprocess.run(
        [
            sys.executable, "-m", "tools.objective_review_bundle",
            "draft", "--session", "../private",
        ],
        cwd=repo, capture_output=True, text=True, timeout=15,
    )
    assert process.returncode == 2
    assert "Owner review refused: Owner review session identifier is invalid" in (
        process.stderr
    )
    assert "Traceback" not in process.stderr
