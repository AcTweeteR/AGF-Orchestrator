"""Verified, generation-switched authority bundles.

The owner controller creates and signs bundles. Runtime code only verifies a
pinned owner signature, the selector, the monotonic floor, and the referenced
artifacts before constructing authority state.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from .locking import project_lock
from .owner_authority import OwnerAuthorityError, verify_envelope


class AuthorityGenerationError(ValueError):
    """Raised for incomplete, mixed, stale, or tampered authority bundles."""


class GenerationStatus(StrEnum):
    PREPARING = "PREPARING"
    PREPARED = "PREPARED"
    VERIFIED = "VERIFIED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


COMPONENTS = (
    "constitution",
    "policy",
    "activation",
    "rollback",
    "registration",
    "provider_intelligence",
)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _generation_number(generation_id: str) -> int:
    suffix = generation_id.rsplit("-", 1)[-1]
    if not suffix.isdigit() or int(suffix) < 1:
        raise AuthorityGenerationError("authority generation id has no valid number")
    return int(suffix)


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(payload, sort_keys=True, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class AuthorityComponent:
    name: str
    generation_id: str
    artifact_hash: str
    scheme: str
    project_id: str
    semantic_hash: str
    artifact_path: str | None = None
    artifact_signature: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    def validate(self, project_id: str, generation_id: str, scheme: str) -> None:
        if self.name not in COMPONENTS:
            raise AuthorityGenerationError("unknown authority component")
        if self.generation_id != generation_id or self.project_id != project_id:
            raise AuthorityGenerationError("authority component binding is invalid")
        if self.scheme != scheme:
            raise AuthorityGenerationError("authority component scheme is mixed")
        for value in (self.artifact_hash, self.semantic_hash):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
            ):
                raise AuthorityGenerationError("authority component hash is invalid")
        if self.artifact_path is not None and (
            not self.artifact_path
            or Path(self.artifact_path).is_absolute()
            or ".." in Path(self.artifact_path).parts
        ):
            raise AuthorityGenerationError("authority component path is unsafe")
        if self.scheme == "Ed25519" and not isinstance(self.artifact_signature, dict):
            raise AuthorityGenerationError("authority component signature is missing")


@dataclass(frozen=True)
class AuthorityGeneration:
    generation_id: str
    project_id: str
    scheme: str
    owner_key_id: str
    owner_fingerprint: str
    constitution_id: str
    constitution_hash: str
    policy_hash: str
    operation_id: str
    status: GenerationStatus
    components: tuple[AuthorityComponent, ...]
    manifest_hash: str
    generation_number: int = 0
    predecessor_id: str | None = None
    predecessor_hash: str | None = None
    signature: dict[str, Any] | None = None

    def _unsigned(self) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "project_id": self.project_id,
            "scheme": self.scheme,
            "owner_key_id": self.owner_key_id,
            "owner_fingerprint": self.owner_fingerprint,
            "constitution_id": self.constitution_id,
            "constitution_hash": self.constitution_hash,
            "policy_hash": self.policy_hash,
            "operation_id": self.operation_id,
            "status": self.status.value,
            "components": [item.to_dict() for item in self.components],
            "generation_number": self.generation_number,
            "predecessor_id": self.predecessor_id,
            "predecessor_hash": self.predecessor_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._unsigned(),
            "manifest_hash": self.manifest_hash,
            "signature": self.signature,
        }

    def validate(self, *, active: bool = False) -> None:
        if not self.generation_id or not self.project_id.startswith("project-"):
            raise AuthorityGenerationError("authority generation identity is invalid")
        if self.scheme not in {"HMAC-SHA256", "Ed25519"}:
            raise AuthorityGenerationError("authority generation scheme is invalid")
        if (
            not self.owner_key_id
            or not isinstance(self.owner_fingerprint, str)
            or len(self.owner_fingerprint) != 64
        ):
            raise AuthorityGenerationError("authority owner binding is invalid")
        if not self.constitution_id or len(self.constitution_hash) != 64:
            raise AuthorityGenerationError("authority Constitution binding is invalid")
        if len(self.policy_hash) != 64 or not self.operation_id:
            raise AuthorityGenerationError("authority policy binding is invalid")
        if self.generation_number < 1:
            raise AuthorityGenerationError("authority generation number is invalid")
        if self.predecessor_id is not None and not self.predecessor_hash:
            raise AuthorityGenerationError("authority predecessor binding is incomplete")
        if self.manifest_hash != _hash(self._unsigned()):
            raise AuthorityGenerationError("authority manifest hash is invalid")
        if self.scheme == "Ed25519":
            if not isinstance(self.signature, dict):
                raise AuthorityGenerationError("authority generation signature is missing")
            try:
                verify_envelope(self._unsigned(), self.signature)
            except OwnerAuthorityError as exc:
                raise AuthorityGenerationError("authority generation signature is invalid") from exc
        items = {item.name: item for item in self.components}
        if set(items) != set(COMPONENTS) or len(self.components) != len(COMPONENTS):
            raise AuthorityGenerationError("authority generation is incomplete")
        for item in self.components:
            item.validate(self.project_id, self.generation_id, self.scheme)
        if items["constitution"].semantic_hash != self.constitution_hash:
            raise AuthorityGenerationError("Constitution hash binding is inconsistent")
        if items["policy"].semantic_hash != self.policy_hash:
            raise AuthorityGenerationError("policy hash binding is inconsistent")
        if active and self.status is not GenerationStatus.ACTIVE:
            raise AuthorityGenerationError("selected authority generation is not ACTIVE")


def build_generation(**kwargs: Any) -> AuthorityGeneration:
    kwargs.pop("manifest_hash", None)
    kwargs.setdefault("generation_number", _generation_number(kwargs["generation_id"]))
    kwargs.setdefault("predecessor_id", None)
    kwargs.setdefault("predecessor_hash", None)
    kwargs.setdefault("signature", None)
    unsigned = AuthorityGeneration(**kwargs, manifest_hash="0" * 64)
    return AuthorityGeneration(
        **{**unsigned.__dict__, "manifest_hash": _hash(unsigned._unsigned())}
    )


class AuthorityGenerationStore:
    """Owner-controller mutation store and runtime read/verify store."""

    def __init__(self, root: str | Path, *, legacy_signing_key: bytes | None = None):
        self.root = Path(root).expanduser().resolve()
        self.legacy_signing_key = legacy_signing_key

    def _verify_signature(self, generation: AuthorityGeneration) -> None:
        if generation.scheme != "HMAC-SHA256":
            return
        if self.legacy_signing_key is None or not isinstance(generation.signature, str):
            raise AuthorityGenerationError("legacy HMAC generation requires explicit owner key")
        expected = hmac.new(
            self.legacy_signing_key,
            json.dumps(
                generation._unsigned(), sort_keys=True, separators=(",", ":")
            ).encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(generation.signature, expected):
            raise AuthorityGenerationError("legacy HMAC generation signature is invalid")

    def _sign_legacy(self, generation: AuthorityGeneration) -> AuthorityGeneration:
        if generation.scheme != "HMAC-SHA256":
            return generation
        if self.legacy_signing_key is None:
            raise AuthorityGenerationError("legacy HMAC generation requires explicit owner key")
        signature = hmac.new(
            self.legacy_signing_key,
            json.dumps(generation._unsigned(), sort_keys=True, separators=(",", ":")).encode(),
            hashlib.sha256,
        ).hexdigest()
        return replace(generation, signature=signature)

    def _directory(self, project_id: str) -> Path:
        self._validate_project_id(project_id)
        return self.root / "authority-generations" / project_id

    def _generation_path(self, project_id: str, generation_id: str) -> Path:
        self._validate_generation_id(generation_id)
        return self._directory(project_id) / f"{generation_id}.json"

    def _selector_path(self, project_id: str) -> Path:
        return self._directory(project_id) / "active.json"

    def _floor_path(self, project_id: str) -> Path:
        return self._directory(project_id) / "generation-floor.json"

    def _metadata_transition_path(self, project_id: str) -> Path:
        return self._directory(project_id) / "metadata-transition.json"

    def _recover_metadata(self, project_id: str) -> None:
        path = self._metadata_transition_path(project_id)
        if not path.exists():
            return
        try:
            transition = json.loads(path.read_text(encoding="utf-8"))
            if transition.get("project_id") != project_id:
                raise ValueError
            generation = transition["generation"]
            generation_path = Path(generation["path"])
            root = self._directory(project_id).resolve()
            resolved_generation_path = generation_path.resolve()
            if root not in resolved_generation_path.parents:
                raise ValueError
            _atomic_write(resolved_generation_path, generation["payload"])
            _atomic_write(self._selector_path(project_id), transition["selector"])
            _atomic_write(self._floor_path(project_id), transition["floor"])
            path.unlink()
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AuthorityGenerationError("authority metadata transition is incomplete") from exc

    def _commit_metadata(
        self,
        project_id: str,
        generation_path: Path,
        generation_payload: dict[str, Any],
        selector: dict[str, Any],
        floor: dict[str, Any],
    ) -> None:
        transition_path = self._metadata_transition_path(project_id)
        _atomic_write(
            transition_path,
            {
                "schema_version": "1.0",
                "project_id": project_id,
                "generation": {"path": str(generation_path), "payload": generation_payload},
                "selector": selector,
                "floor": floor,
            },
        )
        _atomic_write(generation_path, generation_payload)
        _atomic_write(self._selector_path(project_id), selector)
        _atomic_write(self._floor_path(project_id), floor)
        transition_path.unlink(missing_ok=True)

    @staticmethod
    def _validate_project_id(project_id: str) -> None:
        if not isinstance(project_id, str) or not re.fullmatch(r"project-[0-9a-f]{16}", project_id):
            raise AuthorityGenerationError("authority project identity is invalid")

    @staticmethod
    def _validate_generation_id(generation_id: str) -> None:
        if not isinstance(generation_id, str) or not re.fullmatch(
            r"generation-[1-9][0-9]*", generation_id
        ):
            raise AuthorityGenerationError("authority generation identity is invalid")

    def _floor(self, project_id: str) -> int:
        self._recover_metadata(project_id)
        path = self._floor_path(project_id)
        if not path.exists():
            return 0
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or set(value) != {
                "schema_version",
                "project_id",
                "generation_number",
            }:
                raise ValueError
            if value["schema_version"] != "1.0" or value["project_id"] != project_id:
                raise ValueError
            if not isinstance(value["generation_number"], int) or value["generation_number"] < 0:
                raise ValueError
            floor = value["generation_number"]
            persisted_numbers = []
            for candidate in self._directory(project_id).glob("generation-*.json"):
                try:
                    persisted_numbers.append(_generation_number(candidate.stem))
                except AuthorityGenerationError:
                    continue
            if persisted_numbers and floor < max(persisted_numbers):
                raise AuthorityGenerationError("authority generation floor was downgraded")
            return floor
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise AuthorityGenerationError("authority generation floor is invalid") from exc

    def _save_prepared_owner_controlled(self, generation: AuthorityGeneration) -> None:
        generation = self._sign_legacy(generation)
        generation.validate()
        self._verify_signature(generation)
        if generation.status not in {
            GenerationStatus.PREPARING,
            GenerationStatus.PREPARED,
            GenerationStatus.VERIFIED,
        }:
            raise AuthorityGenerationError("only non-active generations may be prepared")
        with project_lock(self.root, generation.project_id, "authority-generation-prepare", 5.0):
            _atomic_write(
                self._generation_path(generation.project_id, generation.generation_id),
                generation.to_dict(),
            )

    def load(self, project_id: str, generation_id: str) -> AuthorityGeneration:
        path = self._generation_path(project_id, generation_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            generation = _from_dict(payload)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise AuthorityGenerationError("authority generation is unavailable") from exc
        if generation.generation_id != generation_id:
            raise AuthorityGenerationError("authority generation identity does not match path")
        generation.validate()
        self._verify_signature(generation)
        return generation

    def _activate_owner_controlled(
        self,
        project_id: str,
        generation_id: str,
        *,
        active_signature: dict[str, Any] | None = None,
    ) -> None:
        generation = self.load(project_id, generation_id)
        if (
            generation.project_id != project_id
            or generation.status is not GenerationStatus.VERIFIED
        ):
            raise AuthorityGenerationError("generation is not ready for cutover")
        with project_lock(self.root, project_id, "authority-generation-activate", 5.0):
            floor = self._floor(project_id)
            if generation.generation_number <= floor:
                raise AuthorityGenerationError("authority generation downgrade or replay detected")
            active = build_generation(
                **{
                    **generation.__dict__,
                    "status": GenerationStatus.ACTIVE,
                    "manifest_hash": "0" * 64,
                    "signature": active_signature
                    if active_signature is not None
                    else generation.signature,
                }
            )
            active = self._sign_legacy(active)
            active.validate(active=True)
            self._commit_metadata(
                project_id,
                self._generation_path(project_id, generation_id),
                active.to_dict(),
                {
                    "schema_version": "1.0",
                    "project_id": project_id,
                    "generation_id": generation_id,
                    "manifest_hash": active.manifest_hash,
                },
                {
                    "schema_version": "1.0",
                    "project_id": project_id,
                    "generation_number": generation.generation_number,
                },
            )

    def active(self, project_id: str) -> AuthorityGeneration:
        self._recover_metadata(project_id)
        try:
            selector = json.loads(self._selector_path(project_id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AuthorityGenerationError("active authority selector is unavailable") from exc
        if not isinstance(selector, dict) or set(selector) != {
            "schema_version",
            "project_id",
            "generation_id",
            "manifest_hash",
        }:
            raise AuthorityGenerationError("active authority selector schema is invalid")
        if selector["schema_version"] != "1.0" or selector["project_id"] != project_id:
            raise AuthorityGenerationError("active authority selector project mismatch")
        if not all(
            isinstance(selector.get(key), str) and selector[key]
            for key in ("generation_id", "manifest_hash")
        ):
            raise AuthorityGenerationError("active authority selector values are invalid")
        generation = self.load(project_id, selector["generation_id"])
        if generation.manifest_hash != selector["manifest_hash"]:
            raise AuthorityGenerationError("active authority selector hash mismatch")
        if generation.generation_number < self._floor(project_id):
            raise AuthorityGenerationError("active authority generation is below monotonic floor")
        generation.validate(active=True)
        return generation


def _from_dict(payload: dict[str, Any]) -> AuthorityGeneration:
    required = {
        "generation_id",
        "project_id",
        "scheme",
        "owner_key_id",
        "owner_fingerprint",
        "constitution_id",
        "constitution_hash",
        "policy_hash",
        "operation_id",
        "status",
        "components",
        "manifest_hash",
        "generation_number",
        "predecessor_id",
        "predecessor_hash",
        "signature",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise AuthorityGenerationError("authority generation schema is invalid")
    try:
        return AuthorityGeneration(
            payload["generation_id"],
            payload["project_id"],
            payload["scheme"],
            payload["owner_key_id"],
            payload["owner_fingerprint"],
            payload["constitution_id"],
            payload["constitution_hash"],
            payload["policy_hash"],
            payload["operation_id"],
            GenerationStatus(payload["status"]),
            tuple(AuthorityComponent(**item) for item in payload["components"]),
            payload["manifest_hash"],
            payload["generation_number"],
            payload["predecessor_id"],
            payload["predecessor_hash"],
            payload["signature"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthorityGenerationError("authority generation schema is invalid") from exc
