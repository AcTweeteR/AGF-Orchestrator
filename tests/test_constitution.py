import json
from pathlib import Path

import pytest

from agf_orchestrator.constitution import (
    ConstitutionVerificationError,
    ConstitutionVerifier,
    canonical_hash,
    canonical_json,
)

PROJECT_ID = "project-0123456789abcdef"


def _write_state(
    root: Path, *, project_id: str = PROJECT_ID, constitution_id: str = "constitution-v1"
):
    directory = root / "projects" / project_id / "constitution"
    directory.mkdir(parents=True)
    record = {
        "schema_version": "1.0",
        "constitution_id": constitution_id,
        "version": "1.0",
        "project_id": project_id,
        "compatibility": "agf-constitution-v1",
        "body": {"owner": "external-owner", "rules": ["fail-closed"]},
        "key_id": "owner-key-1",
        "signature": "valid-signature",
    }
    pointer = {
        "schema_version": "1.0",
        "project_id": project_id,
        "constitution_id": constitution_id,
        "record_hash": canonical_hash(record),
    }
    (directory / f"{constitution_id}.json").write_bytes(canonical_json(record))
    (directory / "active.json").write_bytes(canonical_json(pointer))
    return record, pointer


def _signature_verifier(payload: bytes, signature: str, key_id: str) -> bool:
    return bool(payload and signature == "valid-signature" and key_id == "owner-key-1")


def test_valid_state_returns_bounded_evidence_and_is_idempotent(tmp_path):
    _write_state(tmp_path)
    verifier = ConstitutionVerifier(tmp_path, PROJECT_ID, signature_verifier=_signature_verifier)

    first = verifier.verify()
    second = verifier.verify()

    assert first == second
    assert first.to_dict() == {
        "project_id": PROJECT_ID,
        "constitution_id": "constitution-v1",
        "version": "1.0",
        "record_hash": first.record_hash,
        "key_id": "owner-key-1",
        "compatibility": "agf-constitution-v1",
        "status": "VERIFIED",
    }


def test_missing_pointer_fails_closed(tmp_path):
    with pytest.raises(ConstitutionVerificationError, match="unreadable active pointer"):
        ConstitutionVerifier(tmp_path, PROJECT_ID, signature_verifier=_signature_verifier).verify()


def test_missing_signature_verifier_fails_closed(tmp_path):
    _write_state(tmp_path)
    with pytest.raises(ConstitutionVerificationError, match="signature verifier is unavailable"):
        ConstitutionVerifier(tmp_path, PROJECT_ID).verify()


@pytest.mark.parametrize("field", ["signature", "key_id", "compatibility", "project_id"])
def test_tampered_record_fails_closed(tmp_path, field):
    _write_state(tmp_path)
    path = tmp_path / "projects" / PROJECT_ID / "constitution" / "constitution-v1.json"
    record = json.loads(path.read_text())
    record[field] = "tampered"
    path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(
        ConstitutionVerificationError,
        match=(
            "(record hash mismatch|constitution compatibility mismatch|"
            "constitution project identity mismatch)"
        ),
    ):
        ConstitutionVerifier(tmp_path, PROJECT_ID, signature_verifier=_signature_verifier).verify()


def test_pointer_for_another_project_cannot_cross_project_boundary(tmp_path):
    _write_state(tmp_path, project_id="project-fedcba9876543210")
    with pytest.raises(ConstitutionVerificationError, match="unreadable active pointer"):
        ConstitutionVerifier(tmp_path, PROJECT_ID, signature_verifier=_signature_verifier).verify()


def test_project_identity_is_validated_before_path_use(tmp_path):
    with pytest.raises(ConstitutionVerificationError, match="invalid project identity"):
        ConstitutionVerifier(tmp_path, "../other", signature_verifier=_signature_verifier)


def test_unknown_schema_and_extra_fields_are_rejected(tmp_path):
    _write_state(tmp_path)
    path = tmp_path / "projects" / PROJECT_ID / "constitution" / "active.json"
    pointer = json.loads(path.read_text())
    pointer["unexpected"] = True
    path.write_text(json.dumps(pointer), encoding="utf-8")

    with pytest.raises(ConstitutionVerificationError, match="invalid active pointer schema"):
        ConstitutionVerifier(tmp_path, PROJECT_ID, signature_verifier=_signature_verifier).verify()


def test_constitution_identity_cannot_escape_namespaced_directory(tmp_path):
    _write_state(tmp_path)
    path = tmp_path / "projects" / PROJECT_ID / "constitution" / "active.json"
    pointer = json.loads(path.read_text())
    pointer["constitution_id"] = "../outside"
    path.write_text(json.dumps(pointer), encoding="utf-8")

    with pytest.raises(ConstitutionVerificationError, match="invalid constitution identity"):
        ConstitutionVerifier(tmp_path, PROJECT_ID, signature_verifier=_signature_verifier).verify()


def test_pointer_change_during_verification_is_rejected(tmp_path):
    _, pointer = _write_state(tmp_path)
    verifier = None

    def mutate_pointer(payload, signature, key_id):
        nonlocal verifier
        verifier.pointer_path.write_text(json.dumps({**pointer, "record_hash": "changed"}))
        return True

    verifier = ConstitutionVerifier(tmp_path, PROJECT_ID, signature_verifier=mutate_pointer)
    with pytest.raises(ConstitutionVerificationError, match="changed during verification"):
        verifier.verify()


def test_canonical_json_rejects_nan():
    with pytest.raises(ConstitutionVerificationError, match="non-canonical data"):
        canonical_json({"value": float("nan")})
