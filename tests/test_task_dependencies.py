import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from test_delivery_reconciliation import fixture, git, intent
from test_executor import fake_codex

from agf_orchestrator.adapters.codex import CodexAdapter, CodexInvocationProfile
from agf_orchestrator.delivery_reconciliation import DeliveryIntentStore
from agf_orchestrator.execution_models import ExecutionStatus
from agf_orchestrator.executor import Executor
from agf_orchestrator.models import PlanStatus, Task, plan_from_dict
from agf_orchestrator.project_registry import ProjectRegistry
from agf_orchestrator.session_manager import SessionManager
from agf_orchestrator.task_dependencies import DependencyEvidenceError, verify_task_dependencies


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def prepared(tmp_path, monkeypatch, *, integrated=True, third=False, top_level=False):
    root, base, candidate, remote = fixture(tmp_path)
    remote = Path(remote).as_uri()
    git(root, "remote", "set-url", "origin", remote)
    state = tmp_path / "state"
    monkeypatch.setenv("AGF_STATE_DIR", str(state))
    registry = ProjectRegistry(state)
    registry.add("alpha", root)
    manager = SessionManager(state)
    session = manager.start("alpha", "Correct calculator and document it")
    original = plan_from_dict(json.loads(Path(session.plan_path).read_text()))
    first = Task(
        "task-001", "Correct calculator", "Correct addition", ["calculator.py"], [],
        ["Addition returns sum"], ["git diff --check"], "low", "Implementer", PlanStatus.READY,
    )
    second = replace(first, task_id="task-002", title="Document addition", objective="Document sum",
                     allowed_paths=["README.md"], dependencies=[first.task_id])
    third_task = replace(second, task_id="task-003", allowed_paths=["notes.md"],
                         dependencies=[first.task_id, second.task_id])
    tasks = [first, replace(second, dependencies=[])] if top_level else [first, second]
    if third:
        tasks.append(third_task)
    edges = [{"task_id": second.task_id, "depends_on": first.task_id}] if top_level else []
    original = replace(original, tasks=tasks, dependencies=edges, parallel_groups=[],
                       architecture_impact={"status": "approved", "requires_architect": False})
    original.validate()
    path, file_hash = manager.store.write_artifact(
        session.session_id, "dependency-plan.json", json.dumps(original.to_dict()) + "\n",
    )
    session.plan_path = path
    session.artifact_hashes["plan"] = file_hash
    manager.store.save(session)
    item = replace(
        intent(root, base, candidate, remote), project_id=session.project_id,
        session_id=session.session_id, plan_id=original.plan_id,
        plan_hash=digest(original.to_dict()), task_hash=digest(asdict(first)),
    )
    item = replace(item, content_sha256=digest(item._payload()))
    intents = DeliveryIntentStore(state)
    intents.put(item)
    if integrated:
        git(root, "merge", "--ff-only", "agf/task-001")
        git(root, "push", "origin", "main")
        session = manager.resume(session.session_id)
    plan = plan_from_dict(json.loads(Path(session.plan_path).read_text()))
    return root, state, manager, session, plan, item


def test_integrated_predecessor_allows_real_isolated_successor_execution(tmp_path, monkeypatch):
    root, state, manager, session, plan, _ = prepared(tmp_path, monkeypatch)
    restarted = SessionManager(state)
    loaded = restarted.get(session.session_id)
    assert loaded.plan_path == session.plan_path
    proof = verify_task_dependencies(session.session_id, plan, plan.tasks[1], str(root))
    assert len(proof) == 1 and "dependency task-001" in proof[0]
    binary = fake_codex(root, body="printf 'addition documentation\\n' > README.md")
    result = Executor(CodexAdapter(str(binary), profile=CodexInvocationProfile())).execute(
        plan, "task-002", str(root), dry_run=False, session_id=session.session_id,
    )
    assert result.status is ExecutionStatus.COMPLETED
    assert any("dependency task-001" in item for item in result.evidence)
    assert not (root / "README.md").exists()


def test_unmerged_predecessor_does_not_allow_successor(tmp_path, monkeypatch):
    root, _, _, session, plan, _ = prepared(tmp_path, monkeypatch, integrated=False)
    result = Executor().execute(plan, "task-002", str(root), session_id=session.session_id)
    assert result.status is ExecutionStatus.BLOCKED
    assert "dependency" in result.blocking_issues[0]


@pytest.mark.parametrize("tamper", ["receipt", "plan", "intent", "session", "definition"])
def test_dependency_tampering_and_cross_session_reuse_fail_closed(tmp_path, monkeypatch, tamper):
    root, state, manager, session, plan, item = prepared(tmp_path, monkeypatch)
    session_id = session.session_id
    if tamper == "receipt":
        path = DeliveryIntentStore(state).receipt_path(session.project_id, item.delivery_id)
        payload = json.loads(path.read_text())
        payload["observed_sha"] = "f" * 40
        path.write_text(json.dumps(payload))
    elif tamper == "plan":
        Path(session.plan_path).write_text("{}")
    elif tamper == "intent":
        path = state / "delivery-intents" / session.project_id / (item.delivery_id + ".json")
        payload = json.loads(path.read_text())
        payload["review_evidence"]["status"] = "REQUEST_CHANGES"
        path.write_text(json.dumps(payload))
    elif tamper == "session":
        session_id = manager.start("alpha", "Different mission").session_id
    else:
        plan = replace(plan, tasks=[replace(plan.tasks[0], acceptance_criteria=["different"]),
                                    plan.tasks[1]])
    with pytest.raises(DependencyEvidenceError):
        verify_task_dependencies(session_id, plan, plan.tasks[1], str(root))


def test_unreconciled_target_advance_or_revert_blocks_dependencies(tmp_path, monkeypatch):
    root, _, _, session, plan, _ = prepared(tmp_path, monkeypatch)
    git(root, "revert", "--no-edit", "HEAD")
    with pytest.raises(DependencyEvidenceError):
        verify_task_dependencies(session.session_id, plan, plan.tasks[1], str(root))


def test_all_predecessors_are_required_and_survive_multiple_reconciliations(tmp_path, monkeypatch):
    root, state, manager, session, plan, first_intent = prepared(tmp_path, monkeypatch, third=True)
    with pytest.raises(DependencyEvidenceError):
        verify_task_dependencies(session.session_id, plan, plan.tasks[2], str(root))
    base = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-b", "agf/task-002")
    (root / "README.md").write_text("Addition documentation\n")
    git(root, "add", "README.md")
    git(root, "commit", "-m", "document addition")
    candidate = git(root, "rev-parse", "HEAD")
    git(root, "push", "-u", "origin", "agf/task-002")
    git(root, "checkout", "main")
    second_intent = replace(
        intent(root, base, candidate, first_intent.repository_identity),
        delivery_id="delivery-test-002", project_id=session.project_id,
        session_id=session.session_id, plan_id=plan.plan_id, plan_hash=digest(plan.to_dict()),
        task_id="task-002", task_hash=digest(asdict(plan.tasks[1])),
        allowed_paths=("README.md",), changed_files=("README.md",),
        delivery_branch="agf/task-002",
    )
    second_intent = replace(second_intent, content_sha256=digest(second_intent._payload()))
    DeliveryIntentStore(state).put(second_intent)
    git(root, "merge", "--ff-only", "agf/task-002")
    git(root, "push", "origin", "main")
    updated = SessionManager(state).resume(session.session_id)
    current = plan_from_dict(json.loads(Path(updated.plan_path).read_text()))
    proofs = verify_task_dependencies(session.session_id, current, current.tasks[2], str(root))
    assert len(proofs) == 2
    assert "task-001" in proofs[0] and "task-002" in proofs[1]


def test_top_level_dependency_edges_are_enforced(tmp_path, monkeypatch):
    root, _, _, session, plan, _ = prepared(tmp_path, monkeypatch, top_level=True)
    assert not plan.tasks[1].dependencies
    assert verify_task_dependencies(session.session_id, plan, plan.tasks[1], str(root))
    assert Executor().execute(plan, "task-002", str(root)).status is ExecutionStatus.BLOCKED


def test_changed_predecessor_dependency_graph_invalidates_historical_proof(tmp_path, monkeypatch):
    root, _, manager, session, plan, _ = prepared(tmp_path, monkeypatch, third=True)
    # Alter a prerequisite in the canonical successor plan, retaining a valid DAG.
    third = replace(plan.tasks[2], dependencies=[])
    changed = replace(plan, tasks=[plan.tasks[0], plan.tasks[1], third],
                      dependencies=[{"task_id": "task-001", "depends_on": "task-003"}])
    changed.validate()
    path, file_hash = manager.store.write_artifact(
        session.session_id, "changed-graph.json", json.dumps(changed.to_dict()) + "\n",
    )
    session.plan_path = path
    session.artifact_hashes["plan"] = file_hash
    manager.store.save(session)
    with pytest.raises(DependencyEvidenceError):
        verify_task_dependencies(session.session_id, changed, changed.tasks[1], str(root))
