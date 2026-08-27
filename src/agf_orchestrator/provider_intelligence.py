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
SUPPORTED_DECISION_DOMAINS = frozenset(
    {"architect", "code-intelligence", "knowledge", "documentation"}
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


def _validate_gate_pairs(pairs: object, allowed_names: set[str], label: str) -> None:
    if not isinstance(pairs, (tuple, list)):
        raise ProviderIntelligenceError(f"{label} is invalid")
    for item in pairs:
        if (
            not isinstance(item, (tuple, list))
            or len(item) != 2
            or not isinstance(item[0], str)
            or item[0] not in allowed_names
            or not isinstance(item[1], bool)
        ):
            raise ProviderIntelligenceError(f"{label} is invalid")


def _validated_gate_evidence(pairs: object) -> dict[str, str]:
    if not isinstance(pairs, (tuple, list)):
        raise ProviderIntelligenceError("gate evidence is invalid")
    result: dict[str, str] = {}
    for item in pairs:
        if (
            not isinstance(item, (tuple, list))
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], str)
            or not item[1].strip()
            or item[0] in result
        ):
            raise ProviderIntelligenceError("gate evidence is invalid")
        result[item[0]] = item[1]
    if set(result) != set(ARCHITECT_GATE_NAMES):
        raise ProviderIntelligenceError("gate evidence is incomplete")
    return result


def _parse_scoped_gate_evidence(value: object) -> tuple[tuple[Any, ...], ...]:
    if not isinstance(value, (tuple, list)):
        raise ProviderIntelligenceError("provider-scoped gate evidence is invalid")
    parsed: list[tuple[Any, ...]] = []
    for item in value:
        if not isinstance(item, (tuple, list)) or len(item) != 3:
            raise ProviderIntelligenceError("provider-scoped gate evidence is invalid")
        provider_id, profile_sha256, facts = item
        if not isinstance(facts, (tuple, list)):
            raise ProviderIntelligenceError("provider-scoped gate evidence is invalid")
        parsed.append(
            (
                provider_id,
                profile_sha256,
                tuple(
                    tuple(fact) if isinstance(fact, (tuple, list)) else fact
                    for fact in facts
                ),
            )
        )
    return tuple(parsed)


def _validate_architect_gate_evidence(
    gate_evidence: dict[str, str], gates: SelectionGates
) -> None:
    if not gate_evidence["policy_eligible"].startswith("active-policy:"):
        raise ProviderIntelligenceError("policy gate evidence is not authority-bound")
    for name, prefix in (
        ("privacy_eligible", "codex-safe-environment-v1;read-only-canary;"),
        ("independence_eligible", "architect-advisory;reviewer-separate-stage;"),
    ):
        value = gate_evidence[name]
        if not value.startswith(prefix) or value.removeprefix(prefix) not in {"True", "False"}:
            raise ProviderIntelligenceError(f"{name} evidence is invalid")
        if (value.endswith("True")) != bool(getattr(gates, name)):
            raise ProviderIntelligenceError(f"{name} evidence disagrees with gate")
    budget = gate_evidence["budget_eligible"]
    if not budget.startswith("bounded-timeout-seconds:"):
        raise ProviderIntelligenceError("budget gate evidence is invalid")
    try:
        timeout_text, budget_value = budget.removeprefix("bounded-timeout-seconds:").split(";", 1)
        if float(timeout_text) <= 0 or budget_value not in {"True", "False"}:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ProviderIntelligenceError("budget gate evidence is invalid") from exc
    if (budget_value == "True") != bool(gates.budget_eligible):
        raise ProviderIntelligenceError("budget evidence disagrees with gate")
    if gate_evidence["health_eligible"] != f"invocation-verified:{gates.health_eligible}":
        raise ProviderIntelligenceError("health gate evidence disagrees with gate")
    empirical = gate_evidence["empirical_evidence_eligible"]
    if not empirical.startswith("deterministic-canary-sha256:"):
        raise ProviderIntelligenceError("empirical gate evidence is invalid")
    canary_hash = empirical.removeprefix("deterministic-canary-sha256:")
    if len(canary_hash) != 64 or any(char not in "0123456789abcdef" for char in canary_hash):
        raise ProviderIntelligenceError("empirical gate evidence is invalid")


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
    provider_gate_evidence: tuple[tuple[str, bool], ...] = ()
    decision_domain: str = "architect"
    provider_security_posture: tuple[tuple[str, str], ...] = ()
    provider_gate_evidence_by_candidate: tuple[
        tuple[str, str, tuple[tuple[str, bool], ...]], ...
    ] = ()

    def _unsigned(self) -> dict[str, Any]:
        payload = {
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
        if self.provider_gate_evidence:
            payload["provider_gate_evidence"] = [list(item) for item in self.provider_gate_evidence]
        if self.decision_domain != "architect":
            payload["decision_domain"] = self.decision_domain
        if self.provider_security_posture:
            payload["provider_security_posture"] = [
                list(item) for item in self.provider_security_posture
            ]
        if self.provider_gate_evidence_by_candidate:
            payload["provider_gate_evidence_by_candidate"] = [
                [provider_id, profile_sha256, [list(item) for item in gates]]
                for provider_id, profile_sha256, gates in self.provider_gate_evidence_by_candidate
            ]
        return payload

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned(), "signature": self.signature, "state_sha256": self.state_sha256}

    def validate(self, *, now: str | None = None, target_sha: str | None = None) -> None:
        if (
            self.schema_version != "1.0"
            or not isinstance(self.decision_domain, str)
            or not self.decision_domain.strip()
            or len(self.decision_domain) > 80
        ):
            raise ProviderIntelligenceError("provider intelligence schema/version is invalid")
        expected_algorithm = (
            "architect-gates-v1"
            if self.decision_domain == "architect"
            else "provider-eligibility-v1"
        )
        if self.algorithm_version != expected_algorithm:
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
        if not self.requirements or any(
            not isinstance(item, str) or not item.strip() or len(item) > 128
            for item in self.requirements
        ) or len(set(self.requirements)) != len(self.requirements):
            raise ProviderIntelligenceError("provider requirements are invalid")
        if self.decision_domain == "architect" and self.requirements != ARCHITECT_REQUIREMENTS:
            raise ProviderIntelligenceError("Architect requirements are not canonical")
        if self.decision_domain != "architect" and tuple(sorted(self.requirements)) != (
            self.requirements
        ):
            raise ProviderIntelligenceError("provider requirements are not canonicalized")
        if self.requirements_hash != _hash(list(self.requirements)):
            raise ProviderIntelligenceError("Architect requirements hash is invalid")
        if self.policy_generation < 1:
            raise ProviderIntelligenceError("policy generation is invalid")
        _validate_gate_pairs(
            self.provider_gate_evidence,
            {"network_eligible", "authentication_eligible"},
            "provider gate evidence",
        )
        if len({item[0] for item in self.provider_gate_evidence}) != len(
            self.provider_gate_evidence
        ):
            raise ProviderIntelligenceError("provider gate evidence is invalid")
        if self.decision_domain == "architect" and self.provider_gate_evidence_by_candidate:
            raise ProviderIntelligenceError(
                "Architect state cannot contain candidate-scoped gate evidence"
            )
        for name in (
            "policy_eligible", "privacy_eligible", "independence_eligible",
            "budget_eligible", "health_eligible", "empirical_evidence_eligible",
            "allow_fallback",
        ):
            if type(getattr(self.gates, name)) is not bool:
                raise ProviderIntelligenceError("selection gate types are invalid")
        candidate_keys = {
            (candidate.profile.provider_id, candidate.profile.profile_sha256)
            for candidate in self.candidates
        }
        scoped_keys = set()
        for item in self.provider_gate_evidence_by_candidate:
            if not isinstance(item, (tuple, list)) or len(item) != 3:
                raise ProviderIntelligenceError("provider-scoped gate evidence is invalid")
            provider_id, profile_sha256, facts = item
            key = (provider_id, profile_sha256)
            if (
                not isinstance(provider_id, str)
                or not provider_id
                or not isinstance(profile_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", profile_sha256)
            ):
                raise ProviderIntelligenceError("provider-scoped gate identity is invalid")
            if key in scoped_keys or key not in candidate_keys:
                raise ProviderIntelligenceError("provider-scoped gate identity is invalid")
            scoped_keys.add(key)
            _validate_gate_pairs(
                facts,
                {
                    "policy_eligible", "privacy_eligible", "network_eligible",
                    "authentication_eligible", "health_eligible", "budget_eligible",
                    "empirical_evidence_eligible", "independence_eligible",
                },
                "provider-scoped gate evidence",
            )
            if not facts or len({item[0] for item in facts}) != len(facts):
                raise ProviderIntelligenceError("provider-scoped gate evidence is invalid")
        if self.decision_domain != "architect" and len(self.candidates) > 1:
            if scoped_keys != candidate_keys:
                raise ProviderIntelligenceError(
                    "provider-scoped gate evidence is required for multiple candidates"
                )
        if any(
            not isinstance(provider_id, str)
            or not provider_id
            or not isinstance(posture, str)
            or len(posture) > 4096
            for provider_id, posture in self.provider_security_posture
        ) or len({provider_id for provider_id, _ in self.provider_security_posture}) != len(
            self.provider_security_posture
        ):
            raise ProviderIntelligenceError("provider security posture is invalid")
        if self.state_sha256 != _hash(self._unsigned()):
            raise ProviderIntelligenceError("provider intelligence state hash is invalid")
        observed_at = _utc_timestamp(self.observed_at)
        expires_at = _utc_timestamp(self.expires_at)
        if expires_at <= observed_at:
            raise ProviderIntelligenceError("provider intelligence expiry is invalid")
        if now is not None and _utc_timestamp(now) >= expires_at:
            raise ProviderIntelligenceError("provider intelligence evidence is stale")
        evidence_bundle = {
                "requirements_hash": self.requirements_hash,
                "candidates": [candidate.profile.profile_sha256 for candidate in self.candidates],
                "provider_interfaces": [list(item) for item in self.provider_interfaces],
                "gate_evidence": [list(item) for item in self.gate_evidence],
                "gates": self.to_dict()["gates"],
                "policy_generation": self.policy_generation,
                "target_sha": self.target_sha,
        }
        if self.provider_gate_evidence:
            evidence_bundle["provider_gate_evidence"] = [
                list(item) for item in self.provider_gate_evidence
            ]
        if self.provider_security_posture:
            evidence_bundle["provider_security_posture"] = [
                list(item) for item in self.provider_security_posture
            ]
        if self.provider_gate_evidence_by_candidate:
            evidence_bundle["provider_gate_evidence_by_candidate"] = [
                [provider_id, profile_sha256, [list(fact) for fact in facts]]
                for provider_id, profile_sha256, facts
                in self.provider_gate_evidence_by_candidate
            ]
        if self.decision_domain != "architect":
            evidence_bundle["decision_domain"] = self.decision_domain
        if self.evidence_bundle_hash != _hash(evidence_bundle):
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
        if self.decision_domain == "architect" and any(
            interface not in APPROVED_PROVIDER_INTERFACES
            for _, interface in self.provider_interfaces
        ):
            raise ProviderIntelligenceError("provider interface is not approved")
        if self.decision_domain != "architect" and any(
            not isinstance(interface, str) or not interface.strip() or len(interface) > 80
            for _, interface in self.provider_interfaces
        ):
            raise ProviderIntelligenceError("provider interface is invalid")
        if set(interface_ids) != candidate_ids:
            raise ProviderIntelligenceError("provider interface bindings are incomplete")
        gate_evidence = _validated_gate_evidence(self.gate_evidence)
        if self.decision_domain == "architect":
            _validate_architect_gate_evidence(gate_evidence, self.gates)


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
    optional = {
        "provider_gate_evidence", "decision_domain", "provider_security_posture",
        "provider_gate_evidence_by_candidate",
    }
    if not set(payload).issubset(required | optional) or not required.issubset(payload):
        raise ProviderIntelligenceError("provider intelligence state schema is invalid")

    def parse_security_posture(value: Any) -> tuple[tuple[str, str], ...]:
        if not isinstance(value, (list, tuple)):
            raise ProviderIntelligenceError("provider security posture is invalid")
        parsed: list[tuple[str, str]] = []
        for item in value:
            if (
                not isinstance(item, (list, tuple))
                or len(item) != 2
                or not isinstance(item[0], str)
                or not isinstance(item[1], str)
            ):
                raise ProviderIntelligenceError("provider security posture is invalid")
            parsed.append((item[0], item[1]))
        return tuple(parsed)

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
            tuple(tuple(item) for item in payload.get("provider_gate_evidence", ())),
            payload.get("decision_domain", "architect"),
            parse_security_posture(payload.get("provider_security_posture", ())),
            _parse_scoped_gate_evidence(
                payload.get("provider_gate_evidence_by_candidate", ())
            ),
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ProviderIntelligenceError("provider intelligence state schema is invalid") from exc
    try:
        state.validate()
    except ProviderIntelligenceError:
        raise
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ProviderIntelligenceError("provider intelligence state schema is invalid") from exc
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
    provider_gate_evidence: tuple[tuple[str, bool], ...] = (),
    requirements: tuple[str, ...] | None = None,
    decision_domain: str = "architect",
    provider_security_posture: tuple[tuple[str, str], ...] = (),
    provider_gate_evidence_by_candidate: tuple[
        tuple[str, str, tuple[tuple[str, bool], ...]], ...
    ] = (),
) -> ProviderIntelligenceState:
    requirements = tuple(requirements or ARCHITECT_REQUIREMENTS)
    requirements_hash = _hash(list(requirements))
    evidence_bundle = {
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
    if provider_gate_evidence:
        evidence_bundle["provider_gate_evidence"] = [
            list(item) for item in provider_gate_evidence
        ]
    if provider_security_posture:
        evidence_bundle["provider_security_posture"] = [
            list(item) for item in provider_security_posture
        ]
    if provider_gate_evidence_by_candidate:
        evidence_bundle["provider_gate_evidence_by_candidate"] = [
            [provider_id, profile_sha256, [list(item) for item in facts]]
            for provider_id, profile_sha256, facts in provider_gate_evidence_by_candidate
        ]
    if decision_domain != "architect":
        evidence_bundle["decision_domain"] = decision_domain
    evidence_bundle_hash = _hash(evidence_bundle)
    unsigned = ProviderIntelligenceState(
        "1.0",
        "architect-gates-v1" if decision_domain == "architect" else "provider-eligibility-v1",
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
        provider_gate_evidence,
        decision_domain,
        provider_security_posture,
        provider_gate_evidence_by_candidate,
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
        self._verification_mode = (
            "staging-hmac" if staging or signing_key is not None else "owner-envelope"
        )
        self.path = root / "capability-intelligence"
        self.expected_project_id: str | None = None
        self.expected_decision_domain: str | None = None

    @property
    def owner_verifying(self) -> bool:
        """Whether this concrete store uses AGF's pinned owner envelope."""
        return (
            type(self) is ProviderIntelligenceStore
            and self._verification_mode == "owner-envelope"
            and self.signing_key is None
            and self.staging is False
        )

    def for_project(
        self, project_id: str, decision_domain: str = "architect"
    ) -> "ProviderIntelligenceStore":
        if not isinstance(project_id, str) or not re.fullmatch(r"project-[0-9a-f]{16}", project_id):
            raise ProviderIntelligenceError("provider project identity is invalid")
        if not isinstance(decision_domain, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9-]{0,79}", decision_domain
        ):
            raise ProviderIntelligenceError("provider decision domain is invalid")
        if decision_domain not in SUPPORTED_DECISION_DOMAINS:
            raise ProviderIntelligenceError("provider decision domain is not registered")
        store = ProviderIntelligenceStore(
            self.root, signing_key=self.signing_key, staging=self.staging
        )
        filename = "architect.json" if decision_domain == "architect" else f"{decision_domain}.json"
        store.path = self.root / "capability-intelligence" / project_id / filename
        store.expected_project_id = project_id
        store.expected_decision_domain = decision_domain
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
        if (
            self.expected_decision_domain is not None
            and state.decision_domain != self.expected_decision_domain
        ):
            raise ProviderIntelligenceError("provider intelligence decision domain differs")
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
        if (
            self.expected_decision_domain is not None
            and state.decision_domain != self.expected_decision_domain
        ):
            raise ProviderIntelligenceError("provider intelligence decision domain differs")
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
