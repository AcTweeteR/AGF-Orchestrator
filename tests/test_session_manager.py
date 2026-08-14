import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agf_orchestrator.architect_planning import ProviderArchitect
from agf_orchestrator.capability_profiles import capability_profile_hash
from agf_orchestrator.capability_selection import CapabilityCandidate, SelectionGates
from agf_orchestrator.constitution import ConstitutionVerificationError
from agf_orchestrator.locking import LockError, project_lock
from agf_orchestrator.policy_authority import PolicyActivationError
from agf_orchestrator.project_models import ProjectStatus
from agf_orchestrator.project_registry import ProjectRegistry, ProjectRegistryError
from agf_orchestrator.session_manager import (
    SessionManager,
    SessionManagerError,
    _canonical_plan_hash,
)
from agf_orchestrator.session_models import SessionStatus
from tests.test_architect_planning import FakeProvider, profile


def registered(tmp_path):
    root, origin = tmp_path / "repo", tmp_path / "origin.git"
    tmp_path.mkdir(parents=True, exist_ok=True)
    root.mkdir()
    origin.mkdir()
    subprocess.run(["git", "init", "-b", "feature", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    for key, value in (("user.name", "Test"), ("user.email", "test@example.invalid")):
        subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
    (root / "x").write_text("x")
    subprocess.run(["git", "-C", str(root), "add", "x"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "init"], check=True, capture_output=True
    )
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", origin.as_uri()], check=True)
    state = tmp_path / "state"
    registry = ProjectRegistry(state)
    registry.add("alpha", root)
    return root, state


def test_delivery_intent_plan_hash_ignores_artifact_formatting():
    payload = {"plan_id": "plan-test", "tasks": [{"task_id": "task-001"}]}
    formatted = json.dumps(payload, indent=2, sort_keys=True) + "\n"

    assert hashlib.sha256(formatted.encode()).hexdigest() != _canonical_plan_hash(payload)
    assert _canonical_plan_hash(json.loads(formatted)) == _canonical_plan_hash(payload)


def test_start_is_ready_and_resume_is_idempotent(tmp_path):
    root, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Add contributor link validation")
    assert session.status is SessionStatus.READY
    again = manager.resume(session.session_id)
    assert again.session_id == session.session_id
    assert len(again.events) == 1
    subprocess.run(["git", "-C", str(root), "add", "x"], check=True)
    (root / "x").write_text("drift")
    subprocess.run(
        ["git", "-C", str(root), "commit", "-am", "drift"], check=True, capture_output=True
    )
    stale = manager.resume(session.session_id)
    assert stale.status is SessionStatus.STALE
    assert "base SHA" in stale.blocking_issues[0]


def test_repair_reconciled_lineage_requires_exact_receipt_binding(tmp_path, monkeypatch):
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Validate contributor links")
    project = ProjectRegistry(state).get("alpha")
    predecessor = Path(session.plan_path)
    predecessor_hash = manager.store.artifact_hash(str(predecessor))
    predecessor_payload = json.loads(predecessor.read_text())
    task = {
        "task_id": "task-001",
        "title": "Validate contributor links",
        "objective": "Validate contributor links",
        "allowed_paths": ["x"],
        "dependencies": [],
        "acceptance_criteria": ["x is present"],
        "validation_commands": ["python -m pytest"],
        "risk_level": "low",
        "assigned_role": "Implementer",
        "status": "READY",
        "requirement_refs": [],
    }
    predecessor_payload["tasks"] = [task]
    predecessor.write_text(json.dumps(predecessor_payload, indent=2, sort_keys=True) + "\n")
    predecessor_hash = manager.store.artifact_hash(str(predecessor))
    candidate_sha = project.current_head_sha
    receipt_sha = "r" * 64
    intent_hash = "i" * 64
    current_payload = json.loads(predecessor.read_text())
    current_payload["scope"] = {
        "lineage": str(predecessor),
        "predecessor_plan_sha256": predecessor_hash,
        "delivery_reconciliation": {
            "delivery_id": "delivery-001",
            "intent_hash": intent_hash,
            "receipt_hash": receipt_sha,
            "observed_sha": candidate_sha,
            "completed_task_id": "task-001",
        },
    }
    current_path, current_hash = manager.store.write_artifact(
        session.session_id,
        "plan-v2.json",
        json.dumps(current_payload, indent=2, sort_keys=True) + "\n",
    )
    intent = SimpleNamespace(
        delivery_id="delivery-001",
        base_sha=candidate_sha,
        candidate_sha=candidate_sha,
        plan_id=predecessor_payload["plan_id"],
        plan_hash=_canonical_plan_hash(predecessor_payload),
        task_id="task-001",
        task_hash=hashlib.sha256(
            json.dumps(task, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        content_sha256=intent_hash,
    )
    receipt = SimpleNamespace(observed_sha=candidate_sha, receipt_sha256=receipt_sha)

    class FakeDeliveryStore:
        def __init__(self, _state):
            pass

        def for_session(self, _project_id, _session_id):
            return [intent]

        def observe(self, _project_id, _delivery_id, _root):
            return receipt

        def receipt_path(self, _project_id, _delivery_id):
            return tmp_path / "receipt.json"

    monkeypatch.setattr("agf_orchestrator.session_manager.DeliveryIntentStore", FakeDeliveryStore)
    monkeypatch.setattr(manager, "_validate_plan_identity", lambda *_args: None)
    stale = replace(
        session,
        status=SessionStatus.STALE,
        current_stage=SessionStatus.STALE.value,
        plan_path=current_path,
        artifact_hashes={"plan": current_hash},
    )
    manager.store.save(stale)

    repaired = manager._repair_reconciled_lineage_binding(
        stale, project, Path(project.repository_root)
    )
    assert repaired.status is SessionStatus.READY
    assert repaired.artifact_hashes["predecessor_plan"] == predecessor_hash

    current_payload["scope"]["delivery_reconciliation"]["observed_sha"] = "f" * 40
    Path(current_path).write_text(json.dumps(current_payload, indent=2, sort_keys=True) + "\n")
    tampered = replace(
        repaired,
        plan_path=current_path,
        artifact_hashes={"plan": manager.store.artifact_hash(current_path)},
    )
    assert manager._repair_reconciled_lineage_binding(
        tampered, project, Path(project.repository_root)
    ) is None


def test_assess_placeholder_persists_evidence_and_blocks_unsupported_scope(tmp_path):
    root, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Identify a genuinely useful bounded improvement")
    assessed = manager.assess(session.session_id)
    assert assessed.status is SessionStatus.BLOCKED
    assert assessed.plan_path.endswith("plan-v2.json")
    assert assessed.artifact_hashes["original_plan"]
    assert (state / "artifacts" / session.session_id / "assessment.json").exists()
    assert (state / "artifacts" / session.session_id / "architecture.json").exists()
    assert Path(assessed.plan_path).exists()
    assert not list(root.glob("**/*.agf"))
    retry = manager.transition(session.session_id, SessionStatus.RETRY_REQUIRED)
    assert retry.status is SessionStatus.RETRY_REQUIRED
    restarted = SessionManager(state)
    assert restarted.resume(session.session_id).status is SessionStatus.RETRY_REQUIRED
    assert manager.assess(session.session_id).status is SessionStatus.BLOCKED


def test_changed_provider_authority_requires_fresh_assessment(tmp_path):
    root, state = registered(tmp_path)
    initial = SessionManager(state)
    session = initial.start("alpha", "Identify a genuinely useful bounded improvement")
    assert initial.assess(session.session_id).status is SessionStatus.BLOCKED

    project_id = ProjectRegistry(state).get("alpha").project_id
    provider_profile = replace(profile("provider-a"), project_id=project_id)
    provider_profile = replace(
        provider_profile, profile_sha256=capability_profile_hash(provider_profile)
    )
    candidates = (CapabilityCandidate(provider_profile, 0),)
    gates = SelectionGates(
        policy_eligible=True, privacy_eligible=True, independence_eligible=True,
        budget_eligible=True, health_eligible=True, empirical_evidence_eligible=True,
    )
    architect = ProviderArchitect(
        candidates, {"provider-a": FakeProvider()}, now="2026-08-10T12:00:00Z",
        project_id=project_id, gates=gates,
    )
    refreshed = SessionManager(
        state, architect=architect, architect_candidates=candidates,
        architect_providers={"provider-a": FakeProvider()}, architect_gates=gates,
    ).assess(session.session_id)

    assert refreshed.status is SessionStatus.RETRY_REQUIRED
    assert "provider evidence is stale" in refreshed.blocking_issues[0]


def test_recovery_requires_fresh_evaluation_and_preserves_versioned_lineage(tmp_path):
    root, state = registered(tmp_path)
    (root / "README.md").write_text("# Test\n")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "add readme"],
        check=True, capture_output=True,
    )
    project_id = ProjectRegistry(state).get("alpha").project_id
    provider_profile = replace(profile("provider-a"), project_id=project_id)
    provider_profile = replace(
        provider_profile, profile_sha256=capability_profile_hash(provider_profile)
    )
    candidates = (CapabilityCandidate(provider_profile, 0),)
    gates = SelectionGates(
        policy_eligible=True, privacy_eligible=True, independence_eligible=True,
        budget_eligible=True, health_eligible=True, empirical_evidence_eligible=True,
    )
    architect = ProviderArchitect(
        candidates, {"provider-a": FakeProvider()}, now="2026-08-10T12:00:00Z",
        project_id=project_id, gates=gates,
    )
    manager = SessionManager(
        state, architect=architect,
        architect_candidates=candidates, architect_providers={"provider-a": FakeProvider()},
        architect_gates=gates,
    )
    session = manager.start("alpha", "Improve file:README.md")
    assessed = manager.assess(session.session_id)
    assert assessed.status is SessionStatus.READY
    first_plan = Path(assessed.plan_path)
    first_files = {
        path.name for path in (state / "artifacts" / session.session_id).glob("*.json")
    }
    recovered = SessionManager(state, architect=architect, architect_candidates=candidates,
                               architect_providers={"provider-a": FakeProvider()},
                               architect_gates=gates).assess(session.session_id)
    assert recovered.status is SessionStatus.RETRY_REQUIRED
    retried = manager.assess(session.session_id)
    assert retried.status is SessionStatus.READY
    assert Path(retried.plan_path).name == "plan-v3.json"
    assert first_plan.exists()
    current_files = {
        path.name for path in (state / "artifacts" / session.session_id).glob("*.json")
    }
    assert first_files <= current_files
    plan_payload = json.loads(Path(retried.plan_path).read_text(encoding="utf-8"))
    assert Path(plan_payload["scope"]["lineage"]).resolve() == first_plan.resolve()
    restarted = SessionManager(
        state, architect=architect, architect_candidates=candidates,
        architect_providers={"provider-a": FakeProvider()}, architect_gates=gates,
    )
    assert restarted.resume(session.session_id).status is SessionStatus.RETRY_REQUIRED


def test_interrupted_assessment_preserves_partial_generation_and_uses_next_version(
    tmp_path, monkeypatch
):
    root, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Identify a genuinely useful bounded improvement")
    assert manager.assess(session.session_id).status is SessionStatus.BLOCKED
    manager.transition(session.session_id, SessionStatus.RETRY_REQUIRED)
    original_write = manager.store.write_artifact
    calls = 0

    def fail_after_first_generation(session_id, name, content):
        nonlocal calls
        calls += 1
        if name == "architecture-v3.json":
            raise RuntimeError("injected artifact failure")
        return original_write(session_id, name, content)

    monkeypatch.setattr(manager.store, "write_artifact", fail_after_first_generation)
    with pytest.raises(RuntimeError, match="injected artifact failure"):
        manager.assess(session.session_id)
    artifact_dir = state / "artifacts" / session.session_id
    assert (artifact_dir / "assessment-v3.json").exists()
    assert not (artifact_dir / "architecture-v3.json").exists()
    assert manager.get(session.session_id).plan_path.endswith("plan-v2.json")
    monkeypatch.setattr(manager.store, "write_artifact", original_write)
    retried = manager.assess(session.session_id)
    assert retried.plan_path.endswith("plan-v4.json")
    assert (artifact_dir / "assessment-v3.json").exists()
    assert "architect_response" not in retried.artifact_hashes


def test_invalid_transition_and_cancel_preserve_events(tmp_path):
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Add contributor link validation")
    try:
        manager.transition(session.session_id, SessionStatus.COMPLIANCE)
    except SessionManagerError:
        pass
    else:
        raise AssertionError("invalid transition accepted")
    cancelled = manager.cancel(session.session_id)
    assert cancelled.status is SessionStatus.CANCELLED
    assert len(cancelled.events) == 2


def test_missing_or_changed_artifact_marks_session_stale(tmp_path):
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    missing = manager.start("alpha", "Validate contributor links")
    import os

    os.unlink(missing.plan_path)
    assert manager.resume(missing.session_id).status is SessionStatus.STALE

    _, state2 = registered(tmp_path / "second")
    manager2 = SessionManager(state2)
    changed = manager2.start("alpha", "Validate contributor links")
    with open(changed.plan_path, "a", encoding="utf-8") as handle:
        handle.write("tampered")
    assert manager2.resume(changed.session_id).status is SessionStatus.STALE


def test_disabled_project_stales_session_and_terminal_cannot_resume(tmp_path):
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Validate contributor links")
    manager.registry.set_status("alpha", ProjectStatus.DISABLED)
    assert manager.resume(session.session_id).status is SessionStatus.STALE
    cancelled = manager.cancel(session.session_id)
    with pytest.raises(SessionManagerError):
        manager.resume(cancelled.session_id)


def test_invalid_resume_flags_and_project_lock_do_not_mutate_session(tmp_path):
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Validate contributor links")
    with pytest.raises(SessionManagerError, match="confirm-execution"):
        manager.resume(session.session_id, confirm_execution=True)
    assert len(manager.get(session.session_id).events) == 1
    project = manager.registry.get("alpha")
    with project_lock(state, project.project_id, "test-holder"):
        with pytest.raises(LockError):
            manager.resume(session.session_id)
    assert len(manager.get(session.session_id).events) == 1


def test_execution_checkpoint_requires_both_policy_permissions(tmp_path):
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    manager.registry.set_status("alpha", ProjectStatus.ACTIVE)
    project = manager.registry.get("alpha")
    from dataclasses import replace

    project = replace(project, policy=replace(project.policy, allow_live_execution=True))
    manager.registry._save([project])
    session = manager.start("alpha", "Validate contributor links")
    with pytest.raises(SessionManagerError, match="delivery"):
        manager.resume(
            session.session_id, execute=True, confirm_execution=True, confirm_delivery=True
        )
    assert manager.get(session.session_id).status is SessionStatus.READY
    from agf_orchestrator import session_manager as session_module

    class Authority:
        def resolve(self, project_id):
            return {"status": "VERIFIED"}

    original_authority = session_module.ConstitutionAuthority
    session_module.ConstitutionAuthority = Authority
    try:
        authorized = manager.resume(session.session_id, execute=True, confirm_execution=True)
    finally:
        session_module.ConstitutionAuthority = original_authority
    assert authorized.status is SessionStatus.EXECUTING
    assert authorized.execution_report_path is None


def test_project_removal_blocks_nonterminal_and_preserves_terminal_evidence(tmp_path):
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    active = manager.start("alpha", "Validate contributor links")
    with pytest.raises(ProjectRegistryError, match="active sessions"):
        manager.registry.remove("alpha")
    manager.cancel(active.session_id)
    manager.registry.remove("alpha")
    assert manager.store.load(active.session_id).status is SessionStatus.CANCELLED


def test_repeated_operation_id_adds_one_event_and_uncertain_save_is_human_required(tmp_path):
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Validate contributor links")
    first = manager.transition(
        session.session_id,
        SessionStatus.EXECUTING,
        operation_id="operation-1",
        summary="authorization checkpoint",
    )
    second = manager.transition(
        session.session_id,
        SessionStatus.EXECUTING,
        operation_id="operation-1",
        summary="duplicate retry",
    )
    assert len(second.events) == len(first.events)
    manager.store.save = lambda _: (_ for _ in ()).throw(OSError("simulated interrupted save"))
    with pytest.raises(SessionManagerError, match="HUMAN_REQUIRED"):
        manager.transition(
            session.session_id,
            SessionStatus.REVIEWING,
            operation_id="operation-2",
        )


def test_repair_lineage_replaces_proven_self_reference_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.ConstitutionAuthority.resolve",
        lambda self, project_id: object(),
    )
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.PolicyAuthority.resolve",
        lambda self, project_id: object(),
    )
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Identify a genuinely useful bounded improvement")
    assessed = manager.assess(session.session_id)
    plan_path = Path(assessed.plan_path)
    payload = json.loads(plan_path.read_text())
    preserved_scope = {
        key: payload["scope"].get(key)
        for key in ("assessment_hash", "architecture_hash", "delivery_branch", "in", "out")
    }
    payload["scope"]["lineage"] = str(plan_path)
    payload["scope"]["predecessor_plan_sha256"] = "0" * 64
    plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    corrupted = manager.store.load(session.session_id)
    corrupted.artifact_hashes["plan"] = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    corrupted.artifact_hashes["predecessor_plan"] = "0" * 64
    manager.store.save(corrupted)

    repaired = manager.repair_lineage(session.session_id)
    repaired_payload = json.loads(plan_path.read_text())
    predecessor = Path(repaired_payload["scope"]["lineage"])
    assert repaired.status is SessionStatus.BLOCKED
    assert predecessor.name == "plan.json"
    assert repaired_payload["scope"]["predecessor_plan_sha256"] == hashlib.sha256(
        predecessor.read_bytes()
    ).hexdigest()
    assert {
        key: repaired_payload["scope"].get(key) for key in preserved_scope
    } == preserved_scope
    assert (plan_path.parent / "plan-v2-invalid-lineage.json").exists()
    assert (plan_path.parent / "lineage-repair.json").exists()
    again = SessionManager(state).repair_lineage(session.session_id)
    assert again.events == repaired.events
    assert again.artifact_hashes["plan"] == hashlib.sha256(plan_path.read_bytes()).hexdigest()
    tampered_session = manager.store.load(session.session_id)
    tampered_session.artifact_hashes["plan"] = "f" * 64
    manager.store.save(tampered_session)
    with pytest.raises(SessionManagerError, match="session hash"):
        SessionManager(state).repair_lineage(session.session_id)


def test_repair_lineage_refuses_ambiguous_predecessor(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.ConstitutionAuthority.resolve",
        lambda self, project_id: object(),
    )
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.PolicyAuthority.resolve",
        lambda self, project_id: object(),
    )
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Identify a genuinely useful bounded improvement")
    assessed = manager.assess(session.session_id)
    plan_path = Path(assessed.plan_path)
    payload = json.loads(plan_path.read_text())
    payload["scope"]["lineage"] = str(plan_path)
    plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    predecessor = plan_path.parent / "plan-copy.json"
    predecessor.write_bytes((plan_path.parent / "plan.json").read_bytes())
    corrupted = manager.store.load(session.session_id)
    corrupted.artifact_hashes["plan"] = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    manager.store.save(corrupted)
    with pytest.raises(SessionManagerError, match="uniquely proven"):
        manager.repair_lineage(session.session_id)


def test_repaired_predecessor_tampering_fails_closed_on_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.ConstitutionAuthority.resolve",
        lambda self, project_id: object(),
    )
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.PolicyAuthority.resolve",
        lambda self, project_id: object(),
    )
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Identify a genuinely useful bounded improvement")
    assessed = manager.assess(session.session_id)
    plan_path = Path(assessed.plan_path)
    payload = json.loads(plan_path.read_text())
    payload["scope"]["lineage"] = str(plan_path)
    plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    corrupted = manager.store.load(session.session_id)
    corrupted.artifact_hashes["plan"] = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    manager.store.save(corrupted)
    manager.repair_lineage(session.session_id)
    predecessor = plan_path.parent / "plan.json"
    predecessor.write_text(predecessor.read_text() + "\n")
    stale = SessionManager(state).resume(session.session_id)
    assert stale.status is SessionStatus.STALE
    assert "predecessor" in stale.blocking_issues[0]


def test_lineage_repair_recovers_after_interrupted_session_save(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.ConstitutionAuthority.resolve",
        lambda self, project_id: object(),
    )
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.PolicyAuthority.resolve",
        lambda self, project_id: object(),
    )
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Identify a genuinely useful bounded improvement")
    assessed = manager.assess(session.session_id)
    plan_path = Path(assessed.plan_path)
    payload = json.loads(plan_path.read_text())
    payload["scope"]["lineage"] = str(plan_path)
    plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    corrupted = manager.store.load(session.session_id)
    corrupted.artifact_hashes["plan"] = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    manager.store.save(corrupted)
    original_save = manager._save
    calls = {"count": 0}

    def fail_once(value):
        calls["count"] += 1
        if calls["count"] == 1:
            raise SessionManagerError("simulated interrupted save")
        original_save(value)

    manager._save = fail_once
    with pytest.raises(SessionManagerError, match="interrupted"):
        manager.repair_lineage(session.session_id)
    recovered = SessionManager(state).repair_lineage(session.session_id)
    assert recovered.artifact_hashes["lineage_repair_backup"]
    assert recovered.status is SessionStatus.BLOCKED


def test_lineage_repair_rejects_tampered_audit_after_interruption(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.ConstitutionAuthority.resolve",
        lambda self, project_id: object(),
    )
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.PolicyAuthority.resolve",
        lambda self, project_id: object(),
    )
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Identify a genuinely useful bounded improvement")
    assessed = manager.assess(session.session_id)
    plan_path = Path(assessed.plan_path)
    payload = json.loads(plan_path.read_text())
    payload["scope"]["lineage"] = str(plan_path)
    plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    corrupted = manager.store.load(session.session_id)
    corrupted.artifact_hashes["plan"] = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    manager.store.save(corrupted)
    original_save = manager._save
    manager._save = lambda value: (_ for _ in ()).throw(SessionManagerError("interrupted"))
    with pytest.raises(SessionManagerError, match="interrupted"):
        manager.repair_lineage(session.session_id)
    interrupted = manager.store.load(session.session_id)
    interrupted.artifact_hashes["plan"] = "f" * 64
    manager.store.save(interrupted)
    with pytest.raises(SessionManagerError, match="session hash"):
        SessionManager(state).repair_lineage(session.session_id)
    interrupted.artifact_hashes["plan"] = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    manager.store.save(interrupted)
    audit = state / "artifacts" / session.session_id / "lineage-repair.json"
    audit.write_text(audit.read_text().replace("plan-v2", "plan-v9"))
    with pytest.raises(SessionManagerError, match="audit"):
        SessionManager(state).repair_lineage(session.session_id)
    manager._save = original_save


def test_lineage_repair_rejects_missing_backup_after_interruption(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.ConstitutionAuthority.resolve",
        lambda self, project_id: object(),
    )
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.PolicyAuthority.resolve",
        lambda self, project_id: object(),
    )
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Identify a genuinely useful bounded improvement")
    assessed = manager.assess(session.session_id)
    plan_path = Path(assessed.plan_path)
    payload = json.loads(plan_path.read_text())
    payload["scope"]["lineage"] = str(plan_path)
    plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    corrupted = manager.store.load(session.session_id)
    corrupted.artifact_hashes["plan"] = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    manager.store.save(corrupted)
    manager._save = lambda value: (_ for _ in ()).throw(SessionManagerError("interrupted"))
    with pytest.raises(SessionManagerError, match="interrupted"):
        manager.repair_lineage(session.session_id)
    (plan_path.parent / "plan-v2-invalid-lineage.json").unlink()
    with pytest.raises(SessionManagerError, match="backup"):
        SessionManager(state).repair_lineage(session.session_id)


def test_lineage_repair_requires_authority_before_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.ConstitutionAuthority.resolve",
        lambda self, project_id: (_ for _ in ()).throw(ConstitutionVerificationError("denied")),
    )
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Identify a genuinely useful bounded improvement")
    manager.assess(session.session_id)
    with pytest.raises(SessionManagerError, match="authority"):
        manager.repair_lineage(session.session_id)


def test_lineage_repair_rejects_audit_symlink_before_read(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.ConstitutionAuthority.resolve",
        lambda self, project_id: object(),
    )
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.PolicyAuthority.resolve",
        lambda self, project_id: object(),
    )
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Identify a genuinely useful bounded improvement")
    assessed = manager.assess(session.session_id)
    plan_path = Path(assessed.plan_path)
    payload = json.loads(plan_path.read_text())
    payload["scope"]["lineage"] = str(plan_path)
    plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    corrupted = manager.store.load(session.session_id)
    corrupted.artifact_hashes["plan"] = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    manager.store.save(corrupted)
    audit = plan_path.parent / "lineage-repair.json"
    audit.symlink_to(plan_path)
    with pytest.raises(SessionManagerError, match="audit"):
        manager.repair_lineage(session.session_id)


def test_lineage_repair_rejects_current_plan_branch_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.ConstitutionAuthority.resolve",
        lambda self, project_id: object(),
    )
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.PolicyAuthority.resolve",
        lambda self, project_id: object(),
    )
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Identify a genuinely useful bounded improvement")
    assessed = manager.assess(session.session_id)
    plan_path = Path(assessed.plan_path)
    payload = json.loads(plan_path.read_text())
    payload["repository"]["branch"] = "unregistered-branch"
    payload["scope"]["lineage"] = str(plan_path)
    plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    corrupted = manager.store.load(session.session_id)
    corrupted.artifact_hashes["plan"] = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    manager.store.save(corrupted)
    with pytest.raises(SessionManagerError, match="binding"):
        manager.repair_lineage(session.session_id)


def test_lineage_repair_rejects_predecessor_cycle(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.ConstitutionAuthority.resolve",
        lambda self, project_id: object(),
    )
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.PolicyAuthority.resolve",
        lambda self, project_id: object(),
    )
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Identify a genuinely useful bounded improvement")
    assessed = manager.assess(session.session_id)
    directory = Path(assessed.plan_path).parent
    predecessor = directory / "plan.json"
    predecessor_payload = json.loads(predecessor.read_text())
    predecessor_payload["scope"]["lineage"] = str(predecessor)
    predecessor_payload["scope"]["predecessor_plan_sha256"] = "x" * 64
    predecessor.write_text(json.dumps(predecessor_payload, indent=2, sort_keys=True) + "\n")
    plan_path = Path(assessed.plan_path)
    plan_payload = json.loads(plan_path.read_text())
    plan_payload["scope"]["lineage"] = str(plan_path)
    plan_path.write_text(json.dumps(plan_payload, indent=2, sort_keys=True) + "\n")
    corrupted = manager.store.load(session.session_id)
    predecessor_hash = hashlib.sha256(predecessor.read_bytes()).hexdigest()
    corrupted.artifact_hashes.update({
        "original_plan": predecessor_hash,
        "predecessor_plan": predecessor_hash,
        "plan": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
    })
    manager.store.save(corrupted)
    with pytest.raises(SessionManagerError, match="lineage"):
        manager.repair_lineage(session.session_id)


def test_lineage_repair_requires_active_policy_independently(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.ConstitutionAuthority.resolve",
        lambda self, project_id: object(),
    )
    monkeypatch.setattr(
        "agf_orchestrator.session_manager.PolicyAuthority.resolve",
        lambda self, project_id: (_ for _ in ()).throw(PolicyActivationError("policy denied")),
    )
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Identify a genuinely useful bounded improvement")
    manager.assess(session.session_id)
    with pytest.raises(SessionManagerError, match="authority"):
        manager.repair_lineage(session.session_id)
