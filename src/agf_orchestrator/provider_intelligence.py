"""Durable, fail-closed Provider Intelligence for governed Architect selection.

This module stores only verified capability evidence.  It does not select a
provider by preference and it does not invoke privileged runtime operations.
The owner/runtime boundary remains the existing adapter and policy boundary.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .authority_context import AuthorityContextError
from .capability_profiles import (
    CapabilityObservation,
    CapabilityProfile,
    CapabilityProfileError,
    CapabilityStatus,
    capability_profile_hash,
    profile_from_dict,
    sha256_text,
)
from .capability_selection import CapabilityCandidate, SelectionGates
from .locking import project_lock
from .owner_authority import OwnerAuthorityError, verify_envelope

ARCHITECT_REQUIREMENTS = (
    "repository-understanding",
    "structured-output",
    "reasoning",
    "context-capacity",
)
APPROVED_PROVIDER_INTERFACES = frozenset({"codex", "openhands"})
ARCHITECT_GATE_NAMES = (
    "policy_eligible",
    "privacy_eligible",
    "independence_eligible",
    "budget_eligible",
    "health_eligible",
    "empirical_evidence_eligible",
)


class ProviderIntelligenceError(ValueError):
    """Raised when provider evidence is missing, stale, tampered, or unsafe."""


def _utc_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ProviderIntelligenceError("provider intelligence timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ProviderIntelligenceError("provider intelligence timestamp must include timezone")
    return parsed.astimezone(UTC)


def _is_hash(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def sign_state(
    state: "ProviderIntelligenceState", key: bytes, *, staging: bool = False
) -> "ProviderIntelligenceState":
    if not staging:
        raise ProviderIntelligenceError("legacy HMAC signing is restricted to explicit staging")
    signature = hmac.new(key, _canonical_bytes(state._unsigned()), hashlib.sha256).hexdigest()
    return ProviderIntelligenceState(**{**state.__dict__, "signature": signature})


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


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
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
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
class ProviderIntelligenceState:
    """Immutable selection inputs bound to one project and target baseline."""

    schema_version: str
    algorithm_version: str
    project_id: str
    constitution_id: str
    constitution_record_hash: str
    target_sha: str
    observed_at: str
    expires_at: str | None
    requirements: tuple[str, ...]
    requirements_hash: str
    candidates: tuple[CapabilityCandidate, ...]
    provider_interfaces: tuple[tuple[str, str], ...]
    gates: SelectionGates
    gate_evidence: tuple[tuple[str, str], ...]
    policy_generation: int
    evidence_bundle_hash: str
    signing_key_id: str
    signature: Any
    state_sha256: str

    def _unsigned(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "algorithm_version": self.algorithm_version,
            "project_id": self.project_id,
            "constitution_id": self.constitution_id,
            "constitution_record_hash": self.constitution_record_hash,
            "target_sha": self.target_sha,
            "observed_at": self.observed_at,
            "expires_at": self.expires_at,
            "requirements": list(self.requirements),
            "requirements_hash": self.requirements_hash,
            "candidates": [
                {
                    "profile": candidate.profile.to_dict(),
                    "priority": candidate.priority,
                    "diagnostic_only": candidate.diagnostic_only,
                }
                for candidate in self.candidates
            ],
            "provider_interfaces": [list(item) for item in self.provider_interfaces],
            "gates": {
                "policy_eligible": self.gates.policy_eligible,
                "privacy_eligible": self.gates.privacy_eligible,
                "independence_eligible": self.gates.independence_eligible,
                "budget_eligible": self.gates.budget_eligible,
                "health_eligible": self.gates.health_eligible,
                "empirical_evidence_eligible": self.gates.empirical_evidence_eligible,
                "allow_fallback": self.gates.allow_fallback,
            },
            "gate_evidence": [list(item) for item in self.gate_evidence],
            "policy_generation": self.policy_generation,
            "evidence_bundle_hash": self.evidence_bundle_hash,
            "signing_key_id": self.signing_key_id,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned(), "signature": self.signature, "state_sha256": self.state_sha256}

    def validate(self, *, now: str | None = None, target_sha: str | None = None) -> None:
        if self.schema_version != "1.0" or self.algorithm_version != "architect-gates-v1":
            raise ProviderIntelligenceError("provider intelligence schema/version is invalid")
        if (
            not self.project_id.startswith("project-")
            or len(self.target_sha) != 40
            or not self.constitution_id
            or len(self.constitution_record_hash) != 64
        ):
            raise ProviderIntelligenceError("provider intelligence binding is invalid")
        if target_sha is not None and self.target_sha != target_sha:
            raise ProviderIntelligenceError("provider intelligence target SHA is stale")
        if self.requirements != ARCHITECT_REQUIREMENTS:
            raise ProviderIntelligenceError("Architect requirements are not canonical")
        if self.requirements_hash != _hash(list(self.requirements)):
            raise ProviderIntelligenceError("Architect requirements hash is invalid")
        if self.policy_generation < 1:
            raise ProviderIntelligenceError("policy generation is invalid")
        if self.state_sha256 != _hash(self._unsigned()):
            raise ProviderIntelligenceError("provider intelligence state hash is invalid")
        observed_at = _utc_timestamp(self.observed_at)
        expires_at = _utc_timestamp(self.expires_at)
        if expires_at <= observed_at:
            raise ProviderIntelligenceError("provider intelligence expiry is invalid")
        if now is not None and _utc_timestamp(now) >= expires_at:
            raise ProviderIntelligenceError("provider intelligence evidence is stale")
        if self.evidence_bundle_hash != _hash(
            {
                "requirements_hash": self.requirements_hash,
                "candidates": [candidate.profile.profile_sha256 for candidate in self.candidates],
                "provider_interfaces": [list(item) for item in self.provider_interfaces],
                "gate_evidence": [list(item) for item in self.gate_evidence],
                "gates": self.to_dict()["gates"],
                "policy_generation": self.policy_generation,
                "target_sha": self.target_sha,
            }
        ):
            raise ProviderIntelligenceError("provider evidence bundle hash is invalid")
        if not self.signing_key_id or not isinstance(self.signature, (str, dict)):
            raise ProviderIntelligenceError("provider intelligence signature metadata is invalid")
        for candidate in self.candidates:
            try:
                candidate.validate()
                candidate.profile.validate_binding(self.project_id, candidate.profile.provider_id)
                if now is not None:
                    candidate.profile.validate_at(now)
            except (CapabilityProfileError, ValueError) as exc:
                raise ProviderIntelligenceError(str(exc)) from exc
        candidate_provider_ids = [candidate.profile.provider_id for candidate in self.candidates]
        if len(candidate_provider_ids) != len(set(candidate_provider_ids)):
            raise ProviderIntelligenceError("provider candidate bindings are duplicated")
        candidate_ids = set(candidate_provider_ids)
        interface_ids = [provider_id for provider_id, _ in self.provider_interfaces]
        if any(
            provider_id not in candidate_ids or not interface
            for provider_id, interface in self.provider_interfaces
        ):
            raise ProviderIntelligenceError("provider interface binding is invalid")
        if len(interface_ids) != len(set(interface_ids)):
            raise ProviderIntelligenceError("provider interface bindings are duplicated")
        if any(
            interface not in APPROVED_PROVIDER_INTERFACES
            for _, interface in self.provider_interfaces
        ):
            raise ProviderIntelligenceError("provider interface is not approved")
        if set(interface_ids) != candidate_ids:
            raise ProviderIntelligenceError("provider interface bindings are incomplete")
        gate_evidence = dict(self.gate_evidence)
        if set(gate_evidence) != set(ARCHITECT_GATE_NAMES) or any(
            not isinstance(value, str) or not value.strip() for value in gate_evidence.values()
        ):
            raise ProviderIntelligenceError("Architect gate evidence is incomplete")
        if not gate_evidence["policy_eligible"].startswith("active-policy:"):
            raise ProviderIntelligenceError("policy gate evidence is not authority-bound")
        for name, prefix in (
            ("privacy_eligible", "codex-safe-environment-v1;read-only-canary;"),
            ("independence_eligible", "architect-advisory;reviewer-separate-stage;"),
        ):
            value = gate_evidence[name]
            if not value.startswith(prefix) or value.removeprefix(prefix) not in {"True", "False"}:
                raise ProviderIntelligenceError(f"{name} evidence is invalid")
            if (value.endswith("True")) != bool(getattr(self.gates, name)):
                raise ProviderIntelligenceError(f"{name} evidence disagrees with gate")
        budget = gate_evidence["budget_eligible"]
        if not budget.startswith("bounded-timeout-seconds:"):
            raise ProviderIntelligenceError("budget gate evidence is invalid")
        try:
            timeout_text, budget_value = budget.removeprefix("bounded-timeout-seconds:").split(
                ";", 1
            )
            if float(timeout_text) <= 0 or budget_value not in {"True", "False"}:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ProviderIntelligenceError("budget gate evidence is invalid") from exc
        if (budget_value == "True") != bool(self.gates.budget_eligible):
            raise ProviderIntelligenceError("budget evidence disagrees with gate")
        if gate_evidence["health_eligible"] != f"invocation-verified:{self.gates.health_eligible}":
            raise ProviderIntelligenceError("health gate evidence disagrees with gate")
        empirical = gate_evidence["empirical_evidence_eligible"]
        if not empirical.startswith("deterministic-canary-sha256:"):
            raise ProviderIntelligenceError("empirical gate evidence is invalid")
        canary_hash = empirical.removeprefix("deterministic-canary-sha256:")
        if len(canary_hash) != 64 or any(char not in "0123456789abcdef" for char in canary_hash):
            raise ProviderIntelligenceError("empirical gate evidence is invalid")


def make_profile(
    *,
    project_id: str,
    provider_id: str,
    provenance_source: str,
    observed_at: str,
    expires_at: str,
    capability_results: dict[str, CapabilityStatus],
    profile_version: int = 1,
) -> CapabilityProfile:
    """Create a profile only from explicit probe outcomes; UNKNOWN is preserved."""
    observations = tuple(
        CapabilityObservation(
            name, status, "probe-v1" if status is CapabilityStatus.SUPPORTED else None
        )
        for name, status in sorted(capability_results.items())
    )
    profile = CapabilityProfile(
        "1.0",
        f"profile-{provider_id}",
        project_id,
        provider_id,
        profile_version,
        provenance_source,
        sha256_text(provenance_source),
        observed_at,
        expires_at,
        observations,
        "0" * 64,
    )
    return CapabilityProfile(
        **{**profile.__dict__, "profile_sha256": capability_profile_hash(profile)}
    )


def state_from_dict(payload: dict[str, Any]) -> ProviderIntelligenceState:
    if not isinstance(payload, dict) or "state_sha256" not in payload:
        raise ProviderIntelligenceError("provider intelligence state is missing its hash")
    required = {
        "schema_version",
        "algorithm_version",
        "project_id",
        "constitution_id",
        "constitution_record_hash",
        "target_sha",
        "observed_at",
        "expires_at",
        "requirements",
        "requirements_hash",
        "candidates",
        "gates",
        "provider_interfaces",
        "policy_generation",
        "evidence_bundle_hash",
        "state_sha256",
        "gate_evidence",
        "signing_key_id",
        "signature",
    }
    if set(payload) != required:
        raise ProviderIntelligenceError("provider intelligence state schema is invalid")
    try:
        candidates = tuple(
            CapabilityCandidate(
                profile_from_dict(item["profile"]),
                item["priority"],
                item.get("diagnostic_only", False),
            )
            for item in payload["candidates"]
        )
        gates = SelectionGates(**payload["gates"])
        state = ProviderIntelligenceState(
            payload["schema_version"],
            payload["algorithm_version"],
            payload["project_id"],
            payload["constitution_id"],
            payload["constitution_record_hash"],
            payload["target_sha"],
            payload["observed_at"],
            payload["expires_at"],
            tuple(payload["requirements"]),
            payload["requirements_hash"],
            candidates,
            tuple(tuple(item) for item in payload["provider_interfaces"]),
            gates,
            tuple(tuple(item) for item in payload["gate_evidence"]),
            payload["policy_generation"],
            payload["evidence_bundle_hash"],
            payload["signing_key_id"],
            payload["signature"],
            payload["state_sha256"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderIntelligenceError("provider intelligence state schema is invalid") from exc
    state.validate()
    return state


def build_state(
    *,
    project_id: str,
    target_sha: str,
    constitution_id: str,
    constitution_record_hash: str,
    observed_at: str,
    expires_at: str,
    candidates: tuple[CapabilityCandidate, ...],
    provider_interfaces: tuple[tuple[str, str], ...],
    gates: SelectionGates,
    gate_evidence: tuple[tuple[str, str], ...],
    policy_generation: int,
    signing_key_id: str = "owner-key-1",
    signature: str = "0" * 64,
) -> ProviderIntelligenceState:
    requirements = ARCHITECT_REQUIREMENTS
    requirements_hash = _hash(list(requirements))
    evidence_bundle_hash = _hash(
        {
            "requirements_hash": requirements_hash,
            "candidates": [candidate.profile.profile_sha256 for candidate in candidates],
            "provider_interfaces": [list(item) for item in provider_interfaces],
            "gate_evidence": [list(item) for item in gate_evidence],
            "gates": {
                "policy_eligible": gates.policy_eligible,
                "privacy_eligible": gates.privacy_eligible,
                "independence_eligible": gates.independence_eligible,
                "budget_eligible": gates.budget_eligible,
                "health_eligible": gates.health_eligible,
                "empirical_evidence_eligible": gates.empirical_evidence_eligible,
                "allow_fallback": gates.allow_fallback,
            },
            "policy_generation": policy_generation,
            "target_sha": target_sha,
        }
    )
    unsigned = ProviderIntelligenceState(
        "1.0",
        "architect-gates-v1",
        project_id,
        constitution_id,
        constitution_record_hash,
        target_sha,
        observed_at,
        expires_at,
        requirements,
        requirements_hash,
        candidates,
        provider_interfaces,
        gates,
        gate_evidence,
        policy_generation,
        evidence_bundle_hash,
        signing_key_id,
        signature,
        "0" * 64,
    )
    return ProviderIntelligenceState(
        **{**unsigned.__dict__, "state_sha256": _hash(unsigned._unsigned())}
    )


class ProviderIntelligenceStore:
    """Atomic persistence under the approved AGF state root."""

    def __init__(
        self,
        state_dir: str | Path | None = None,
        *,
        signing_key: bytes | None = None,
        staging: bool = False,
    ):
        root = (
            Path(state_dir or os.environ.get("AGF_STATE_DIR") or "~/.agf-orchestrator")
            .expanduser()
            .absolute()
        )
        self.root = root
        if signing_key is not None and not staging:
            raise ProviderIntelligenceError(
                "legacy HMAC provider signing is restricted to explicit staging"
            )
        self.signing_key = signing_key
        self.staging = staging
        self.path = root / "capability-intelligence"
        self.expected_project_id: str | None = None

    def for_project(self, project_id: str) -> "ProviderIntelligenceStore":
        if not isinstance(project_id, str) or not re.fullmatch(r"project-[0-9a-f]{16}", project_id):
            raise ProviderIntelligenceError("provider project identity is invalid")
        store = ProviderIntelligenceStore(
            self.root, signing_key=self.signing_key, staging=self.staging
        )
        store.path = self.root / "capability-intelligence" / project_id / "architect.json"
        store.expected_project_id = project_id
        return store

    def _ensure_safe_path(self) -> None:
        root = self.root.resolve()
        if self.root.is_symlink() or root != self.root:
            raise ProviderIntelligenceError(
                "provider intelligence state root must not use symlinks"
            )
        resolved = self.path.resolve(strict=False)
        if root not in resolved.parents:
            raise ProviderIntelligenceError("provider intelligence path escapes state root")
        current = self.path
        while current != root:
            if current.is_symlink():
                raise ProviderIntelligenceError("provider intelligence path must not use symlinks")
            current = current.parent

    def load(self) -> ProviderIntelligenceState:
        return self._load()

    def _load_for_owner_recovery(self) -> ProviderIntelligenceState:
        return self._load(allow_stale_authority=True, allow_expired=True)

    def _load(
        self, *, allow_stale_authority: bool = False, allow_expired: bool = False
    ) -> ProviderIntelligenceState:
        self._ensure_safe_path()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProviderIntelligenceError("provider intelligence state is unavailable") from exc
        state = state_from_dict(payload)
        if self.expected_project_id is not None and state.project_id != self.expected_project_id:
            raise ProviderIntelligenceError("provider intelligence project binding differs")
        now = (
            None
            if allow_expired
            else datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        state.validate(now=now)
        self._verify_signature(state)
        # Once the generation selector is installed, provider evidence must
        # bind to that single verified authority path.  Before migration the
        # selector is absent and the legacy HMAC staging path remains active.
        try:
            from .authority_context import AuthorityContext

            context = AuthorityContext.resolve_runtime(state.project_id, self.root)
        except AuthorityContextError as exc:
            raise ProviderIntelligenceError("provider authority context is invalid") from exc
        if not allow_stale_authority and context is not None and (
            context.generation_number != state.policy_generation
            or context.constitution_hash != state.constitution_record_hash
        ):
            raise ProviderIntelligenceError("provider evidence is bound to stale authority")
        return state

    def save(self, state: ProviderIntelligenceState) -> None:
        if self.expected_project_id is not None and state.project_id != self.expected_project_id:
            raise ProviderIntelligenceError("provider intelligence project binding differs")
        now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        state.validate(now=now)
        self._ensure_safe_path()
        with project_lock(self.root, state.project_id, "provider-intelligence-save", timeout=5.0):
            self._save_locked(state)

    def _save_locked(
        self, state: ProviderIntelligenceState, *, allow_renewal: bool = False
    ) -> None:
        self._verify_signature(state)
        current = self.path.parent
        unsafe = self.root.is_symlink()
        while current != current.parent:
            unsafe = unsafe or current.is_symlink()
            current = current.parent
        if unsafe:
            raise ProviderIntelligenceError(
                "provider intelligence state root must not contain symlinks"
            )
        if self.path.exists():
            existing = self._load_for_owner_recovery()
            if existing.to_dict() != state.to_dict():
                existing_ids = [
                    (item.profile.provider_id, item.profile.profile_id)
                    for item in existing.candidates
                ]
                state_ids = [
                    (item.profile.provider_id, item.profile.profile_id)
                    for item in state.candidates
                ]
                if (
                    len(set(existing_ids)) != len(existing_ids)
                    or len(set(state_ids)) != len(state_ids)
                ):
                    raise ProviderIntelligenceError(
                        "provider intelligence profile identities are duplicated"
                    )
                existing_profiles = {
                    identity: item.profile.profile_version
                    for identity, item in zip(existing_ids, existing.candidates)
                }
                state_profiles = {
                    identity: item.profile.profile_version
                    for identity, item in zip(state_ids, state.candidates)
                }
                profiles_advanced = (
                    set(existing_profiles) == set(state_profiles)
                    and all(
                        state_profiles[key] > existing_profiles[key]
                        for key in existing_profiles
                    )
                )
                explicit_renewal = (
                    allow_renewal
                    and existing.project_id == state.project_id
                    and existing.target_sha == state.target_sha
                    and existing.policy_generation == state.policy_generation
                    and existing.constitution_id == state.constitution_id
                    and existing.constitution_record_hash == state.constitution_record_hash
                    and existing.requirements_hash == state.requirements_hash
                    and state.observed_at > existing.observed_at
                    and profiles_advanced
                )
                if (
                    existing.project_id == state.project_id
                    and existing.policy_generation <= state.policy_generation
                    and existing.expires_at is not None
                    and state.observed_at >= existing.expires_at
                    and profiles_advanced
                ) or (
                    existing.project_id == state.project_id
                    and existing.target_sha != state.target_sha
                    and existing.policy_generation <= state.policy_generation
                    and profiles_advanced
                ) or explicit_renewal:
                    _atomic_write(self.path, state.to_dict())
                    return
                raise ProviderIntelligenceError(
                    "provider intelligence state already exists with different evidence"
                )
            return
        _atomic_write(self.path, state.to_dict())

    def _verify_signature(self, state: ProviderIntelligenceState) -> None:
        if self.signing_key is None:
            try:
                verify_envelope(state._unsigned(), state.signature)
            except (OwnerAuthorityError, TypeError) as exc:
                raise ProviderIntelligenceError(
                    "provider intelligence owner signature is invalid"
                ) from exc
            if state.signing_key_id != state.signature.get("key_id"):
                raise ProviderIntelligenceError("provider intelligence signer identity is invalid")
        else:
            expected = hmac.new(
                self.signing_key, _canonical_bytes(state._unsigned()), hashlib.sha256
            ).hexdigest()
            if not isinstance(state.signature, str) or not hmac.compare_digest(
                state.signature, expected
            ):
                raise ProviderIntelligenceError("provider intelligence owner signature is invalid")
