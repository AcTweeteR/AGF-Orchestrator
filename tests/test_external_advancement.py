import hashlib
import json

import pytest

from agf_orchestrator.external_advancement import (
    ExternalAdvancement,
    ExternalAdvancementError,
    ExternalAdvancementStore,
)


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
