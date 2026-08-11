"""The permanent owner-controlled Constitution Authority."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


class ConstitutionVerificationError(RuntimeError):
    """Raised when the Constitution Authority cannot prove valid state."""


_PROJECT_ID = re.compile(r"^project-[0-9a-f]{16}$")
_CONSTITUTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_POINTER_KEYS = {"schema_version", "project_id", "constitution_id", "record_hash"}
_RECORD_KEYS = {
    "schema_version",
    "constitution_id",
    "version",
    "project_id",
    "compatibility",
    "approval_status",
    "body",
    "key_id",
    "signature",
}
_AUTHORITY_KEY_ID = "owner-key-1"


def canonical_json(value: dict[str, Any]) -> bytes:
    """Serialize an object deterministically and reject ambiguous values."""
    if not isinstance(value, dict):
        raise ConstitutionVerificationError("CONSTITUTION_ROOT_OF_TRUST_INVALID: object required")
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ConstitutionVerificationError(
            "CONSTITUTION_ROOT_OF_TRUST_INVALID: non-canonical data"
        ) from exc


def canonical_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ActiveConstitution:
    """Immutable constitutional state returned only by Constitution Authority."""

    project_id: str
    constitution_id: str
    version: str
    compatibility: str
    approval_status: str
    key_id: str
    record_hash: str
    body: Mapping[str, Any]

    def evidence(self) -> dict[str, str]:
        return {
            "project_id": self.project_id,
            "constitution_id": self.constitution_id,
            "version": self.version,
            "compatibility": self.compatibility,
            "approval_status": self.approval_status,
            "key_id": self.key_id,
            "record_hash": self.record_hash,
            "status": "VERIFIED",
        }


class ConstitutionAuthority:
    """The only runtime authority that resolves active constitutional state.

    Its source location, key location, compatibility and verification
    mechanism are fixed by the runtime. No constructor argument, provider,
    environment variable or runtime selector can replace them.
    """

    schema_version = "1.0"
    compatibility = "agf-constitution-v1"

    def __init__(self) -> None:
        self.state_dir = Path.home() / ".agf-orchestrator"
        self.authority_dir = self.state_dir / "constitution-authority"

    def resolve(self, project_id: str) -> ActiveConstitution:
        """Resolve through the single runtime authority resolver."""
        from .authority_context import resolve_authority

        return resolve_authority(
            project_id, constitution_backend=self, include_policy=False
        ).constitution

    def _resolve_legacy(self, project_id: str) -> ActiveConstitution:
        """Verify the pre-migration HMAC artifacts behind the central resolver."""
        self._validate_project_id(project_id)
        pointer_path = self._project_constitution_dir(project_id) / "active.json"
        pointer = self._read_json(pointer_path, "active pointer")
        self._require_keys(pointer, _POINTER_KEYS, "active pointer")
        if pointer["schema_version"] != self.schema_version:
            self._invalid("unsupported active pointer schema")
        if pointer["project_id"] != project_id:
            self._invalid("project identity mismatch")
        constitution_id = self._string(pointer, "constitution_id", "active pointer")
        if not _CONSTITUTION_ID.fullmatch(constitution_id):
            self._invalid("invalid constitution identity")
        constitution_dir = self._project_constitution_dir(project_id)
        record_path = constitution_dir / f"{constitution_id}.json"
        record = self._read_json(record_path, "constitution record")
        self._require_keys(record, _RECORD_KEYS, "constitution record")
        if record["schema_version"] != self.schema_version:
            self._invalid("unsupported constitution schema")
        if record["project_id"] != project_id:
            self._invalid("constitution project identity mismatch")
        if record["constitution_id"] != constitution_id:
            self._invalid("constitution identity mismatch")
        if record["compatibility"] != self.compatibility:
            self._invalid("constitution compatibility mismatch")
        if record["approval_status"] != "APPROVED":
            self._invalid("constitution approval is not APPROVED")
        if record["key_id"] != _AUTHORITY_KEY_ID:
            self._invalid("constitution key identity is not owner-controlled")
        self._string(record, "version", "constitution record")
        self._string(record, "signature", "constitution record")
        unsigned = {key: value for key, value in record.items() if key != "signature"}
        key = self._read_owner_key()
        expected_signature = hmac.new(key, canonical_json(unsigned), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(record["signature"], expected_signature):
            self._invalid("constitution signature is invalid")
        actual_hash = canonical_hash(record)
        if pointer["record_hash"] != actual_hash:
            self._invalid("constitution record hash mismatch")
        current_pointer = self._read_json(pointer_path, "active pointer")
        if current_pointer != pointer:
            self._invalid("active pointer changed during verification")
        return ActiveConstitution(
            project_id=project_id,
            constitution_id=constitution_id,
            version=record["version"],
            compatibility=record["compatibility"],
            approval_status=record["approval_status"],
            key_id=record["key_id"],
            record_hash=actual_hash,
            body=_freeze(record["body"]),
        )

    def _project_constitution_dir(self, project_id: str) -> Path:
        return self.state_dir / "projects" / project_id / "constitution"

    def _read_owner_key(self) -> bytes:
        path = self.authority_dir / "owner.key"
        if path.is_symlink():
            self._invalid("owner key symlink is not trusted")
        try:
            key = base64.b64decode(path.read_text(encoding="ascii"), validate=True)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ConstitutionVerificationError(
                "CONSTITUTION_ROOT_OF_TRUST_INVALID: unreadable owner key"
            ) from exc
        if len(key) < 32:
            self._invalid("owner key is too short")
        return key

    @staticmethod
    def _validate_project_id(project_id: str) -> None:
        if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id):
            raise ConstitutionVerificationError(
                "CONSTITUTION_ROOT_OF_TRUST_INVALID: invalid project identity"
            )

    @staticmethod
    def _require_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
        if set(value) != expected:
            raise ConstitutionVerificationError(
                f"CONSTITUTION_ROOT_OF_TRUST_INVALID: invalid {label} schema"
            )

    @staticmethod
    def _string(value: dict[str, Any], field: str, label: str) -> str:
        result = value.get(field)
        if not isinstance(result, str) or not result:
            raise ConstitutionVerificationError(
                f"CONSTITUTION_ROOT_OF_TRUST_INVALID: invalid {label} {field}"
            )
        return result

    @staticmethod
    def _read_json(path: Path, label: str) -> dict[str, Any]:
        if path.is_symlink():
            raise ConstitutionVerificationError(
                f"CONSTITUTION_ROOT_OF_TRUST_INVALID: {label} symlink is not trusted"
            )
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConstitutionVerificationError(
                f"CONSTITUTION_ROOT_OF_TRUST_INVALID: unreadable {label}"
            ) from exc
        if not isinstance(value, dict):
            raise ConstitutionVerificationError(
                f"CONSTITUTION_ROOT_OF_TRUST_INVALID: {label} object required"
            )
        return value

    @staticmethod
    def _invalid(detail: str) -> None:
        raise ConstitutionVerificationError(f"CONSTITUTION_ROOT_OF_TRUST_INVALID: {detail}")
