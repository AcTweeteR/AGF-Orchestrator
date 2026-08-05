import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest

from agf_orchestrator.constitution import (
    ConstitutionAuthority,
    ConstitutionVerificationError,
    canonical_hash,
    canonical_json,
)

PROJECT_ID = "project-0123456789abcdef"
KEY = b"owner-key-material-that-is-at-least-32-bytes-long"


def _write_state(root: Path, *, project_id: str = PROJECT_ID):
    authority_dir = root / ".agf-orchestrator" / "constitution-authority"
    constitution_dir = root / ".agf-orchestrator" / "projects" / project_id / "constitution"
    authority_dir.mkdir(parents=True)
    constitution_dir.mkdir(parents=True)
    (authority_dir / "owner.key").write_text(base64.b64encode(KEY).decode("ascii"))
    unsigned = {
        "schema_version": "1.0",
        "constitution_id": "constitution-v1",
        "version": "1.0",
        "project_id": project_id,
        "compatibility": "agf-constitution-v1",
        "approval_status": "APPROVED",
        "body": {"owner": "external-owner", "rules": ["fail-closed"]},
        "key_id": "owner-key-1",
    }
    record = {
        **unsigned,
        "signature": hmac.new(KEY, canonical_json(unsigned), hashlib.sha256).hexdigest(),
    }
    pointer = {
        "schema_version": "1.0",
        "project_id": project_id,
        "constitution_id": "constitution-v1",
        "record_hash": canonical_hash(record),
    }
    (constitution_dir / "constitution-v1.json").write_bytes(canonical_json(record))
    (constitution_dir / "active.json").write_bytes(canonical_json(pointer))
    return record, pointer


def _authority(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return ConstitutionAuthority()


def test_authority_resolves_immutable_state_and_is_idempotent(tmp_path, monkeypatch):
    _write_state(tmp_path)
    authority = _authority(tmp_path, monkeypatch)

    first = authority.resolve(PROJECT_ID)
    second = authority.resolve(PROJECT_ID)

    assert first == second
    assert first.evidence()["status"] == "VERIFIED"
    with pytest.raises(TypeError):
        first.body["new"] = "blocked"


def test_authority_has_fixed_source_and_no_injectable_provider():
    assert ConstitutionAuthority.__init__.__code__.co_argcount == 1
    assert "signature_verifier" not in ConstitutionAuthority.__init__.__annotations__


def test_missing_pointer_fails_closed(tmp_path, monkeypatch):
    with pytest.raises(ConstitutionVerificationError, match="unreadable active pointer"):
        _authority(tmp_path, monkeypatch).resolve(PROJECT_ID)


def test_missing_owner_key_fails_closed(tmp_path, monkeypatch):
    _write_state(tmp_path)
    (tmp_path / ".agf-orchestrator" / "constitution-authority" / "owner.key").unlink()

    with pytest.raises(ConstitutionVerificationError, match="unreadable owner key"):
        _authority(tmp_path, monkeypatch).resolve(PROJECT_ID)


@pytest.mark.parametrize("field", ["signature", "key_id", "approval_status", "body"])
def test_tampered_record_fails_closed(tmp_path, monkeypatch, field):
    _write_state(tmp_path)
    path = (
        tmp_path
        / ".agf-orchestrator"
        / "projects"
        / PROJECT_ID
        / "constitution"
        / "constitution-v1.json"
    )
    record = json.loads(path.read_text())
    record[field] = "tampered"
    path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(ConstitutionVerificationError):
        _authority(tmp_path, monkeypatch).resolve(PROJECT_ID)


def test_pointer_for_another_project_cannot_cross_project_boundary(tmp_path, monkeypatch):
    _write_state(tmp_path, project_id="project-fedcba9876543210")

    with pytest.raises(ConstitutionVerificationError, match="unreadable active pointer"):
        _authority(tmp_path, monkeypatch).resolve(PROJECT_ID)


def test_project_identity_cannot_escape_namespaced_directory(tmp_path, monkeypatch):
    _write_state(tmp_path)

    with pytest.raises(ConstitutionVerificationError, match="invalid project identity"):
        _authority(tmp_path, monkeypatch).resolve("../other")


def test_constitution_identity_cannot_escape_namespaced_directory(tmp_path, monkeypatch):
    _write_state(tmp_path)
    path = (
        tmp_path
        / ".agf-orchestrator"
        / "projects"
        / PROJECT_ID
        / "constitution"
        / "active.json"
    )
    pointer = json.loads(path.read_text())
    pointer["constitution_id"] = "../outside"
    path.write_text(json.dumps(pointer), encoding="utf-8")

    with pytest.raises(ConstitutionVerificationError, match="invalid constitution identity"):
        _authority(tmp_path, monkeypatch).resolve(PROJECT_ID)


def test_unapproved_state_fails_closed(tmp_path, monkeypatch):
    _write_state(tmp_path)
    path = (
        tmp_path
        / ".agf-orchestrator"
        / "projects"
        / PROJECT_ID
        / "constitution"
        / "constitution-v1.json"
    )
    record = json.loads(path.read_text())
    record["approval_status"] = "CANDIDATE"
    path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(ConstitutionVerificationError, match="approval is not APPROVED"):
        _authority(tmp_path, monkeypatch).resolve(PROJECT_ID)


def test_pointer_change_during_resolution_is_rejected(tmp_path, monkeypatch):
    _, pointer = _write_state(tmp_path)
    authority = _authority(tmp_path, monkeypatch)
    original = authority._read_json
    calls = 0

    def mutate(path, label):
        nonlocal calls
        result = original(path, label)
        calls += 1
        if calls == 2:
            (authority._project_constitution_dir(PROJECT_ID) / "active.json").write_text(
                json.dumps({**pointer, "record_hash": "changed"})
            )
        return result

    monkeypatch.setattr(authority, "_read_json", mutate)
    with pytest.raises(ConstitutionVerificationError, match="changed during verification"):
        authority.resolve(PROJECT_ID)


def test_canonical_json_rejects_nan():
    with pytest.raises(ConstitutionVerificationError, match="non-canonical data"):
        canonical_json({"value": float("nan")})
