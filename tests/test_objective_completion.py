import json
from dataclasses import asdict, replace

import pytest
from test_delivery_reconciliation import git
from test_objective_acceptance import contract, install_fixture_generation
from test_task_dependencies import digest, prepared

from agf_orchestrator.delivery_reconciliation import DeliveryIntentStore
from agf_orchestrator.objective_acceptance import content_hash
from agf_orchestrator.project_registry import ProjectRegistry
from agf_orchestrator.session_manager import SessionManager, SessionManagerError
from agf_orchestrator.session_models import SessionStatus

CHECK = 'python -B -c "from calculator import add; assert add(2, 3) == 5"'


def ready(tmp_path, monkeypatch, *, command=CHECK, human=False):
    root, state, manager, session, plan, item = prepared(
        tmp_path, monkeypatch, single=True, integrated=False,
    )
    plan = replace(
        plan, objective_id="objective-calculator", requirement_refs=["requirement-add"],
        tasks=[replace(plan.tasks[0], validation_commands=[command],
                       requirement_refs=["requirement-add"])],
    )
    path, file_hash = manager.store.write_artifact(
        session.session_id, "accepted-plan.json", json.dumps(plan.to_dict()) + "\n",
    )
    session.plan_path = path
    session.artifact_hashes["plan"] = file_hash
    manager.store.save(session)
    registry = ProjectRegistry(state)
    project = registry.get(session.project_id)
    project = replace(project, policy=replace(project.policy, allow_live_execution=True))
    registry._save([project])
    payload = contract(project, session)
    for criterion in payload["criteria"]:
        criterion["validation_commands"] = [command]
    if human:
        payload["criteria"][0].update(method="human", validation_commands=[])
    authority_root, sign = install_fixture_generation(
        tmp_path, monkeypatch, payload, policy_state=True,
    )
    policy_hash = content_hash(json.loads((authority_root / "policy.json").read_text()))
    item = replace(item, plan_hash=digest(plan.to_dict()), task_hash=digest(asdict(plan.tasks[0])),
                   policy_hash=policy_hash, constitution_id="fixture-constitution")
    item = replace(item, content_sha256=digest(item._payload()))
    # Fixture setup precedes integration; production still enforces immutable puts.
    intent_path = DeliveryIntentStore(state).root / session.project_id / f"{item.delivery_id}.json"
    intent_path.write_text(json.dumps(item.to_dict()))
    git(root, "merge", "--ff-only", "agf/task-001")
    git(root, "push", "origin", "main")
    session = manager.resume(session.session_id)
    return root, state, manager, session, sign


def test_fresh_validation_and_canonical_evidence_close_and_survive_restart(tmp_path, monkeypatch):
    root, state, manager, session, _ = ready(tmp_path, monkeypatch)
    before = git(root, "status", "--porcelain")
    result = manager.complete(session.session_id, execute=True, confirm_execution=True)
    assert result["status"] == "SUCCESS", result
    assert result["objective_completed"]
    assert manager.get(session.session_id).status is SessionStatus.COMPLETED
    assert git(root, "status", "--porcelain") == before
    assert all(item["status"] == "SATISFIED" for item in result["report"]["criteria"])
    restarted = SessionManager(state)
    count = len(restarted.get(session.session_id).events)
    again = restarted.complete(session.session_id, execute=True, confirm_execution=True)
    assert again["status"] == "SUCCESS"
    assert len(restarted.get(session.session_id).events) == count


def test_failed_current_validation_never_completes(tmp_path, monkeypatch):
    _, _, manager, session, _ = ready(
        tmp_path, monkeypatch, command='python -B -c "raise SystemExit(1)"',
    )
    result = manager.complete(session.session_id, execute=True, confirm_execution=True)
    assert result["status"] == "BLOCKED"
    assert not result["objective_completed"]
    assert manager.get(session.session_id).status is SessionStatus.READY


def test_human_criterion_requires_exact_signed_acceptance(tmp_path, monkeypatch):
    _, _, manager, session, sign = ready(tmp_path, monkeypatch, human=True)
    result = manager.complete(session.session_id, execute=True, confirm_execution=True)
    assert result["status"] == "HUMAN_REQUIRED", result
    requests = result["report"]["required_human_acceptance"]
    assert len(requests) == 1
    request = requests[0]
    manager.store.write_artifact(
        session.session_id, request["artifact_name"],
        json.dumps({"payload": request["payload"], "envelope": sign(request["payload"])}) + "\n",
    )
    assert manager.complete(session.session_id, execute=True,
                            confirm_execution=True)["status"] == "SUCCESS"


def test_missing_execution_confirmation_cannot_run_acceptance(tmp_path, monkeypatch):
    _, _, manager, session, _ = ready(tmp_path, monkeypatch)
    with pytest.raises(SessionManagerError, match="confirmation"):
        manager.complete(session.session_id)


def test_target_drift_invalidates_historical_success(tmp_path, monkeypatch):
    root, _, manager, session, _ = ready(tmp_path, monkeypatch)
    assert manager.complete(session.session_id, execute=True,
                            confirm_execution=True)["status"] == "SUCCESS"
    (root / "calculator.py").write_text("def add(a, b): return 0\n")
    result = manager.complete(session.session_id, execute=True, confirm_execution=True)
    assert result["status"] == "BLOCKED"
    assert not result["objective_completed"]
    assert manager.get(session.session_id).status is SessionStatus.COMPLETED


def test_crash_before_session_save_does_not_leave_false_completed_state(tmp_path, monkeypatch):
    _, state, manager, session, _ = ready(tmp_path, monkeypatch)
    save = manager.store.save
    monkeypatch.setattr(manager.store, "save", lambda _: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(SessionManagerError, match="persistence"):
        manager.complete(session.session_id, execute=True, confirm_execution=True)
    assert SessionManager(state).get(session.session_id).status is SessionStatus.READY
    monkeypatch.setattr(manager.store, "save", save)
    assert manager.complete(session.session_id, execute=True,
                            confirm_execution=True)["status"] == "SUCCESS"


@pytest.mark.parametrize("changed", ["receipt", "human"])
def test_evidence_changed_during_real_validation_prevents_closure(tmp_path, monkeypatch, changed):
    import agf_orchestrator.objective_completion as completion

    _, state, manager, session, sign = ready(tmp_path, monkeypatch, human=changed == "human")
    if changed == "human":
        result = manager.complete(session.session_id, execute=True, confirm_execution=True)
        request = result["report"]["required_human_acceptance"][0]
        manager.store.write_artifact(
            session.session_id, request["artifact_name"],
            json.dumps({"payload": request["payload"], "envelope": sign(request["payload"])})
            + "\n",
        )
        path = manager.store.artifacts_dir / session.session_id / request["artifact_name"]
    else:
        path = DeliveryIntentStore(state).receipt_path(session.project_id, "delivery-test-001")
    validate = completion._run_validations

    def mutate_after_validation(*args, **kwargs):
        outcome = validate(*args, **kwargs)
        assert outcome[1]
        path.write_text("{}")
        return outcome

    monkeypatch.setattr(completion, "_run_validations", mutate_after_validation)
    result = manager.complete(session.session_id, execute=True, confirm_execution=True)
    assert result["status"] == "BLOCKED"
    assert not result["objective_completed"]
    assert manager.get(session.session_id).status is SessionStatus.READY


def test_readonly_audit_recognizes_authenticated_contract_without_closing(tmp_path, monkeypatch):
    from agf_orchestrator.completion_audit import audit_session_completion

    _, state, manager, session, _ = ready(tmp_path, monkeypatch)
    before = manager.get(session.session_id).to_dict()
    report = audit_session_completion(session.session_id, state_dir=state)
    assert report["objective_approval"] == "VERIFIED"
    assert report["criterion_acceptance"] == "UNKNOWN"
    assert report["status"] == "HUMAN_REQUIRED"
    assert not report["objective_completed"]
    assert manager.get(session.session_id).to_dict() == before


def test_disabling_project_during_validation_prevents_closure(tmp_path, monkeypatch):
    import agf_orchestrator.objective_completion as completion
    from agf_orchestrator.project_models import ProjectStatus

    _, state, manager, session, _ = ready(tmp_path, monkeypatch)
    validate = completion._run_validations

    def disable_after_validation(*args, **kwargs):
        outcome = validate(*args, **kwargs)
        assert outcome[1]
        ProjectRegistry(state).set_status(session.project_id, ProjectStatus.DISABLED)
        return outcome

    monkeypatch.setattr(completion, "_run_validations", disable_after_validation)
    result = manager.complete(session.session_id, execute=True, confirm_execution=True)
    assert result["status"] == "BLOCKED"
    assert not result["objective_completed"]
    assert manager.get(session.session_id).status is SessionStatus.READY


def test_forged_human_acceptance_never_closes(tmp_path, monkeypatch):
    _, _, manager, session, sign = ready(tmp_path, monkeypatch, human=True)
    result = manager.complete(session.session_id, execute=True, confirm_execution=True)
    request = result["report"]["required_human_acceptance"][0]
    envelope = sign(request["payload"])
    envelope["signature"] = "A" * 88
    manager.store.write_artifact(
        session.session_id, request["artifact_name"],
        json.dumps({"payload": request["payload"], "envelope": envelope}) + "\n",
    )
    result = manager.complete(session.session_id, execute=True, confirm_execution=True)
    assert result["status"] != "SUCCESS"
    assert manager.get(session.session_id).status is SessionStatus.READY


@pytest.mark.parametrize("binding", ["objective", "requirements"])
def test_canonical_plan_cannot_relabel_approved_objective(tmp_path, monkeypatch, binding):
    _, _, manager, session, _ = ready(tmp_path, monkeypatch)
    payload = json.loads(manager.store.ensure_safe_path(session.plan_path).read_text())
    if binding == "objective":
        payload["objective_id"] = "objective-unrelated"
    else:
        payload["requirement_refs"] = ["requirement-unrelated"]
        payload["tasks"][0]["requirement_refs"] = ["requirement-unrelated"]
    path, digest = manager.store.write_artifact(
        session.session_id, "relabeled-plan.json", json.dumps(payload) + "\n",
    )
    session.plan_path = path
    session.artifact_hashes["plan"] = digest
    manager.store.save(session)
    result = manager.complete(session.session_id, execute=True, confirm_execution=True)
    assert result["status"] == "BLOCKED"
    assert manager.get(session.session_id).status is SessionStatus.READY
