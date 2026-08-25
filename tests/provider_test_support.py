"""Owner-envelope fixture support for provider eligibility tests."""

from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_PRIVATE_KEY = Ed25519PrivateKey.generate()
_PUBLIC_KEY = _PRIVATE_KEY.public_key().public_bytes_raw()
_FINGERPRINT = hashlib.sha256(_PUBLIC_KEY).hexdigest()
_KEY_ID = "test-owner-ed25519"


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sign_state(state):
    payload = state._unsigned()
    signature = _PRIVATE_KEY.sign(canonical_bytes(payload))
    envelope = {
        "signature_scheme": "Ed25519",
        "signature_version": "1",
        "key_id": _KEY_ID,
        "public_key_fingerprint": _FINGERPRINT,
        "payload_hash": hashlib.sha256(canonical_bytes(payload)).hexdigest(),
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    return state.__class__(**{**state.__dict__, "signature": envelope})


def verify_envelope(payload: object, envelope: dict[str, object]) -> None:
    if set(envelope) != {
        "signature_scheme",
        "signature_version",
        "key_id",
        "public_key_fingerprint",
        "payload_hash",
        "signature",
    }:
        raise ValueError("test owner envelope schema is invalid")
    if envelope["signature_scheme"] != "Ed25519" or envelope["signature_version"] != "1":
        raise ValueError("test owner envelope scheme is invalid")
    if envelope["key_id"] != _KEY_ID or envelope["public_key_fingerprint"] != _FINGERPRINT:
        raise ValueError("test owner envelope identity is invalid")
    payload_bytes = canonical_bytes(payload)
    if envelope["payload_hash"] != hashlib.sha256(payload_bytes).hexdigest():
        raise ValueError("test owner envelope payload differs")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    Ed25519PublicKey.from_public_bytes(_PUBLIC_KEY).verify(
        base64.b64decode(envelope["signature"], validate=True), payload_bytes
    )


def canonical_test_authority(store):
    """Construct production wiring against an explicitly configured test root."""
    from agf_orchestrator.provider_eligibility import ProviderEligibilityAuthority

    previous = os.environ.get("AGF_STATE_DIR")
    os.environ["AGF_STATE_DIR"] = str(store.root)
    try:
        return ProviderEligibilityAuthority(store)
    finally:
        if previous is None:
            os.environ.pop("AGF_STATE_DIR", None)
        else:
            os.environ["AGF_STATE_DIR"] = previous
