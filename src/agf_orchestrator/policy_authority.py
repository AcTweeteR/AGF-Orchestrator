"""Owner-signed activation and verification for the active merge policy.

ADR-0003 remains a proposal until this authority verifies both an owner-signed
policy and a separately signed activation record.  The activation state lives
outside managed repositories beside the constitutional state.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from .constitution import ConstitutionAuthority, ConstitutionVerificationError
from .policy_state_store import PolicyStateError, PolicyStateStore


class PolicyActivationError(RuntimeError):
    """Raised when signed policy state is missing, invalid, or inconsistent."""


class EffectiveRisk(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


_PROJECT_ID = re.compile(r"^project-[0-9a-f]{16}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_POLICY_ID = "merge-policy-adr-0003"
_POLICY_VERSION = "1.0"
_KEY_ID = "owner-key-1"
_SCHEMA = "1.0"
_COMPATIBILITY = "agf-merge-policy-v1"
_ROLLBACK_TARGET = "project-policy-require-human-merge"
_MANDATORY_GATES = (
    "constitution", "policy", "plan", "implementation", "review", "compliance",
    "validation", "risk", "caller_clean", "base_sha", "authorized_paths",
    "remote_state", "delivery_branch",
)


def canonical_json(value: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PolicyActivationError("POLICY_NOT_ACTIVATED: non-canonical policy data") from exc


def canonical_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


@dataclass(frozen=True)
class ActiveMergePolicy:
    project_id: str
    policy_id: str
    version: str
    policy_hash: str
    activation_hash: str
    rollback_target: Any
    key_id: str
    freshness_limits: dict[str, Any]

    def requires_human_merge(self, risk: EffectiveRisk | str) -> bool:
        try:
            value = EffectiveRisk(risk.upper() if isinstance(risk, str) else risk)
        except ValueError:
            value = EffectiveRisk.UNKNOWN
        return value in {EffectiveRisk.CRITICAL, EffectiveRisk.UNKNOWN}

    def allows_autonomous_merge(self, risk: EffectiveRisk | str) -> bool:
        try:
            value = EffectiveRisk(risk.upper() if isinstance(risk, str) else risk)
        except ValueError:
            return False
        return value in {
            EffectiveRisk.LOW,
            EffectiveRisk.MEDIUM,
            EffectiveRisk.HIGH,
        }


class PolicyAuthority:
    """The only authority that resolves the active ADR-0003 policy."""

    def __init__(self) -> None:
        self.state_dir = Path.home() / ".agf-orchestrator"
        self.authority_dir = self.state_dir / "constitution-authority"

    def resolve(self, project_id: str) -> ActiveMergePolicy:
        self._validate_project_id(project_id)
        constitution = self._verify_constitution(project_id)
        try:
            snapshot = PolicyStateStore(self.state_dir, read_only=True).snapshot(project_id)
        except PolicyStateError as exc:
            raise PolicyActivationError("POLICY_NOT_ACTIVATED: policy state is unreadable") from exc
        if snapshot is None or snapshot.get("active_policy_hash") is None:
            self._invalid("policy is not activated")
        policy = snapshot.get("policy")
        activation = snapshot.get("activation")
        if not isinstance(policy, dict) or not isinstance(activation, dict):
            self._invalid("policy state is inconsistent")
        pointer = {
            "schema_version": _SCHEMA,
            "project_id": project_id,
            "policy_id": snapshot.get("active_policy_id"),
            "policy_hash": snapshot.get("active_policy_hash"),
            "activation_hash": canonical_hash(activation),
        }
        self._verify_policy(project_id, constitution.constitution_id, pointer, policy)
        self._verify_activation(project_id, pointer, policy, activation)
        if snapshot.get("generation", 0) < 1:
            self._invalid("policy generation is invalid")
        return ActiveMergePolicy(
            project_id,
            policy["policy_id"],
            policy["version"],
            canonical_hash(policy),
            canonical_hash(activation),
            activation["rollback_target"],
            policy["key_id"],
            policy["body"]["freshness_limits"],
        )

    def resolve_or_none(self, project_id: str) -> ActiveMergePolicy | None:
        """Return no policy only when no activation artifacts exist at all."""
        self._validate_project_id(project_id)
        if not (self.state_dir / "policy-state.sqlite3").exists():
            return None
        try:
            snapshot = PolicyStateStore(self.state_dir, read_only=True).snapshot(project_id)
        except PolicyStateError as exc:
            raise PolicyActivationError("POLICY_NOT_ACTIVATED: policy state is unreadable") from exc
        if snapshot is None:
            return None
        return self.resolve(project_id)

    def _verify_constitution(self, project_id: str):
        try:
            return ConstitutionAuthority().resolve(project_id)
        except ConstitutionVerificationError as exc:
            raise PolicyActivationError(
                "POLICY_NOT_ACTIVATED: ConstitutionAuthority failed"
            ) from exc

    def _verify_policy(self, project_id, constitution_id, pointer, policy) -> None:
        required = {
            "schema_version", "policy_id", "version", "project_id", "constitution_id",
            "compatibility", "approval_status", "key_id", "previous_policy",
            "activation_time", "body", "signature",
        }
        if set(policy) != required or set(pointer) != {
            "schema_version", "project_id", "policy_id", "policy_hash", "activation_hash"
        }:
            self._invalid("policy schema is invalid")
        if policy["schema_version"] != _SCHEMA or policy["policy_id"] != _POLICY_ID:
            self._invalid("policy identity is invalid")
        if policy["version"] != _POLICY_VERSION or policy["project_id"] != project_id:
            self._invalid("policy version or project identity is invalid")
        if policy["constitution_id"] != constitution_id:
            self._invalid("policy constitution binding is invalid")
        if policy["compatibility"] != _COMPATIBILITY or policy["approval_status"] != "APPROVED":
            self._invalid("policy approval or compatibility is invalid")
        if policy["key_id"] != _KEY_ID or not isinstance(policy["body"], dict):
            self._invalid("policy owner binding is invalid")
        if not self._is_identity(policy["previous_policy"]):
            self._invalid("policy rollback identity is invalid")
        if policy["previous_policy"]["policy_id"] == _ROLLBACK_TARGET:
            expected_rollback = hashlib.sha256(
                canonical_json({"project_id": project_id, "require_human_merge": True})
            ).hexdigest()
            if policy["previous_policy"]["policy_hash"] != expected_rollback:
                self._invalid("policy rollback target is not the constitutional fallback")
        limits = policy["body"].get("freshness_limits")
        self._validate_limits(limits)
        self._validate_time(policy["activation_time"], limits["policy_seconds"])
        self._verify_signature(policy, "policy")
        if pointer["project_id"] != project_id or pointer["policy_id"] != _POLICY_ID:
            self._invalid("active policy pointer identity is invalid")
        if not self._is_hash(pointer["policy_hash"]):
            self._invalid("active policy hash format is invalid")
        if not self._is_hash(pointer["activation_hash"]):
            self._invalid("active activation hash format is invalid")
        if pointer["policy_hash"] != canonical_hash(policy):
            self._invalid("active policy hash is invalid")
        body = policy["body"]
        expected_behavior = {
            "LOW": "AUTONOMOUS", "MEDIUM": "AUTONOMOUS", "HIGH": "AUTONOMOUS",
            "CRITICAL": "HUMAN_REQUIRED", "UNKNOWN": "HUMAN_REQUIRED",
        }
        if body.get("risk_merge_behavior") != expected_behavior:
            self._invalid("effective risk policy is invalid")
        if body.get("mandatory_gates") != list(_MANDATORY_GATES):
            self._invalid("mandatory gates are invalid")
        if not isinstance(body.get("freshness_limits"), dict):
            self._invalid("freshness limits are invalid")
        if not isinstance(body.get("protected_object_prohibitions"), list):
            self._invalid("protected object prohibitions are invalid")
        if body.get("rollback_target") != _ROLLBACK_TARGET:
            self._invalid("rollback target is invalid")
        if body.get("protected_boundary_uncertainty") != "CRITICAL":
            self._invalid("protected-boundary uncertainty is not CRITICAL")

    def _verify_activation(self, project_id, pointer, policy, activation) -> None:
        required = {
            "schema_version", "project_id", "policy_id", "policy_version", "policy_hash",
            "previous_policy_hash", "active_pointer_value", "activation_time",
            "compatibility", "rollback_target", "key_id", "operation_id", "signature",
        }
        if set(activation) != required:
            self._invalid("activation schema is invalid")
        if activation["project_id"] != project_id or activation["policy_id"] != policy["policy_id"]:
            self._invalid("activation project or policy binding is invalid")
        if activation["policy_version"] != policy["version"]:
            self._invalid("activation policy version is invalid")
        if not self._is_hash(activation["policy_hash"]):
            self._invalid("activation policy hash format is invalid")
        if activation["policy_hash"] != canonical_hash(policy):
            self._invalid("activation policy hash is invalid")
        expected_pointer = {
            "project_id": project_id,
            "policy_id": policy["policy_id"],
            "policy_hash": activation["policy_hash"],
        }
        if activation["active_pointer_value"] != expected_pointer:
            self._invalid("activation pointer binding is invalid")
        if activation["schema_version"] != _SCHEMA or activation["compatibility"] != _COMPATIBILITY:
            self._invalid("activation schema or compatibility is invalid")
        if activation["key_id"] != _KEY_ID:
            self._invalid("activation owner binding is invalid")
        if not self._is_hash(activation["previous_policy_hash"]):
            self._invalid("activation rollback predecessor is invalid")
        if activation["previous_policy_hash"] != policy["previous_policy"]["policy_hash"]:
            self._invalid("activation rollback predecessor does not match policy")
        if activation["rollback_target"] != policy["previous_policy"]:
            self._invalid("activation rollback target is invalid")
        self._validate_time(
            activation["activation_time"], policy["body"]["freshness_limits"]["policy_seconds"]
        )
        if not isinstance(activation["operation_id"], str) or not re.fullmatch(
            r"operation-[a-z0-9][a-z0-9-]{2,127}", activation["operation_id"]
        ):
            self._invalid("activation operation identity is invalid")
        self._verify_signature(activation, "activation")
        if pointer["activation_hash"] != canonical_hash(activation):
            self._invalid("active pointer activation hash is invalid")

    def _verify_signature(self, record: dict[str, Any], label: str) -> None:
        signature = record.get("signature")
        if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
            self._invalid(f"{label} signature is invalid")
        key = self._read_owner_key()
        expected = hmac.new(
            key,
            canonical_json({key: value for key, value in record.items() if key != "signature"}),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            self._invalid(f"{label} signature is invalid")

    def _read_owner_key(self) -> bytes:
        path = self.authority_dir / "owner.key"
        if path.is_symlink() or self.authority_dir.is_symlink():
            self._invalid("owner key symlink is not trusted")
        try:
            if self.authority_dir.stat().st_mode & 0o077:
                self._invalid("owner key directory permissions are broad")
            if path.stat().st_mode & 0o077:
                self._invalid("owner key permissions are broad")
            key = base64.b64decode(path.read_text(encoding="ascii"), validate=True)
        except (OSError, UnicodeError, ValueError) as exc:
            raise PolicyActivationError("POLICY_NOT_ACTIVATED: owner key unreadable") from exc
        if len(key) < 32:
            self._invalid("owner key is too short")
        return key

    @staticmethod
    def _is_hash(value: Any) -> bool:
        return isinstance(value, str) and bool(_HEX.fullmatch(value))

    @staticmethod
    def _is_identity(value: Any) -> bool:
        return (
            isinstance(value, dict)
            and set(value) == {"policy_id", "policy_hash"}
            and isinstance(value["policy_id"], str)
            and PolicyAuthority._is_hash(value["policy_hash"])
        )

    @staticmethod
    def _validate_time(value: Any, max_age_seconds: int) -> None:
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise PolicyActivationError(
                "POLICY_NOT_ACTIVATED: invalid activation time"
            ) from exc
        now = datetime.now(UTC)
        if (
            parsed.tzinfo is None
            or parsed > now
            or now - parsed > timedelta(seconds=max_age_seconds)
        ):
            raise PolicyActivationError("POLICY_NOT_ACTIVATED: invalid activation time")

    @staticmethod
    def _validate_limits(value: Any) -> None:
        if not isinstance(value, dict) or set(value) != {"policy_seconds", "gate_seconds"}:
            raise PolicyActivationError("POLICY_NOT_ACTIVATED: invalid freshness limits")
        if any(
            not isinstance(value[key], int) or isinstance(value[key], bool) or value[key] <= 0
            for key in value
        ):
            raise PolicyActivationError("POLICY_NOT_ACTIVATED: invalid freshness limits")

    def _policy_dir(self, project_id: str) -> Path:
        return self.state_dir / "projects" / project_id / "policy"

    @staticmethod
    def _validate_project_id(project_id: str) -> None:
        if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id):
            raise PolicyActivationError("POLICY_NOT_ACTIVATED: invalid project identity")

    @staticmethod
    def _require_text(value: dict[str, Any], field: str, label: str) -> str:
        result = value.get(field)
        if not isinstance(result, str) or not result:
            raise PolicyActivationError(f"POLICY_NOT_ACTIVATED: invalid {label} {field}")
        return result

    @staticmethod
    def _read_object(path: Path, label: str) -> dict[str, Any]:
        if path.is_symlink():
            raise PolicyActivationError(f"POLICY_NOT_ACTIVATED: {label} symlink is not trusted")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PolicyActivationError(f"POLICY_NOT_ACTIVATED: unreadable {label}") from exc
        if not isinstance(value, dict):
            raise PolicyActivationError(f"POLICY_NOT_ACTIVATED: {label} object required")
        return value

    @staticmethod
    def _check_permissions(directory: Path, *paths: Path) -> None:
        if directory.is_symlink():
            raise PolicyActivationError(
                "POLICY_NOT_ACTIVATED: policy directory symlink is not trusted"
            )
        for path in (directory, *paths):
            try:
                mode = path.stat().st_mode & 0o777
            except OSError as exc:
                raise PolicyActivationError(
                    "POLICY_NOT_ACTIVATED: policy permissions unreadable"
                ) from exc
            if mode & 0o077:
                raise PolicyActivationError("POLICY_NOT_ACTIVATED: policy permissions are broad")

    @staticmethod
    def _invalid(detail: str) -> None:
        raise PolicyActivationError(f"POLICY_NOT_ACTIVATED: {detail}")
