"""Fail-closed verification of the owner-controlled constitution root of trust."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class ConstitutionVerificationError(RuntimeError):
    """Raised when the active constitutional root of trust is not verifiable."""


SignatureVerifier = Callable[[bytes, str, str], bool]

_PROJECT_ID = re.compile(r"^project-[0-9a-f]{16}$")
_CONSTITUTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_POINTER_KEYS = {"schema_version", "project_id", "constitution_id", "record_hash"}
_RECORD_KEYS = {
    "schema_version",
    "constitution_id",
    "version",
    "project_id",
    "compatibility",
    "body",
    "key_id",
    "signature",
}


def canonical_json(value: dict[str, Any]) -> bytes:
    """Serialize a JSON object deterministically and reject ambiguous values."""
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


@dataclass(frozen=True)
class ConstitutionEvidence:
    project_id: str
    constitution_id: str
    version: str
    record_hash: str
    key_id: str
    compatibility: str
    status: str = "VERIFIED"

    def to_dict(self) -> dict[str, str]:
        return {
            "project_id": self.project_id,
            "constitution_id": self.constitution_id,
            "version": self.version,
            "record_hash": self.record_hash,
            "key_id": self.key_id,
            "compatibility": self.compatibility,
            "status": self.status,
        }


class ConstitutionVerifier:
    """Verify the externally activated constitution for one project.

    This class deliberately has no activation, promotion, rollback, or
    environment-selection operation. Those operations belong to the
    external owner-controlled activation authority.
    """

    schema_version = "1.0"
    compatibility = "agf-constitution-v1"

    def __init__(
        self,
        state_dir: str | Path,
        project_id: str,
        *,
        signature_verifier: SignatureVerifier | None = None,
    ) -> None:
        if not _PROJECT_ID.fullmatch(project_id):
            raise ConstitutionVerificationError(
                "CONSTITUTION_ROOT_OF_TRUST_INVALID: invalid project identity"
            )
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.project_id = project_id
        self.signature_verifier = signature_verifier
        self.constitution_dir = self.state_dir / "projects" / project_id / "constitution"

    @property
    def pointer_path(self) -> Path:
        return self.constitution_dir / "active.json"

    def verify(self) -> ConstitutionEvidence:
        """Return bounded verification evidence or fail closed."""
        pointer = self._read_json(self.pointer_path, "active pointer")
        self._require_keys(pointer, _POINTER_KEYS, "active pointer")
        if pointer["schema_version"] != self.schema_version:
            self._invalid("unsupported active pointer schema")
        if pointer["project_id"] != self.project_id:
            self._invalid("project identity mismatch")
        constitution_id = self._string(pointer, "constitution_id", "active pointer")
        if not _CONSTITUTION_ID.fullmatch(constitution_id):
            self._invalid("invalid constitution identity")
        record_path = self.constitution_dir / f"{constitution_id}.json"
        if record_path.is_symlink():
            self._invalid("constitution record symlink is not trusted")
        record = self._read_json(record_path, "constitution record")
        self._require_keys(record, _RECORD_KEYS, "constitution record")
        if record["schema_version"] != self.schema_version:
            self._invalid("unsupported constitution schema")
        if record["project_id"] != self.project_id:
            self._invalid("constitution project identity mismatch")
        if record["constitution_id"] != constitution_id:
            self._invalid("constitution identity mismatch")
        if record["compatibility"] != self.compatibility:
            self._invalid("constitution compatibility mismatch")
        for field in ("version", "key_id", "signature"):
            self._string(record, field, "constitution record")
        actual_hash = canonical_hash(record)
        if pointer["record_hash"] != actual_hash:
            self._invalid("constitution record hash mismatch")
        if self.signature_verifier is None:
            self._invalid("signature verifier is unavailable")
        try:
            valid_signature = self.signature_verifier(
                canonical_json(record), record["signature"], record["key_id"]
            )
        except Exception as exc:  # pragma: no cover - defensive boundary
            raise ConstitutionVerificationError(
                "CONSTITUTION_ROOT_OF_TRUST_INVALID: signature verification failed"
            ) from exc
        if valid_signature is not True:
            self._invalid("constitution signature is invalid")
        current_pointer = self._read_json(self.pointer_path, "active pointer")
        if current_pointer != pointer:
            self._invalid("active pointer changed during verification")
        return ConstitutionEvidence(
            project_id=self.project_id,
            constitution_id=constitution_id,
            version=record["version"],
            record_hash=actual_hash,
            key_id=record["key_id"],
            compatibility=record["compatibility"],
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
