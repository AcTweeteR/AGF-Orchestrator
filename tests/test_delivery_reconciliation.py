import hashlib
import json
import subprocess

import pytest

from agf_orchestrator.delivery_reconciliation import (
    DeliveryIntent,
    DeliveryIntentStore,
    DeliveryReconciliationError,
)


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def fixture(tmp_path):
    root = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    for key, value in (("user.name", "AGF"), ("user.email", "agf@example.invalid")):
        git(root, "config", key, value)
    (root / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
    git(root, "add", "calculator.py")
    git(root, "commit", "-m", "baseline")
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-u", "origin", "main")
    base = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-b", "agf/task-001")
    (root / "calculator.py").write_text("def add(a, b):\n    return a + b\n")
    git(root, "commit", "-am", "AGF: bounded correction")
    candidate = git(root, "rev-parse", "HEAD")
    git(root, "push", "-u", "origin", "agf/task-001")
    git(root, "checkout", "main")
    return root, base, candidate, str(remote)


def intent(root, base, candidate, remote):
    diff = subprocess.run(
        ["git", "-C", str(root), "diff", f"{base}..{candidate}"],
        check=True, capture_output=True,
    ).stdout
    payload = {
        "schema_version": "1.0", "delivery_id": "delivery-test-001",
        "project_id": "project-0123456789abcdef", "session_id": "session-test-001",
        "plan_id": "plan-test", "plan_hash": "a" * 64, "task_id": "task-001",
        "task_hash": "f" * 64,
        "repository_identity": remote, "base_sha": base, "candidate_sha": candidate,
        "candidate_tree_sha": git(root, "rev-parse", f"{candidate}^{{tree}}"),
        "delivery_branch": "agf/task-001", "target_branch": "main",
        "allowed_paths": ("calculator.py",),
        "changed_files": ("calculator.py",), "diff_sha256": hashlib.sha256(diff).hexdigest(),
        "review_sha256": "b" * 64, "compliance_sha256": "c" * 64,
        "authorization_sha256": "d" * 64, "policy_hash": "e" * 64,
        "review_evidence": {"status": "APPROVE"},
        "compliance_evidence": {"status": "PASS"},
        "authorization_evidence": {"authorization_status": "AUTHORIZED"},
        "constitution_id": "constitution-v1", "authority_generation": 1,
        "evidence_generation": 1, "created_at": "2026-08-14T09:00:00Z",
        "state": "EXTERNAL_ACTION_REQUIRED",
    }
    payload["review_sha256"] = hashlib.sha256(
        json.dumps(payload["review_evidence"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    payload["compliance_sha256"] = hashlib.sha256(
        json.dumps(payload["compliance_evidence"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    payload["authorization_sha256"] = hashlib.sha256(
        json.dumps(
            payload["authorization_evidence"], sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return DeliveryIntent(
        **payload,
        content_sha256=hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    )


def test_exact_external_merge_reconciles_and_is_idempotent(tmp_path):
    root, base, candidate, remote = fixture(tmp_path)
    store = DeliveryIntentStore(tmp_path / "state")
    store.put(intent(root, base, candidate, remote))
    git(root, "merge", "--ff-only", "agf/task-001")
    git(root, "push", "origin", "main")
    receipt = store.observe("project-0123456789abcdef", "delivery-test-001", root)
    assert receipt.observed_sha == candidate
    assert store.observe("project-0123456789abcdef", "delivery-test-001", root) == receipt


def test_unrelated_target_drift_is_rejected(tmp_path):
    root, base, candidate, remote = fixture(tmp_path)
    store = DeliveryIntentStore(tmp_path / "state")
    store.put(intent(root, base, candidate, remote))
    (root / "README.md").write_text("unrelated\n")
    git(root, "add", "README.md")
    git(root, "commit", "-m", "unrelated")
    with pytest.raises(DeliveryReconciliationError, match="remote target ref"):
        store.observe("project-0123456789abcdef", "delivery-test-001", root)


def test_tampered_intent_and_missing_intent_fail_closed(tmp_path):
    root, base, candidate, remote = fixture(tmp_path)
    store = DeliveryIntentStore(tmp_path / "state")
    with pytest.raises(DeliveryReconciliationError, match="missing"):
        store.observe("project-0123456789abcdef", "delivery-test-001", root)
    stored = intent(root, base, candidate, remote)
    store.put(stored)
    path = (
        tmp_path / "state" / "delivery-intents" / "project-0123456789abcdef"
        / "delivery-test-001.json"
    )
    payload = json.loads(path.read_text())
    payload["candidate_sha"] = "f" * 40
    path.write_text(json.dumps(payload))
    with pytest.raises(DeliveryReconciliationError, match="hash"):
        store.get("project-0123456789abcdef", "delivery-test-001")
