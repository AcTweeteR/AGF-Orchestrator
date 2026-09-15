import hashlib
import json
import subprocess
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_delivery_reconciliation import git
from test_objective_completion import ready
from test_objective_plan import approved_first_plan
from test_task_dependencies import prepared

from agf_orchestrator.delivery import DeliveryPipeline
from agf_orchestrator.delivery_reconciliation import DeliveryIntentStore
from agf_orchestrator.execution_journal import require_reconciled_execution
from agf_orchestrator.objective_plan import require_unexecuted_planning
from agf_orchestrator.session_continuation import SessionContinuation
from agf_orchestrator.session_models import SessionStatus

FLAGS = dict(execute=True, confirm_execution=True, confirm_delivery=True)


class ForbiddenPipeline:
    def deliver(self, *args, **kwargs):
        raise AssertionError("delivery must not be dispatched")


def test_integrated_objective_closes_through_real_acceptance(tmp_path, monkeypatch):
    _, _, manager, session, _ = ready(tmp_path, monkeypatch)
    result = SessionContinuation(manager, ForbiddenPipeline()).tick(session.session_id, **FLAGS)
    assert result["status"] == "SUCCESS", result
    assert manager.get(session.session_id).status is SessionStatus.COMPLETED


def test_provider_retry_required_is_forwarded_to_bounded_campaign():
    retry = SimpleNamespace(session_id="session-test", status=SessionStatus.RETRY_REQUIRED,
                            required_human_actions=[])
    manager = SimpleNamespace(assess=lambda _: retry)
    result = SessionContinuation(manager, ForbiddenPipeline())._assess(retry)
    assert result["status"] == "RETRY"
    assert result["action"] == "assessment"


def test_pending_intent_waits_then_reconciles_real_git_without_redispatch(tmp_path, monkeypatch):
    root, _, manager, session, _, _ = prepared(tmp_path, monkeypatch, integrated=False, single=True)
    driver = SessionContinuation(manager, ForbiddenPipeline())
    assert driver.tick(session.session_id, **FLAGS)["status"] == "WAIT"
    git(root, "merge", "--ff-only", "agf/task-001")
    git(root, "push", "origin", "main")
    reconciled = driver.tick(session.session_id, **FLAGS)
    assert reconciled["action"] == "reconcile"
    assert reconciled["status"] == "CONTINUE"
    # Integration alone cannot supply the absent signed Objective contract.
    assert driver.tick(session.session_id, **FLAGS)["status"] == "HUMAN_REQUIRED"


def test_corrupt_integration_evidence_is_not_treated_as_pending_task(tmp_path, monkeypatch):
    _, state, manager, session, _, item = prepared(tmp_path, monkeypatch, single=True)
    DeliveryIntentStore(state).receipt_path(session.project_id, item.delivery_id).write_text("{}")
    result = SessionContinuation(manager, ForbiddenPipeline()).tick(session.session_id, **FLAGS)
    assert result["status"] in {"BLOCKED", "HUMAN_REQUIRED"}
    assert not result["objective_completed"]


def test_interrupted_dispatch_does_not_invoke_pipeline_twice(tmp_path, monkeypatch):
    import agf_orchestrator.session_continuation as continuation

    _, _, manager, session, _, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(continuation, "admit_live_delivery", lambda *args: None)
    monkeypatch.setattr(SessionContinuation, "_objective_gate", lambda *args: None)
    calls = []

    def crash(self, plan, task_id, *args, **kwargs):
        calls.append(task_id)
        raise RuntimeError("simulated process interruption")

    monkeypatch.setattr(DeliveryPipeline, "_deliver", crash)
    driver = SessionContinuation(manager, DeliveryPipeline())
    first = driver.tick(session.session_id, **FLAGS)
    assert first["status"] == "BLOCKED"
    restarted = SessionContinuation(manager, DeliveryPipeline())
    second = restarted.tick(session.session_id, **FLAGS)
    assert second["status"] == "HUMAN_REQUIRED"
    assert calls == ["task-002"]
    assert len(list((manager.store.artifacts_dir / session.session_id).glob(
        "execution-started-*.json",
    ))) == 1
    assert not list((manager.store.artifacts_dir / session.session_id).glob(
        "continuation-*-started.json",
    ))


def test_pipeline_success_text_never_closes_objective(tmp_path, monkeypatch):
    import agf_orchestrator.session_continuation as continuation

    _, _, manager, session, _, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(continuation, "admit_live_delivery", lambda *args: None)
    monkeypatch.setattr(SessionContinuation, "_objective_gate", lambda *args: None)

    monkeypatch.setattr(DeliveryPipeline, "_deliver", lambda *args, **kwargs:
                        SimpleNamespace(to_dict=lambda: {"status": "SUCCESS"}))
    driver = SessionContinuation(manager, DeliveryPipeline())
    result = driver.tick(session.session_id, **FLAGS)
    assert result["status"] == "CONTINUE"
    assert not result["objective_completed"]
    assert json.loads(Path(result["evidence_path"]).read_text())["status"] == "SUCCESS"
    assert driver.tick(session.session_id, **FLAGS)["status"] == "HUMAN_REQUIRED"
    assert manager.get(session.session_id).status is SessionStatus.READY


def test_failed_implementation_retries_within_existing_budget(tmp_path, monkeypatch):
    import agf_orchestrator.session_continuation as continuation

    _, _, manager, session, _, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(continuation, "admit_live_delivery", lambda *args: None)
    monkeypatch.setattr(SessionContinuation, "_objective_gate", lambda *args: None)
    calls = []

    def unavailable(self, plan, task_id, *args, **kwargs):
        calls.append(self.max_correction_rounds)
        return SimpleNamespace(to_dict=lambda: {
            "plan_id": plan.plan_id, "task_id": task_id,
            "status": "BLOCKED", "execution_status": "FAILED", "review_status": "NOT_RUN",
            "push_status": "NOT_REQUESTED", "commit_sha": None, "correction_rounds": 0,
        })

    monkeypatch.setattr(DeliveryPipeline, "_deliver", unavailable)
    for _ in range(3):
        result = SessionContinuation(manager, DeliveryPipeline()).tick(
            session.session_id, **FLAGS,
        )
        assert result["status"] == "CONTINUE", result
    result = SessionContinuation(manager, DeliveryPipeline()).tick(
        session.session_id, **FLAGS,
    )
    assert result["status"] == "BLOCKED"
    assert result["action"] == "retry-budget"
    assert calls == [2, 1, 0]
    assert not result["objective_completed"]


def test_known_failure_survives_external_target_rebinding(tmp_path, monkeypatch):
    root, _, manager, session, _, _, intent_template = approved_first_plan(
        tmp_path, monkeypatch, persist_intent=False,
    )
    session = manager.bind_objective_plan(session.session_id)
    from agf_orchestrator.models import plan_from_dict

    plan = plan_from_dict(json.loads(Path(session.plan_path).read_text()))

    def failed(self, plan, task_id, *args, **kwargs):
        return SimpleNamespace(to_dict=lambda: {
            "base_sha": plan.repository.head_sha,
            "plan_id": plan.plan_id,
            "task_id": task_id,
            "status": "FAILED",
            "execution_status": "FAILED",
            "review_status": "NOT_RUN",
            "push_status": "NOT_REQUESTED",
            "commit_sha": None,
            "correction_rounds": 0,
            "evidence": ["cleanup succeeded: yes", "caller repository clean: yes"],
        })

    monkeypatch.setattr(DeliveryPipeline, "_deliver", failed)
    DeliveryPipeline().deliver(
        plan, "task-001", str(root), execute=True,
        project_id=session.project_id, session_id=session.session_id,
    )
    old_plan_hash = session.artifact_hashes["plan"]
    (root / "external").write_text("advance")
    git(root, "add", "external")
    git(root, "commit", "-m", "external advance")
    target = git(root, "rev-parse", "HEAD")
    replacement = replace(
        plan, repository=replace(plan.repository, head_sha=target),
        objective_id=None, requirement_refs=[],
        tasks=[replace(task, requirement_refs=[]) for task in plan.tasks],
        scope={key: value for key, value in plan.scope.items()
               if key not in {"lineage", "predecessor_plan_sha256"}},
    )
    plan_path, plan_hash = manager.store.write_artifact(
        session.session_id, "plan-after-external.json",
        json.dumps(replacement.to_dict(), sort_keys=True) + "\n",
    )
    origin_path = manager.store.artifacts_dir / session.session_id / "planning-origin.json"
    origin = {
        "schema_version": "1.0", "protocol": "governed-session/1",
        "session_id": session.session_id, "project_id": session.project_id,
        "initial_plan_sha256": plan_hash,
    }
    origin_path.write_text(json.dumps(origin, sort_keys=True) + "\n")
    rebound = replace(
        manager.get(session.session_id), base_sha=target, plan_path=str(plan_path),
        artifact_hashes={
            "plan": plan_hash,
            "planning_origin": manager.store.artifact_hash(str(origin_path)),
            "external_advancement": "e" * 64,
            "historical:plan": old_plan_hash,
            "historical:objective_binding": "b" * 64,
        },
    )
    rebound = manager._append_event(
        rebound, rebound.status, rebound.status, "external target reconciled", [], [],
        "RELEASE_MANAGER", "external-advance:external-rebind-001",
    )
    manager.store.save(rebound)
    advancement = SimpleNamespace(
        target_sha=target, evidence_hash="e" * 64,
        project_id=session.project_id,
        session_id=session.session_id, repository_identity=plan.repository.origin,
        previous_sha=plan.repository.head_sha, branch=plan.repository.branch,
    )
    monkeypatch.setattr(
        "agf_orchestrator.external_advancement.ExternalAdvancementStore",
        lambda _state: SimpleNamespace(get=lambda *_args: advancement),
    )

    advancement.target_sha = "f" * 40
    from agf_orchestrator.execution_journal import ExecutionRecoveryRequired

    with pytest.raises(ExecutionRecoveryRequired, match="ancestor relation"):
        require_reconciled_execution(manager.store, rebound, replacement)
    advancement.target_sha = target
    assert require_reconciled_execution(manager.store, rebound, replacement) == 1
    require_unexecuted_planning(rebound, manager.store)
    from test_objective_acceptance import contract

    from agf_orchestrator.objective_acceptance import AcceptanceCriterion, content_hash
    from agf_orchestrator.objective_models import objective_from_dict
    from agf_orchestrator.objective_plan import project_objective_plan
    from agf_orchestrator.task_dependencies import _read_verified

    component = contract(manager.registry.get(session.project_id), rebound)
    objective = objective_from_dict(component["objective"])
    criteria = tuple(AcceptanceCriterion(**item) for item in component["criteria"])
    projected = project_objective_plan(replacement, rebound, objective, criteria)
    projected_path, projected_hash = manager.store.write_artifact(
        session.session_id, "objective-plan-after-external.json",
        json.dumps(projected.to_dict(), sort_keys=True) + "\n",
    )
    rebound = replace(
        rebound, plan_path=projected_path,
        artifact_hashes={**rebound.artifact_hashes, "plan": projected_hash},
    )
    manager.store.save(rebound)
    approved_hash = content_hash(projected.to_dict())
    git(root, "checkout", "-b", "agf/recovered-task-001")
    (root / "calculator.py").write_text("def add(a, b):\n    return a + b\n")
    git(root, "commit", "-am", "recovered delivery")
    candidate = git(root, "rev-parse", "HEAD")
    tree = git(root, "rev-parse", "HEAD^{tree}")
    git(root, "push", "-u", "origin", "agf/recovered-task-001")
    git(root, "checkout", plan.repository.branch)
    diff = subprocess.run(
        ["git", "-C", str(root), "diff", f"{target}..{candidate}"],
        check=True, capture_output=True,
    ).stdout
    delivery_intent = replace(
        intent_template, delivery_id="delivery-recovered-001",
        project_id=session.project_id, session_id=session.session_id,
        plan_id=projected.plan_id, plan_hash=content_hash(projected.to_dict()),
        task_id="task-001", task_hash=content_hash(asdict(projected.tasks[0])),
        repository_identity=projected.repository.origin, base_sha=target,
        candidate_sha=candidate, candidate_tree_sha=tree,
        delivery_branch="agf/recovered-task-001", target_branch=plan.repository.branch,
        diff_sha256=hashlib.sha256(diff).hexdigest(), content_sha256="0" * 64,
    )
    delivery_intent = replace(
        delivery_intent, content_sha256=content_hash(delivery_intent._payload()),
    )
    intent_store = DeliveryIntentStore(manager.store.state_dir)
    intent_store.put(delivery_intent)
    git(root, "merge", "--ff-only", "agf/recovered-task-001")
    git(root, "push", "origin", plan.repository.branch)
    resumed = manager.resume(session.session_id)
    integrated = plan_from_dict(json.loads(Path(resumed.plan_path).read_text()))
    proofs = _read_verified(
        session.session_id, integrated, {"task-001"}, str(root),
        state_dir=manager.store.state_dir, all_tasks=True,
        approved_plan_sha256=approved_hash,
    )
    assert len(proofs) == 1
    receipt = intent_store.receipt_path(session.project_id, delivery_intent.delivery_id)
    original_receipt = receipt.read_text()
    receipt.write_text("{}")
    from agf_orchestrator.task_dependencies import DependencyEvidenceError

    with pytest.raises(DependencyEvidenceError):
        _read_verified(
            session.session_id, integrated, {"task-001"}, str(root),
            state_dir=manager.store.state_dir, all_tasks=True,
            approved_plan_sha256=approved_hash,
        )
    receipt.write_text(original_receipt)


def test_historical_failure_requires_persisted_external_advancement(tmp_path, monkeypatch):
    root, _, manager, session, _, _, _ = approved_first_plan(
        tmp_path, monkeypatch, persist_intent=False,
    )
    session = manager.bind_objective_plan(session.session_id)
    from agf_orchestrator.execution_journal import ExecutionRecoveryRequired
    from agf_orchestrator.models import plan_from_dict

    plan = plan_from_dict(json.loads(Path(session.plan_path).read_text()))

    def failed(self, plan, task_id, *args, **kwargs):
        return SimpleNamespace(to_dict=lambda: {
            "base_sha": plan.repository.head_sha, "plan_id": plan.plan_id,
            "task_id": task_id, "status": "FAILED", "execution_status": "FAILED",
            "review_status": "NOT_RUN", "push_status": "NOT_REQUESTED",
            "commit_sha": None, "correction_rounds": 0,
            "evidence": ["cleanup succeeded: yes", "caller repository clean: yes"],
        })

    monkeypatch.setattr(DeliveryPipeline, "_deliver", failed)
    DeliveryPipeline().deliver(
        plan, "task-001", str(root), execute=True,
        project_id=session.project_id, session_id=session.session_id,
    )
    old_hash = session.artifact_hashes["plan"]
    (root / "external").write_text("advance")
    git(root, "add", "external")
    git(root, "commit", "-m", "external advance")
    target = git(root, "rev-parse", "HEAD")
    replacement = replace(
        plan, repository=replace(plan.repository, head_sha=target), objective_id=None,
        requirement_refs=[], tasks=[replace(task, requirement_refs=[]) for task in plan.tasks],
        scope={key: value for key, value in plan.scope.items()
               if key not in {"lineage", "predecessor_plan_sha256"}},
    )
    path, digest = manager.store.write_artifact(
        session.session_id, "untrusted-rebind.json",
        json.dumps(replacement.to_dict(), sort_keys=True) + "\n",
    )
    rebound = replace(
        session, base_sha=target, plan_path=path,
        artifact_hashes={"plan": digest, "historical:plan": old_hash,
                         "external_advancement": "e" * 64},
    )
    manager.store.save(rebound)
    with pytest.raises(ExecutionRecoveryRequired, match="authenticated advance"):
        require_reconciled_execution(manager.store, rebound, replacement)


def test_reconciliation_does_not_resolve_stale_architect_configuration(tmp_path, monkeypatch):
    _, _, manager, session, _, _ = prepared(tmp_path, monkeypatch, integrated=False, single=True)

    def forbidden_factory():
        raise AssertionError("waiting must not load planning configuration")

    result = SessionContinuation(manager, ForbiddenPipeline(),
                                 architect_factory=forbidden_factory).tick(
        session.session_id, **FLAGS,
    )
    assert result["status"] == "WAIT"


def test_registered_delivery_dispatch_excludes_another_process(tmp_path, monkeypatch):
    import multiprocessing

    from agf_orchestrator.delivery import DeliveryPipeline
    from agf_orchestrator.locking import LockError

    monkeypatch.setenv("AGF_STATE_DIR", str(tmp_path / "state"))
    project_id = "project-continuation-lock"
    context = multiprocessing.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)

    def competing_dispatch():
        DeliveryPipeline._deliver = lambda *args, **kwargs: "DUPLICATE"
        try:
            result = DeliveryPipeline().deliver(None, "task-001", str(tmp_path),
                                                execute=True, project_id=project_id)
        except LockError:
            result = "LOCKED"
        sender.send(result)
        sender.close()

    def first_dispatch(*args, **kwargs):
        process = context.Process(target=competing_dispatch)
        process.start()
        process.join(10)
        try:
            assert process.exitcode == 0
            assert receiver.poll(1)
            assert receiver.recv() == "LOCKED"
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)
        return "FIRST"

    monkeypatch.setattr(DeliveryPipeline, "_deliver", first_dispatch)
    try:
        assert DeliveryPipeline().deliver(None, "task-001", str(tmp_path), execute=True,
                                          project_id=project_id) == "FIRST"
    finally:
        receiver.close()
        sender.close()


def test_direct_uncertain_execution_blocks_continuation(tmp_path, monkeypatch):
    import agf_orchestrator.session_continuation as continuation
    from agf_orchestrator.execution_journal import record_execution_start

    _, _, manager, session, plan, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(continuation, "admit_live_delivery", lambda *args: None)
    monkeypatch.setattr(SessionContinuation, "_objective_gate", lambda *args: None)
    record_execution_start(session.session_id, plan, "task-002")
    result = SessionContinuation(manager, ForbiddenPipeline()).tick(session.session_id, **FLAGS)
    assert result["status"] == "HUMAN_REQUIRED"
    assert not list((manager.store.artifacts_dir / session.session_id).glob(
        "continuation-*-started.json",
    ))


def test_dispatch_rechecks_direct_execution_after_shared_lock(tmp_path, monkeypatch):
    import agf_orchestrator.locking as locking
    from agf_orchestrator.delivery import DeliveryPipeline
    from agf_orchestrator.execution_journal import (
        ExecutionRecoveryRequired,
        record_execution_start,
    )

    root, _, _, session, plan, _ = prepared(tmp_path, monkeypatch)
    original = locking.project_lock

    @contextmanager
    def raced_lock(*args, **kwargs):
        with original(*args, **kwargs):
            # A different entry point recorded a dispatch after selection.
            record_execution_start(session.session_id, plan, "task-002")
            yield

    monkeypatch.setattr(locking, "project_lock", raced_lock)
    monkeypatch.setattr(DeliveryPipeline, "_deliver", ForbiddenPipeline().deliver)
    with pytest.raises(ExecutionRecoveryRequired, match="no verified outcome"):
        DeliveryPipeline().deliver(plan, "task-002", str(root), execute=True,
                                   project_id=session.project_id, session_id=session.session_id)


def test_paired_failed_delivery_outcomes_preserve_bounded_retries(tmp_path, monkeypatch):
    import agf_orchestrator.session_continuation as continuation
    from agf_orchestrator.delivery import DeliveryPipeline

    _, _, manager, session, _, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(continuation, "admit_live_delivery", lambda *args: None)
    monkeypatch.setattr(SessionContinuation, "_objective_gate", lambda *args: None)
    calls = []

    def failed(self, plan, task_id, *args, **kwargs):
        calls.append(task_id)
        return SimpleNamespace(to_dict=lambda: {
            "plan_id": plan.plan_id, "task_id": task_id,
            "status": "BLOCKED", "execution_status": "FAILED", "review_status": "NOT_RUN",
            "push_status": "NOT_REQUESTED", "commit_sha": None, "correction_rounds": 0,
        })

    monkeypatch.setattr(DeliveryPipeline, "_deliver", failed)
    for _ in range(3):
        result = SessionContinuation(manager, DeliveryPipeline()).tick(session.session_id, **FLAGS)
        assert result["status"] == "CONTINUE", result
    result = SessionContinuation(manager, DeliveryPipeline()).tick(session.session_id, **FLAGS)
    assert result["action"] == "retry-budget"
    assert calls == ["task-002"] * 3
    directory = manager.store.artifacts_dir / session.session_id
    assert len(list(directory.glob("execution-started-*.json"))) == 3
    assert len(list(directory.glob("execution-finished-*.json"))) == 3


def test_direct_known_failure_consumes_continuation_retry_budget(tmp_path, monkeypatch):
    import agf_orchestrator.session_continuation as continuation
    from agf_orchestrator.delivery import DeliveryPipeline

    root, _, manager, session, plan, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(continuation, "admit_live_delivery", lambda *args: None)
    monkeypatch.setattr(SessionContinuation, "_objective_gate", lambda *args: None)
    calls = []

    def failed(self, plan, task_id, *args, **kwargs):
        calls.append(task_id)
        return SimpleNamespace(to_dict=lambda: {
            "plan_id": plan.plan_id, "task_id": task_id,
            "status": "FAILED", "execution_status": "FAILED", "review_status": "NOT_RUN",
            "push_status": "NOT_REQUESTED", "commit_sha": None, "correction_rounds": 0,
        })

    monkeypatch.setattr(DeliveryPipeline, "_deliver", failed)
    DeliveryPipeline().deliver(plan, "task-002", str(root), execute=True,
                               project_id=session.project_id, session_id=session.session_id)
    for _ in range(2):
        assert SessionContinuation(manager, DeliveryPipeline()).tick(
            session.session_id, **FLAGS,
        )["status"] == "CONTINUE"
    assert SessionContinuation(manager, DeliveryPipeline()).tick(
        session.session_id, **FLAGS,
    )["action"] == "retry-budget"
    assert calls == ["task-002"] * 3


def test_old_integrated_receipt_does_not_resolve_later_uncertain_execution(tmp_path, monkeypatch):
    from agf_orchestrator.execution_journal import (
        ExecutionRecoveryRequired,
        record_execution_start,
        require_reconciled_execution,
    )

    _, _, manager, session, plan, _ = prepared(tmp_path, monkeypatch)
    # task-001 has an older integrated receipt, while this start uses the new plan.
    record_execution_start(session.session_id, plan, "task-001")
    with pytest.raises(ExecutionRecoveryRequired, match="no verified outcome"):
        require_reconciled_execution(manager.store, session, plan)


def test_pending_dispatch_after_integration_prevents_objective_closure(tmp_path, monkeypatch):
    from agf_orchestrator.execution_journal import record_execution_start
    from agf_orchestrator.models import plan_from_dict

    _, _, manager, session, _ = ready(tmp_path, monkeypatch)
    plan = plan_from_dict(json.loads(manager.store.ensure_safe_path(session.plan_path).read_text()))
    record_execution_start(session.session_id, plan, plan.tasks[0].task_id)
    result = SessionContinuation(manager, ForbiddenPipeline()).tick(session.session_id, **FLAGS)
    assert result["status"] in {"BLOCKED", "HUMAN_REQUIRED"}, result
    assert not result["objective_completed"]


def test_direct_delivery_cannot_reset_registered_retry_budget(tmp_path, monkeypatch):
    from agf_orchestrator.delivery import DeliveryPipeline
    from agf_orchestrator.execution_journal import ExecutionRecoveryRequired

    root, _, _, session, plan, _ = prepared(tmp_path, monkeypatch)
    limits = []

    def failed(self, plan, task_id, *args, **kwargs):
        limits.append(self.max_correction_rounds)
        return SimpleNamespace(to_dict=lambda: {
            "plan_id": plan.plan_id, "task_id": task_id,
            "status": "FAILED", "execution_status": "FAILED", "review_status": "NOT_RUN",
            "push_status": "NOT_REQUESTED", "commit_sha": None, "correction_rounds": 0,
        })

    monkeypatch.setattr(DeliveryPipeline, "_deliver", failed)
    for _ in range(3):
        DeliveryPipeline().deliver(plan, "task-002", str(root), execute=True,
                                   project_id=session.project_id, session_id=session.session_id)
    with pytest.raises(ExecutionRecoveryRequired, match="budget exhausted"):
        DeliveryPipeline().deliver(plan, "task-002", str(root), execute=True,
                                   project_id=session.project_id, session_id=session.session_id)
    assert limits == [2, 1, 0]


def test_continuation_crash_before_pipeline_prevents_objective_closure(tmp_path, monkeypatch):
    from dataclasses import asdict

    from agf_orchestrator.models import plan_from_dict
    from agf_orchestrator.objective_acceptance import content_hash

    _, _, manager, session, _ = ready(tmp_path, monkeypatch)
    plan = plan_from_dict(json.loads(manager.store.ensure_safe_path(session.plan_path).read_text()))
    task = plan.tasks[0]
    binding = {"project_id": session.project_id, "session_id": session.session_id,
               "plan_sha256": session.artifact_hashes["plan"],
               "task_sha256": content_hash(asdict(task)), "task_id": task.task_id,
               "base_sha": session.base_sha}
    manager.store.write_artifact(
        session.session_id, f"continuation-{content_hash(binding)}-attempt-0-started.json",
        json.dumps({"binding": binding, "worktrees_sha256": "unknown"}),
    )
    result = SessionContinuation(manager, ForbiddenPipeline()).tick(session.session_id, **FLAGS)
    assert result["status"] in {"BLOCKED", "HUMAN_REQUIRED"}, result
    assert not result["objective_completed"]


@pytest.mark.parametrize("entry", ["direct", "continuation"])
def test_historical_coordinator_crash_blocks_every_dispatch_entry(tmp_path, monkeypatch, entry):
    from dataclasses import asdict

    import agf_orchestrator.session_continuation as continuation
    from agf_orchestrator.execution_journal import ExecutionRecoveryRequired
    from agf_orchestrator.objective_acceptance import content_hash

    root, _, manager, session, plan, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(continuation, "admit_live_delivery", lambda *args: None)
    monkeypatch.setattr(SessionContinuation, "_objective_gate", lambda *args: None)
    task = plan.tasks[-1]
    binding = {"project_id": session.project_id, "session_id": session.session_id,
               "plan_sha256": session.artifact_hashes["plan"],
               "task_sha256": content_hash(asdict(task)), "task_id": task.task_id,
               "base_sha": session.base_sha}
    manager.store.write_artifact(
        session.session_id, f"continuation-{content_hash(binding)}-attempt-0-started.json",
        json.dumps({"binding": binding, "worktrees_sha256": "unknown"}),
    )
    monkeypatch.setattr(DeliveryPipeline, "_deliver", ForbiddenPipeline().deliver)
    if entry == "direct":
        with pytest.raises(ExecutionRecoveryRequired, match="continuation dispatch"):
            DeliveryPipeline().deliver(plan, task.task_id, str(root), execute=True,
                                       project_id=session.project_id, session_id=session.session_id)
    else:
        result = SessionContinuation(manager, DeliveryPipeline()).tick(session.session_id, **FLAGS)
        assert result["status"] == "HUMAN_REQUIRED", result
    assert not list((manager.store.artifacts_dir / session.session_id).glob(
        "execution-started-*.json",
    ))
