import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agf_orchestrator import historical_evidence
from tools import owner_ed25519_authority as controller

PROJECT = "project-0123456789abcdef"
TARGET_SHA = "a" * 40


def _repo(tmp_path):
    root = tmp_path / "target"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True
    )
    (root / "README.md").write_text("target\n")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "initial"], check=True, capture_output=True
    )
    return root


def _setup(monkeypatch, tmp_path):
    state = tmp_path / ".agf-orchestrator"
    root = _repo(tmp_path)
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    project = SimpleNamespace(
        status=SimpleNamespace(value="ACTIVE"),
        repository_root=str(root),
        current_head_sha=head,
        origin_url="file:///tmp/target.git",
    )
    authority = SimpleNamespace(
        constitution=SimpleNamespace(constitution_id="constitution-v1", record_hash="c" * 64),
        policy=SimpleNamespace(policy_id="merge-policy-adr-0003", policy_hash="a" * 64),
        snapshot={"generation": 1},
    )
    monkeypatch.setenv("AGF_STATE_DIR", str(state))
    monkeypatch.setattr(controller.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(
        controller,
        "ProjectRegistry",
        lambda _state_dir=None: SimpleNamespace(verify_read_only=lambda _project: project),
    )
    monkeypatch.setattr(controller, "resolve_authority", lambda _project: authority)
    monkeypatch.setattr(controller, "_generation_root", lambda: tmp_path / "owner-root")
    monkeypatch.setattr(
        controller,
        "sign_envelope",
        lambda payload, _root: {"payload_hash": controller._object_hash(payload)},
    )
    def fake_load(_project, state_root=None):
        document = json.loads(
            (Path(state_root) / "historical-evidence" / PROJECT / "baseline.json").read_text()
        )
        payload = document["payload"]
        return SimpleNamespace(
            baseline_id=payload["baseline_id"],
            operation_id=payload["operation_id"],
            target_sha=payload["target_sha"],
            target_identity=payload["target_identity"],
            coverage_start=payload["coverage_start"],
            baseline_generation=payload["baseline_generation"],
        )

    monkeypatch.setattr(controller, "load_historical_baseline", fake_load)
    return state, root, head


def test_external_baseline_preserves_unknown_and_binds_target(monkeypatch, tmp_path):
    state, _root, head = _setup(monkeypatch, tmp_path)
    result = controller.create_prospective_baseline(
        PROJECT, "historical-baseline-test-00000000", target_sha=head
    )
    assert result["status"] == "COMMITTED"
    payload = json.loads(
        (state / "historical-evidence" / PROJECT / "baseline.json").read_text()
    )["payload"]
    assert payload["target_sha"] == head
    assert payload["target_identity"] == "file:///tmp/target.git"
    assert "pre-baseline=UNKNOWN" in payload["provenance"]
    assert "count" not in payload
    journal = json.loads(
        (state / "historical-evidence" / PROJECT / "baseline-journal.json").read_text()
    )["payload"]
    assert journal["status"] == "COMMITTED"
    assert journal["operation_id"] == "historical-baseline-test-00000000"
    activation = json.loads(
        (state / "historical-evidence" / PROJECT / "baseline-activation.json").read_text()
    )["payload"]
    assert activation["status"] == "COMMITTED"
    assert activation["source_hashes"] == payload["source_hashes"]


def test_external_baseline_rejects_wrong_target_without_writing(monkeypatch, tmp_path):
    state, _root, head = _setup(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="target SHA"):
        controller.create_prospective_baseline(
            PROJECT, "historical-baseline-test-00000000", target_sha="b" * 40
        )
    assert not (state / "historical-evidence" / PROJECT).exists()


def test_external_baseline_rejects_invalid_operation_identity(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="identity"):
        controller.create_prospective_baseline(PROJECT, "operation-1")


def test_external_baseline_rejects_incomplete_replay_journal(monkeypatch, tmp_path):
    state, _root, head = _setup(monkeypatch, tmp_path)
    directory = state / "historical-evidence" / PROJECT
    directory.mkdir(parents=True)
    (directory / "baseline-journal.json").write_text(json.dumps({
        "payload": {"status": "PREPARED", "operation_id": "historical-baseline-test-00000000"}
    }))
    with pytest.raises(RuntimeError, match="incomplete"):
        controller.create_prospective_baseline(
            PROJECT, "historical-baseline-test-00000000", target_sha=head
        )


def test_external_baseline_is_idempotent_after_commit(monkeypatch, tmp_path):
    state, _root, head = _setup(monkeypatch, tmp_path)
    first = controller.create_prospective_baseline(
        PROJECT, "historical-baseline-test-00000000", target_sha=head
    )
    baseline = state / "historical-evidence" / PROJECT / "baseline.json"
    payload = json.loads(baseline.read_text())["payload"]
    monkeypatch.setattr(
        controller,
        "load_historical_baseline",
        lambda *_args, **_kwargs: SimpleNamespace(
            baseline_id=payload["baseline_id"],
            operation_id=payload["operation_id"],
                target_sha=head,
                target_identity="file:///tmp/target.git",
                coverage_start=payload["coverage_start"],
                baseline_generation=1,
        ),
    )
    second = controller.create_prospective_baseline(
        PROJECT, "historical-baseline-test-00000000", target_sha=head
    )
    assert second["status"] == "ALREADY_COMMITTED"
    assert second["baseline_id"] == first["baseline_id"]


def test_conflicting_ledger_is_rejected_before_prepared_journal(monkeypatch, tmp_path):
    state, _root, head = _setup(monkeypatch, tmp_path)
    ledger = state / "historical-evidence" / PROJECT / "ledgers" / "rollback-ledger.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("{}")
    with pytest.raises(RuntimeError, match="ledger"):
        controller.create_prospective_baseline(
            PROJECT, "historical-baseline-test-00000000", target_sha=head
        )
    assert not (state / "historical-evidence" / PROJECT / "baseline-journal.json").exists()


def test_failure_after_prepare_never_exposes_committed_baseline(monkeypatch, tmp_path):
    state, _root, head = _setup(monkeypatch, tmp_path)
    original_write = controller._atomic_write
    writes = 0

    def fail_at_baseline(path, value):
        nonlocal writes
        writes += 1
        if path.name == "baseline.json":
            raise OSError("simulated crash")
        original_write(path, value)

    monkeypatch.setattr(controller, "_atomic_write", fail_at_baseline)
    with pytest.raises(OSError, match="simulated crash"):
        controller.create_prospective_baseline(
            PROJECT, "historical-baseline-test-00000000", target_sha=head
        )
    journal = json.loads(
        (state / "historical-evidence" / PROJECT / "baseline-journal.json").read_text()
    )["payload"]
    assert writes >= 3
    assert journal["status"] == "PREPARED"
    assert not (state / "historical-evidence" / PROJECT / "baseline.json").exists()


def test_runtime_verifier_has_no_owner_signing_or_mutation_import():
    source = Path("src/agf_orchestrator/historical_evidence.py").read_text()
    assert "sign_envelope" not in source
    assert "owner_ed25519_authority" not in source
    assert "_private_key" not in source


def test_verifier_rejects_tampered_source_and_target_drift(monkeypatch, tmp_path):
    state, _root, head = _setup(monkeypatch, tmp_path)
    controller.create_prospective_baseline(
        PROJECT, "historical-baseline-test-00000000", target_sha=head
    )
    authority = SimpleNamespace(
        constitution=SimpleNamespace(constitution_id="constitution-v1", record_hash="c" * 64),
        policy=SimpleNamespace(policy_id="merge-policy-adr-0003", policy_hash="a" * 64),
        snapshot={"generation": 1},
    )
    project = SimpleNamespace(origin_url="file:///tmp/target.git", current_head_sha=head)
    monkeypatch.setattr(historical_evidence, "verify_envelope", lambda *_args: None)
    monkeypatch.setattr(historical_evidence, "resolve_authority", lambda _project: authority)
    monkeypatch.setattr(
        historical_evidence,
        "ProjectRegistry",
        lambda: SimpleNamespace(get=lambda _project: project),
    )
    ledger = state / "historical-evidence" / PROJECT / "ledgers" / "rollback-ledger.json"
    ledger.write_text(ledger.read_text().replace("UNKNOWN", "UNKNOWN "))
    with pytest.raises(historical_evidence.HistoricalEvidenceError, match="invalid"):
        historical_evidence.load_historical_baseline(PROJECT, state_root=state)


def test_verifier_rejects_target_drift(monkeypatch, tmp_path):
    state, _root, head = _setup(monkeypatch, tmp_path)
    controller.create_prospective_baseline(
        PROJECT, "historical-baseline-test-00000000", target_sha=head
    )
    authority = SimpleNamespace(
        constitution=SimpleNamespace(constitution_id="constitution-v1", record_hash="c" * 64),
        policy=SimpleNamespace(policy_id="merge-policy-adr-0003", policy_hash="a" * 64),
        snapshot={"generation": 1},
    )
    project = SimpleNamespace(origin_url="file:///tmp/target.git", current_head_sha="b" * 40)
    monkeypatch.setattr(historical_evidence, "verify_envelope", lambda *_args: None)
    monkeypatch.setattr(historical_evidence, "resolve_authority", lambda _project: authority)
    monkeypatch.setattr(
        historical_evidence,
        "ProjectRegistry",
        lambda: SimpleNamespace(get=lambda _project: project),
    )
    with pytest.raises(historical_evidence.HistoricalEvidenceError, match="stale"):
        historical_evidence.load_historical_baseline(PROJECT, state_root=state)
