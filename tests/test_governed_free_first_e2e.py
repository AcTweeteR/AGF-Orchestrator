"""End-to-end, owner-signed QA against disposable Git and mocked free inference.

The historical ledger is SYNTHETIC TEST DATA. It is never owner production
evidence. Every byte is confined to the pytest-created temporary directory.
"""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from test_delivery_reconciliation import git
from test_objective_plan import approved_first_plan

from agf_orchestrator import owner_authority
from agf_orchestrator.adapters.free_first import FreeFirstGatewayAdapter
from agf_orchestrator.authority_context import resolve_authority
from agf_orchestrator.delivery import DeliveryPipeline
from agf_orchestrator.git_delivery import DraftPRCreator
from agf_orchestrator.historical_evidence import (
    load_historical_baseline,
    load_historical_evidence,
)
from agf_orchestrator.model_gateway import GatewayResult
from agf_orchestrator.project_registry import ProjectRegistry
from agf_orchestrator.reviewer import DeterministicReviewer
from agf_orchestrator.session_continuation import SessionContinuation

_PROJECT_OWNER_REVIEW = "Synthetic owner authorization (pytest fixture only)"


def _hash(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _signed(path, payload, sign):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"payload": payload, "envelope": sign(payload)}) + "\n")


def _signed_project(tmp_path, monkeypatch):
    """Create a real Ed25519 test owner and bind an exact first Objective plan."""
    import test_objective_plan as objective_fixture

    original_install = objective_fixture.install_fixture_generation
    captured = {}

    def install_with_test_policy(*args, **kwargs):
        root, sign = original_install(
            *args, **kwargs,
            freshness_limits={"policy_seconds": 86400, "gate_seconds": 3600},
        )
        captured["sign"] = sign
        return root, sign

    monkeypatch.setattr(objective_fixture, "install_fixture_generation", install_with_test_policy)
    root, state, manager, session, _, projected, _ = approved_first_plan(
        tmp_path, monkeypatch, persist_intent=False,
    )
    registry = ProjectRegistry(state)
    project = registry.get(session.project_id)
    registry._save([
        replace(project, policy=replace(
            project.policy, allow_delivery=True, allow_live_execution=True,
        ))
    ])
    project = registry.get(session.project_id)
    authority = resolve_authority(project.project_id)
    bound = manager.bind_objective_plan(session.session_id)
    assert bound.status.value == "READY"
    assert manager.get(session.session_id).artifact_hashes["objective_binding"]
    return root, manager, session, project, authority, captured["sign"]


def _install_synthetic_signed_history(tmp_path, project, authority, sign):
    """Build a complete signed fixture, never a claim about actual incidents."""
    namespace = (
        Path.home() / ".agf-orchestrator" / "historical-evidence" / project.project_id
    )
    namespace.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).replace(microsecond=0)
    start = (timestamp - timedelta(hours=2)).isoformat()
    end = timestamp.isoformat()
    baseline_id = "baseline-test-synthetic-only-20261104"
    generation = int(authority.snapshot["generation"])
    ledger = {
        "project_id": project.project_id,
        "baseline_id": baseline_id,
        "coverage_before_baseline": "UNKNOWN",
        "coverage_status_from_baseline": "AUTHORITATIVE",
        "fixture_only": True,
        "synthetic_counts": {"incident": 0, "rollback": 0},
    }
    ledger_path = namespace / "ledger" / "synthetic.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(ledger) + "\n")
    ledger_hash = _hash(ledger)
    source_ref = f"ledger:{project.project_id}:ledger/synthetic.json"
    baseline = {
        "schema_version": "1.0",
        "baseline_id": baseline_id,
        "project_id": project.project_id,
        "coverage_start": start,
        "policy_hash": authority.policy.policy_hash,
        "constitution_id": authority.constitution.constitution_id,
        "authority_generation": generation,
        "generated_at": end,
        "provenance": _PROJECT_OWNER_REVIEW,
        "target_identity": project.origin_url,
        "target_sha": project.current_head_sha,
        "policy_id": authority.policy.policy_id,
        "policy_generation": generation,
        "constitution_record_hash": authority.constitution.record_hash,
        "owner_fingerprint": owner_authority.PINNED_OWNER_FINGERPRINT,
        "evidence_definition_version": "synthetic-qa/1",
        "source_set": [source_ref],
        "source_hashes": [ledger_hash],
        "baseline_generation": 1,
        "predecessor_baseline_hash": None,
        "operation_id": "historical-baseline-synthetic-qa-20261104",
    }
    _signed(namespace / "baseline.json", baseline, sign)
    journal = {
        "status": "COMMITTED",
        "operation_id": baseline["operation_id"],
        "project_id": project.project_id,
        "baseline_id": baseline_id,
        "target_sha": project.current_head_sha,
        "source_hashes": [ledger_hash],
        "baseline_hash": _hash(baseline),
        "baseline_generation": 1,
    }
    _signed(namespace / "baseline-journal.json", journal, sign)
    _signed(namespace / "baseline-activation.json", {
        "status": "COMMITTED",
        "project_id": project.project_id,
        "baseline_id": baseline_id,
        "baseline_hash": _hash(baseline),
        "journal_hash": _hash(journal),
        "source_hashes": [ledger_hash],
    }, sign)

    evidence_hashes = {}
    for kind in ("rollback", "incident"):
        evidence = {
            "schema_version": "1.0",
            "project_id": project.project_id,
            "evidence_type": kind,
            "status": "VERIFIED_ZERO",
            "count": 0,
            "baseline_id": baseline_id,
            "coverage_before_baseline": "UNKNOWN",
            "evidence_generation": 1,
            "predecessor_evidence_hash": None,
            "renewal_operation_id": "historical-renewal-synthetic-qa-20261104",
            "coverage_start": start,
            "coverage_end": end,
            "definition_version": "synthetic-qa/1",
            "source_refs": [source_ref],
            "source_hashes": [ledger_hash],
            "policy_hash": authority.policy.policy_hash,
            "constitution_id": authority.constitution.constitution_id,
            "authority_generation": generation,
            "generated_at": end,
            "provenance": _PROJECT_OWNER_REVIEW,
            "coverage_complete": True,
            "completeness_basis": "owner-completeness-v1:synthetic isolated QA only",
        }
        evidence["evidence_hash"] = _hash(evidence)
        evidence_hashes[kind] = evidence["evidence_hash"]
        _signed(namespace / f"{kind}.json", evidence, sign)

    _signed(namespace / "evidence-activation.json", {
        "status": "COMMITTED",
        "project_id": project.project_id,
        "baseline_id": baseline_id,
        "operation_id": "historical-renewal-synthetic-qa-20261104",
        "evidence_generation": 1,
        "evidence_hashes": evidence_hashes,
        "source_hashes": {"rollback": ledger_hash, "incident": ledger_hash},
    }, sign)
    assert load_historical_baseline(project.project_id).authoritative
    assert load_historical_evidence(project.project_id, "rollback") is not None
    assert load_historical_evidence(project.project_id, "incident") is not None


def _runner(tmp_path, manager):
    return SessionContinuation(manager, DeliveryPipeline(
        adapter=FreeFirstGatewayAdapter(timeout=60),
        reviewer=DeterministicReviewer(),
        pr_creator=DraftPRCreator(simulate=True),
        artifact_dir=tmp_path / "artifacts",
        max_correction_rounds=1,
    ))


def _model():
    return GatewayResult(
        model="ollama/qwen3.5:9b-q4_K_M",
        provider="ollama",
        content=json.dumps({
            "changes": [{
                "file": "calculator.py",
                "find": "return a - b",
                "replace": "return a + b",
            }],
        }),
        prompt_tokens=100,
        completion_tokens=50,
    )


_FLAGS = dict(execute=True, confirm_execution=True, confirm_delivery=True)


def test_governed_first_plan_delivery_reconcile_and_completion(tmp_path, monkeypatch):
    root, manager, session, project, authority, sign = _signed_project(
        tmp_path, monkeypatch
    )
    _install_synthetic_signed_history(tmp_path, project, authority, sign)
    original_main = git(root, "rev-parse", "HEAD")
    driver = _runner(tmp_path, manager)

    with patch(
        "agf_orchestrator.adapters.free_first.ModelGatewayProvider.complete",
        return_value=_model(),
    ) as llm:
        implementation = driver.tick(session.session_id, **_FLAGS)
    llm.assert_called_once()
    assert implementation["status"] == "CONTINUE", implementation
    assert implementation["action"] == "delivery"
    delivery = json.loads(Path(implementation["evidence_path"]).read_text())
    assert delivery["status"] == "COMPLETED", delivery.get("blocking_issues")
    assert delivery["review_status"] == "APPROVE"
    assert delivery["compliance_status"] == "PASS"
    assert delivery["push_status"] == "PUSHED"
    assert delivery["commit_sha"]
    assert delivery["pr_url"].startswith("local://draft-pr/")
    assert git(root, "rev-parse", "HEAD") == original_main
    assert git(root, "status", "--porcelain") == ""

    waiting = driver.tick(session.session_id, **_FLAGS)
    assert waiting["status"] == "WAIT", waiting
    assert waiting["action"] == "external-integration"
    assert git(root, "rev-parse", "HEAD") == original_main

    # A simulated external owner integrates a previously reviewed branch.
    git(root, "merge", "--ff-only", delivery["delivery_branch"])
    git(root, "push", "origin", "main")
    resumed = driver.tick(session.session_id, **_FLAGS)
    assert resumed["action"] == "reconcile" and resumed["status"] == "CONTINUE", resumed
    completed = driver.tick(session.session_id, **_FLAGS)
    assert completed["status"] == "SUCCESS", completed
    assert completed["objective_completed"] is True
    assert manager.get(session.session_id).status.value == "COMPLETED"
    assert git(root, "status", "--porcelain") == ""


def test_missing_signed_history_never_publishes_or_completes(tmp_path, monkeypatch):
    root, manager, session, _, _, _ = _signed_project(tmp_path, monkeypatch)
    driver = _runner(tmp_path, manager)
    original_main = git(root, "rev-parse", "HEAD")

    with patch(
        "agf_orchestrator.adapters.free_first.ModelGatewayProvider.complete",
        return_value=_model(),
    ):
        step = driver.tick(session.session_id, **_FLAGS)

    assert step["action"] == "delivery"
    record = json.loads(Path(step["evidence_path"]).read_text())
    assert record["status"] == "BLOCKED"
    assert record["push_status"] == "NOT_REQUESTED"
    assert record["commit_sha"] is None
    assert "risk evidence snapshot binding is stale" in record["blocking_issues"]
    assert git(root, "rev-parse", "HEAD") == original_main
    assert git(root, "status", "--porcelain") == ""
    blocked = driver.tick(session.session_id, **_FLAGS)
    assert blocked["status"] == "HUMAN_REQUIRED"
    assert manager.get(session.session_id).status.value != "COMPLETED"
