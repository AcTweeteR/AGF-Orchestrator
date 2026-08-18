import pytest

from agf_orchestrator.scope_authorization import (
    ScopeAuthorization,
    ScopeAuthorizationError,
    ScopeAuthorizationStore,
    verify_scope_authorization,
)

PROJECT = "project-387b2ba08dc7021a"
SESSION = "session-a610d1e887d0c9ac8d7e"
BASELINE = "9b7049905a0b80e5aee6d4820dcdec82e4850ae6"
SCOPE = "phase-10-5c-r3"


def item(monkeypatch, **changes):
    monkeypatch.setattr(
        "agf_orchestrator.scope_authorization.verify_envelope", lambda *_: None
    )
    payload = {
        "schema_version": "1.0",
        "authorization_id": "scope-1234567890abcdef1234567890abcdef",
        "project_id": PROJECT,
        "session_id": SESSION,
        "repository_identity": "https://github.com/AcTweeteR/ai-virtual-fund.git",
        "baseline_sha": BASELINE,
        "scope_id": SCOPE,
        "decision": "AUTHORIZED_AND_REQUIRED",
        "boundaries": ("no-real-trading", "read-only-ibkr"),
        "operation_id": "owner-scope-r3-20260818",
        "issued_at": "2026-08-18T10:00:00Z",
    }
    payload.update(changes)
    signed = {**payload}
    record = {**payload, "owner_payload": signed, "owner_envelope": {"valid": True}}
    import hashlib
    import json
    evidence = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return ScopeAuthorization(**record, evidence_hash=evidence)


def test_valid_scope_authorization_is_persisted_idempotently(monkeypatch, tmp_path):
    value = item(monkeypatch)
    store = ScopeAuthorizationStore(tmp_path)
    assert store.put(value) == value.evidence_hash
    assert store.put(value) == value.evidence_hash
    assert store.get(PROJECT, value.authorization_id) == value


@pytest.mark.parametrize(
    "changes",
    [
        {"decision": "AUTHORIZED"},
    ],
)
def test_invalid_or_expanded_scope_fails(monkeypatch, changes):
    with pytest.raises(ScopeAuthorizationError):
        item(monkeypatch, **changes).validate()


def test_wrong_project_scope_fails_at_consumption(monkeypatch, tmp_path):
    value = item(monkeypatch)
    class Project:
        project_id = "project-aaaaaaaaaaaaaaaa"
        origin_url = "https://github.com/AcTweeteR/ai-virtual-fund.git"
    with pytest.raises(ScopeAuthorizationError):
        verify_scope_authorization(value, Project(), tmp_path, target_sha=BASELINE, scope_id=SCOPE)


def test_scope_and_boundary_expansion_fail_at_consumption(monkeypatch, tmp_path):
    class Project:
        project_id = PROJECT
        origin_url = "https://github.com/AcTweeteR/ai-virtual-fund.git"
    monkeypatch.setattr(
        "agf_orchestrator.scope_authorization.subprocess.run", lambda *a, **k: None
    )
    with pytest.raises(ScopeAuthorizationError):
        verify_scope_authorization(
            item(monkeypatch, scope_id="phase-10-5d-r4"), Project(), tmp_path,
            target_sha=BASELINE, scope_id=SCOPE,
        )
    with pytest.raises(ScopeAuthorizationError):
        verify_scope_authorization(
            item(monkeypatch, boundaries=("no-real-trading", "enable-orders")),
            Project(), tmp_path, target_sha=BASELINE, scope_id=SCOPE,
            allowed_boundaries=("no-real-trading",),
        )


def test_signature_failure_fails_closed(monkeypatch):
    value = item(monkeypatch)
    monkeypatch.setattr(
        "agf_orchestrator.scope_authorization.verify_envelope",
        lambda *_: (_ for _ in ()).throw(
            __import__("agf_orchestrator.owner_authority", fromlist=["OwnerAuthorityError"])
            .OwnerAuthorityError("bad signature")
        ),
    )
    with pytest.raises(ScopeAuthorizationError, match="signature is invalid"):
        value.validate()


def test_derived_target_with_same_scope_remains_valid(monkeypatch, tmp_path):
    value = item(monkeypatch)
    class Project:
        project_id = PROJECT
        origin_url = "https://github.com/AcTweeteR/ai-virtual-fund.git"
    monkeypatch.setattr(
        "agf_orchestrator.scope_authorization.subprocess.run", lambda *a, **k: None
    )
    verify_scope_authorization(
        value, Project(), tmp_path, target_sha="a" * 40, scope_id=SCOPE, session_id=SESSION
    )


def test_architecture_binding_is_distinct_from_scope_authorization():
    from agf_orchestrator.target_assessment import ArchitectureDecision
    assert "scope_authorization_id" in ArchitectureDecision.__dataclass_fields__


def test_architecture_decision_consumes_scope_without_replacing_it(monkeypatch):
    from agf_orchestrator.target_assessment import ArchitectureDecision
    decision = ArchitectureDecision(
        "1.0", "BLOCKED", True, "bounded", "R3", PROJECT, BASELINE, "a" * 64,
        "agf/r3", (), (), (), (), (), (), {"mode": "test"},
        scope_authorization_id="scope-1234567890abcdef1234567890abcdef",
        scope_id=SCOPE,
    )
    value = item(monkeypatch)
    monkeypatch.setattr(
        "agf_orchestrator.scope_authorization.verify_scope_authorization", lambda *a, **k: None
    )
    decision.consume_scope_authorization(value, object(), "/tmp", session_id=SESSION)
