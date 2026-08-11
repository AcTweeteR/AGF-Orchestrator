import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import agf_orchestrator.owner_authority as owner_authority
from agf_orchestrator.owner_authority import (
    OwnerAuthorityError,
    canonical_bytes,
    fingerprint,
    verify_envelope,
)


def signed_payload(tmp_path, payload=None):
    payload = payload or {"project_id": "project-test", "generation": 1}
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw()
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    (root / "owner-public.key").write_text(base64.b64encode(public).decode() + "\n")
    (root / "owner-public.key").chmod(0o644)
    (root / "anchor.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "signature_scheme": "Ed25519",
                "key_id": "owner-ed25519-1",
                "fingerprint": fingerprint(public),
            }
        )
        + "\n"
    )
    signature = private.sign(canonical_bytes(payload))
    envelope = {
        "signature_scheme": "Ed25519",
        "signature_version": "1",
        "key_id": "owner-ed25519-1",
        "public_key_fingerprint": fingerprint(public),
        "payload_hash": __import__("hashlib").sha256(canonical_bytes(payload)).hexdigest(),
        "signature": base64.b64encode(signature).decode(),
    }
    return root, payload, envelope


def test_runtime_verifies_with_public_material_only(tmp_path, monkeypatch):
    root, payload, envelope = signed_payload(tmp_path)
    monkeypatch.setattr(owner_authority, "DEFAULT_ROOT", root)
    monkeypatch.setattr(
        owner_authority, "PINNED_OWNER_FINGERPRINT", envelope["public_key_fingerprint"]
    )
    verify_envelope(payload, envelope)


def test_wrong_key_and_payload_fail_closed(tmp_path, monkeypatch):
    root, payload, envelope = signed_payload(tmp_path)
    monkeypatch.setattr(owner_authority, "DEFAULT_ROOT", root)
    monkeypatch.setattr(
        owner_authority, "PINNED_OWNER_FINGERPRINT", envelope["public_key_fingerprint"]
    )
    altered = dict(payload, generation=2)
    with pytest.raises(OwnerAuthorityError):
        verify_envelope(altered, envelope)
    envelope["key_id"] = "other"
    with pytest.raises(OwnerAuthorityError):
        verify_envelope(payload, envelope)


def test_public_key_substitution_fails(tmp_path, monkeypatch):
    root, payload, envelope = signed_payload(tmp_path)
    monkeypatch.setattr(owner_authority, "DEFAULT_ROOT", root)
    monkeypatch.setattr(
        owner_authority, "PINNED_OWNER_FINGERPRINT", envelope["public_key_fingerprint"]
    )
    private = Ed25519PrivateKey.generate()
    root.joinpath("owner-public.key").write_text(
        base64.b64encode(private.public_key().public_bytes_raw()).decode()
    )
    with pytest.raises(OwnerAuthorityError):
        verify_envelope(payload, envelope)
