"""Public-only Ed25519 verification for the owner authority boundary.

Private-key operations intentionally live in the external owner controller.
The runtime reads only the pinned, owner-controlled public anchor.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class OwnerAuthorityError(ValueError):
    """Raised when the pinned public owner authority is unavailable or invalid."""


SCHEMA_VERSION = "1.0"
SIGNATURE_SCHEME = "Ed25519"
DEFAULT_ROOT = Path.home() / ".agf-owner-root"
PINNED_OWNER_FINGERPRINT = "d23e23484571f256610658dd2b851ef3e4144dbd03827b8a66ee421c93ffe42a"


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise OwnerAuthorityError("owner authority payload is not canonical") from exc


def fingerprint(public_key: bytes) -> str:
    return hashlib.sha256(public_key).hexdigest()


def _root_dir() -> Path:
    # Runtime trust cannot be redirected by provider-controlled environment.
    return DEFAULT_ROOT.resolve()


def load_pinned_anchor() -> tuple[dict[str, Any], bytes]:
    directory = _root_dir()
    anchor_path = directory / "anchor.json"
    public_path = directory / "owner-public.key"
    try:
        if directory.is_symlink() or anchor_path.is_symlink() or public_path.is_symlink():
            raise OwnerAuthorityError("owner root anchor must not use symlinks")
        if directory.stat().st_mode & 0o022:
            raise OwnerAuthorityError("owner root directory permissions are broad")
        anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
        public_key = base64.b64decode(
            public_path.read_text(encoding="ascii").strip(), validate=True
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise OwnerAuthorityError("owner root anchor is unavailable") from exc
    required = {"schema_version", "signature_scheme", "key_id", "fingerprint"}
    if set(anchor) != required or anchor["schema_version"] != SCHEMA_VERSION:
        raise OwnerAuthorityError("owner root anchor schema is invalid")
    if anchor["signature_scheme"] != SIGNATURE_SCHEME or not isinstance(anchor["key_id"], str):
        raise OwnerAuthorityError("owner root anchor identity is invalid")
    if anchor["fingerprint"] != PINNED_OWNER_FINGERPRINT:
        raise OwnerAuthorityError("owner root fingerprint is not pinned")
    if anchor["fingerprint"] != fingerprint(public_key):
        raise OwnerAuthorityError("owner root public-key fingerprint mismatch")
    try:
        Ed25519PublicKey.from_public_bytes(public_key)
    except ValueError as exc:
        raise OwnerAuthorityError("owner root public key is invalid") from exc
    return anchor, public_key


def verify_envelope(payload: Any, envelope: dict[str, Any]) -> None:
    """Verify an owner envelope using only the pinned public key."""
    if set(envelope) != {
        "signature_scheme",
        "signature_version",
        "key_id",
        "public_key_fingerprint",
        "payload_hash",
        "signature",
    }:
        raise OwnerAuthorityError("owner signature envelope schema is invalid")
    anchor, public_key = load_pinned_anchor()
    payload_bytes = canonical_bytes(payload)
    if envelope["signature_scheme"] != SIGNATURE_SCHEME or envelope["signature_version"] != "1":
        raise OwnerAuthorityError("owner signature scheme is invalid")
    if envelope["key_id"] != anchor["key_id"]:
        raise OwnerAuthorityError("owner signature key identity mismatch")
    if envelope["public_key_fingerprint"] != anchor["fingerprint"]:
        raise OwnerAuthorityError("owner signature fingerprint mismatch")
    if envelope["payload_hash"] != hashlib.sha256(payload_bytes).hexdigest():
        raise OwnerAuthorityError("owner signature payload hash mismatch")
    try:
        signature = base64.b64decode(envelope["signature"], validate=True)
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload_bytes)
    except (ValueError, InvalidSignature) as exc:
        raise OwnerAuthorityError("owner signature is invalid") from exc
