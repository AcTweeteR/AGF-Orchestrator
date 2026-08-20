import hashlib
import json

import pytest

from agf_orchestrator.external_advancement import (
    ExternalAdvancement,
    ExternalAdvancementError,
    ExternalAdvancementStore,
    ExternalResultAcceptance,
    ExternalResultAcceptanceStore,
    verify_external_advancement,
)


def result_item(**overrides):
    payload = {
        "schema_version": "1.0",
        "acceptance_id": "accept-external-result-001",
        "project_id": "project-0123456789abcdef",
        "session_id": "session-0123456789abcdef",
        "repository_identity": "https://github.com/example/project.git",
        "branch": "main",
        "previous_sha": "a" * 40,
        "target_sha": "b" * 40,
        "github": {"state": "MERGED", "merge_commit": "b" * 40, "pr_number": 89},
        "owner_payload": {
            "decision": "ACCEPT_EXTERNAL_RESULT",
            "acceptance_id": "accept-external-result-001",
            "project_id": "project-0123456789abcdef",
            "previous_sha": "a" * 40,
            "target_sha": "b" * 40,
            "github": {"state": "MERGED", "merge_commit": "b" * 40, "pr_number": 89},
        },
        "owner_envelope": {"signed": True},
        "observed_at": "2026-08-16T00:00:00Z",
        "provenance": "EXTERNAL_RESULT_ACCEPTANCE",
    }
    payload.update(overrides)
    payload["evidence_hash"] = hashlib.sha256(
        json.dumps({k: v for k, v in payload.items() if k != "evidence_hash"},
                   sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ExternalResultAcceptance(**payload)


def item(**overrides):
    payload = {
        "schema_version": "1.0",
        "advancement_id": "external-merge-001",
        "project_id": "project-0123456789abcdef",
        "session_id": "session-0123456789abcdef",
        "repository_identity": "https://github.com/example/project.git",
        "branch": "main",
        "previous_sha": "a" * 40,
        "target_sha": "b" * 40,
        "github": {
            "state": "MERGED",
            "merge_commit": "b" * 40,
            "base_branch": "main",
            "pr_number": 74,
        },
        "owner_payload": {
            "operation_id": "external-merge-001",
            "project_id": "project-0123456789abcdef",
            "target_sha": "b" * 40,
            "previous_sha": "a" * 40,
            "repository_identity": "https://github.com/example/project.git",
            "branch": "main",
            "github": {
                "state": "MERGED",
                "merge_commit": "b" * 40,
                "base_branch": "main",
                "pr_number": 74,
            },
            "decision": "AUTHORIZE_EXTERNAL_ADVANCE",
        },
        "owner_envelope": {"signed": True},
        "observed_at": "2026-08-16T00:00:00Z",
        "provenance": "EXTERNAL_OWNER_AUTHORIZED_ADVANCE",
    }
    payload.update(overrides)
    payload["evidence_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ExternalAdvancement(**payload)


def test_external_advance_requires_owner_signature(monkeypatch):
    with pytest.raises(ExternalAdvancementError, match="Owner authorization"):
        item().validate()


def test_external_result_store_is_idempotent_and_hash_consistent(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agf_orchestrator.external_advancement.verify_envelope", lambda payload, envelope: None
    )
    store = ExternalResultAcceptanceStore(tmp_path)
    first = result_item()
    assert store.put(first) == first.evidence_hash
    assert store.put(first) == first.evidence_hash
    assert store.get(first.project_id, first.acceptance_id) == first


def test_external_result_store_rejects_conflicting_replay(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agf_orchestrator.external_advancement.verify_envelope", lambda payload, envelope: None
    )
    store = ExternalResultAcceptanceStore(tmp_path)
    first = result_item()
    store.put(first)
    conflicting = result_item(observed_at="2026-08-17T00:00:00Z")
    conflicting = ExternalResultAcceptance(
        **{**conflicting.__dict__, "evidence_hash": hashlib.sha256(
            json.dumps(conflicting.unsigned(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()}
    )
    with pytest.raises(ExternalAdvancementError, match="replay conflicts"):
        store.put(conflicting)


def test_external_result_store_does_not_leave_partial_file_on_replace_failure(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "agf_orchestrator.external_advancement.verify_envelope", lambda payload, envelope: None
    )
    store = ExternalResultAcceptanceStore(tmp_path)
    first = result_item()
    monkeypatch.setattr(
        "agf_orchestrator.external_advancement.os.replace",
        lambda *_args: (_ for _ in ()).throw(OSError("simulated crash before commit")),
    )
    with pytest.raises(OSError, match="simulated crash"):
        store.put(first)
    directory = tmp_path / "external-result-acceptances" / first.project_id
    assert not list(directory.glob(f"{first.acceptance_id}.json"))
    temporary_files = [
        path for path in directory.iterdir()
        if path.name.startswith(f".{first.acceptance_id}.json.")
        and not path.name.endswith(".lock")
    ]
    assert not temporary_files


def test_external_advance_store_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agf_orchestrator.external_advancement.verify_envelope", lambda payload, envelope: None
    )
    store = ExternalAdvancementStore(tmp_path)
    first = item()
    assert store.put(first) == first.evidence_hash
    assert store.get(first.project_id, first.advancement_id) == first
    assert store.put(first) == first.evidence_hash


def test_external_advance_rejects_tampered_target(monkeypatch):
    monkeypatch.setattr(
        "agf_orchestrator.external_advancement.verify_envelope", lambda payload, envelope: None
    )
    tampered = item(target_sha="c" * 40)
    with pytest.raises(ExternalAdvancementError, match="GitHub merge target"):
        tampered.validate()


def test_external_advance_rejects_unbound_owner_operation(monkeypatch):
    monkeypatch.setattr(
        "agf_orchestrator.external_advancement.verify_envelope", lambda payload, envelope: None
    )
    unbound = item()
    unbound.owner_payload["operation_id"] = "different-operation"
    with pytest.raises(ExternalAdvancementError, match="operation mismatch"):
        unbound.validate()


def test_external_advance_rejects_branch_binding_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agf_orchestrator.external_advancement.verify_envelope", lambda payload, envelope: None
    )
    monkeypatch.setattr(
        "agf_orchestrator.external_advancement._git",
        lambda *_args: "develop",
    )
    project = type(
        "Project",
        (),
        {
            "project_id": "project-0123456789abcdef",
            "default_branch": "develop",
            "origin_url": "https://github.com/example/project.git",
        },
    )()
    with pytest.raises(ExternalAdvancementError, match="branch binding mismatch"):
        verify_external_advancement(item(), project, tmp_path)


def test_external_advance_rejects_unsigned_merge_binding_mismatch(monkeypatch):
    monkeypatch.setattr(
        "agf_orchestrator.external_advancement.verify_envelope", lambda payload, envelope: None
    )
    unbound = item()
    unbound.owner_payload["github"]["merge_commit"] = "c" * 40
    with pytest.raises(ExternalAdvancementError, match="merge evidence mismatch"):
        unbound.validate()


def test_external_advance_rejects_wrong_owner_decision(monkeypatch):
    monkeypatch.setattr(
        "agf_orchestrator.external_advancement.verify_envelope", lambda payload, envelope: None
    )
    unbound = item()
    unbound.owner_payload["decision"] = "AUTHORIZE_OTHER_OPERATION"
    with pytest.raises(ExternalAdvancementError, match="decision mismatch"):
        unbound.validate()
