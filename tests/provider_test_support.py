"""Owner-envelope fixture support for provider eligibility tests."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from contextlib import contextmanager

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


def _sign_binding_subject_payload(subject: dict[str, object]) -> dict[str, str]:
    payload_bytes = canonical_bytes(subject)
    return {
        "signature_scheme": "Ed25519",
        "signature_version": "1",
        "key_id": _KEY_ID,
        "public_key_fingerprint": _FINGERPRINT,
        "payload_hash": hashlib.sha256(payload_bytes).hexdigest(),
        "signature": base64.b64encode(_PRIVATE_KEY.sign(payload_bytes)).decode("ascii"),
    }


def sign_binding_subject_payload(subject: dict[str, object]) -> dict[str, str]:
    """Fixture-only helper for constructing historical signed test artifacts."""
    return _sign_binding_subject_payload(subject)


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


# This is a disposable test owner endpoint. It is not shipped as an operational
# signer, and its generated key is trusted only by test verification fixtures.


@contextmanager
def owner_endpoint(handler=sign_binding_subject_payload, *, response_bytes=None):
    import multiprocessing
    import socket
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix="agf-owner-", dir="/tmp") as root:
        endpoint = str(Path(root) / "owner.sock")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(endpoint)
        listener.listen(1)
        listener.settimeout(2)

        def serve():
            def read_exact(connection, size):
                data = b""
                while len(data) < size:
                    chunk = connection.recv(size - len(data))
                    if not chunk:
                        raise ValueError("incomplete fixture request")
                    data += chunk
                return data

            try:
                with listener.accept()[0] as connection:
                    connection.settimeout(2)
                    size = int.from_bytes(read_exact(connection, 4), "big")
                    assert 0 < size <= 64 * 1024
                    request = json.loads(read_exact(connection, size))
                    assert request["operation"] == "attest-provider-binding"
                    assert request["schema_version"] == "1.0"
                    try:
                        response = handler(request["subject"])
                    except Exception:
                        response = {"error": "test owner unavailable"}
                    raw = json.dumps(response).encode()
                    connection.sendall(
                        response_bytes if response_bytes is not None
                        else len(raw).to_bytes(4, "big") + raw
                    )
            except (TimeoutError, OSError):
                pass  # The runtime can reject before connecting.
            finally:
                listener.close()

        process = multiprocessing.get_context("fork").Process(target=serve)
        process.start()
        listener.close()
        try:
            yield endpoint
        finally:
            process.join(timeout=0.05)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
