"""Deterministic E6-T1 merge-gate aggregation.

This module produces an evidence-bound decision record.  It does not perform
Git operations, create branches, or authorize a delivery adapter to merge.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .merge_models import (
    _SHA,
    AuthorizationStatus,
    DecisionStatus,
    GateEvidence,
    GateStatus,
    MergeDecision,
    MergeValidationError,
    RiskClass,
    canonical_hash,
)
from .policy_state_store import KillSwitchSnapshot, PolicyStateError, PolicyStateStore
from .risk_models import RiskAssessment

REQUIRED_GATES = (
    "constitution",
    "policy",
    "plan",
    "implementation",
    "review",
    "compliance",
    "validation",
    "risk",
    "caller_clean",
    "base_sha",
    "authorized_paths",
    "remote_state",
    "delivery_branch",
    "kill_switch",
)


@dataclass(frozen=True)
class MergePolicy:
    """Owner-approved policy inputs consumed by the E6-T1 aggregator."""

    policy_id: str
    version: str
    allowed_risk_classes: tuple[RiskClass, ...] = (RiskClass.LOW, RiskClass.MEDIUM)
    require_human_merge: bool = True
    permanently_forbidden: tuple[RiskClass, ...] = (
        RiskClass.HIGH,
        RiskClass.CRITICAL,
        RiskClass.UNKNOWN,
    )
    policy_hash: str = ""
    stop_signal: KillSwitchSnapshot = field(
        default_factory=KillSwitchSnapshot.disabled, init=False, repr=False
    )
    authority_generation: int = field(default=0, init=False)

    @classmethod
    def _from_verified_authority(
        cls, *, stop_signal: KillSwitchSnapshot, authority_generation: int, **kwargs: Any
    ) -> "MergePolicy":
        policy = cls(**kwargs)
        object.__setattr__(policy, "stop_signal", stop_signal)
        object.__setattr__(policy, "authority_generation", authority_generation)
        return policy

    def validate(self) -> None:
        if not self.policy_id.strip() or not self.version.strip():
            raise MergeValidationError("merge policy identity is required")
        if not isinstance(self.require_human_merge, bool):
            raise MergeValidationError("require_human_merge is invalid")
        if not self.allowed_risk_classes:
            raise MergeValidationError("allowed_risk_classes cannot be empty")
        if len(set(self.allowed_risk_classes)) != len(self.allowed_risk_classes):
            raise MergeValidationError("allowed_risk_classes must be unique")
        if any(not isinstance(item, RiskClass) for item in self.allowed_risk_classes):
            raise MergeValidationError("allowed_risk_classes is invalid")
        if any(not isinstance(item, RiskClass) for item in self.permanently_forbidden):
            raise MergeValidationError("permanently_forbidden is invalid")
        if set(self.allowed_risk_classes) & set(self.permanently_forbidden):
            raise MergeValidationError("forbidden risk classes cannot be allowed")
        if self.policy_hash and len(self.policy_hash) != 64:
            raise MergeValidationError("policy hash is invalid")
        if not isinstance(self.authority_generation, int) or self.authority_generation < 0:
            raise MergeValidationError("authority generation is invalid")


class MergePolicyEngine:
    """Aggregate all mandatory observations deterministically and fail closed."""

    name = "agf-merge-policy-engine"

    def __init__(self, policy: MergePolicy):
        policy.validate()
        self.policy = policy

    def evaluate(
        self,
        *,
        project_id: str,
        task_id: str,
        base_sha: str,
        delivery_sha: str,
        constitution_id: str,
        risk_class: RiskClass,
        risk_assessment: RiskAssessment | None = None,
        gates: Iterable[GateEvidence] | Mapping[str, GateEvidence | Mapping[str, Any]],
        expiry: str = "",
    ) -> MergeDecision:
        """Return the same decision for the same bounded input observations."""
        if not isinstance(risk_class, RiskClass):
            try:
                risk_class = RiskClass(risk_class)
            except ValueError as exc:
                raise MergeValidationError("risk_class is invalid") from exc
        if risk_assessment is not None:
            risk_assessment.validate()
            if (
                risk_assessment.project_id != project_id
                or risk_assessment.task_id != task_id
            ):
                raise MergeValidationError(
                    "Risk Engine assessment identity does not match decision"
                )
            assessed = RiskClass(risk_assessment.level.name)
            if assessed is not risk_class:
                raise MergeValidationError("risk class does not match Risk Engine assessment")
        elif self.policy.policy_hash:
            raise MergeValidationError("active policy decisions require Risk Engine assessment")
            risk_class = assessed
        stop_signal = self.policy.stop_signal
        if not isinstance(stop_signal, KillSwitchSnapshot):
            raise MergeValidationError("emergency stop signal is invalid")
        if not _SHA.fullmatch(base_sha) or not _SHA.fullmatch(delivery_sha):
            raise MergeValidationError("revision identity is invalid")
        normalized = _normalize_gates(gates)
        by_name = {gate.name: gate for gate in normalized}
        blockers: list[str] = []
        missing = [name for name in REQUIRED_GATES if name not in by_name]
        blockers.extend(f"missing gate: {name}" for name in missing)
        for name in sorted(by_name):
            gate = by_name[name]
            if gate.status is not GateStatus.PASS:
                blockers.append(f"gate {name}: {gate.status.value}")
        if stop_signal.active:
            blockers.append(
                "emergency stop is active: "
                f"{stop_signal.event_id} generation {stop_signal.generation}"
            )
        elif self.policy.policy_hash:
            expected_ref = f"kill-switch:{stop_signal.event_id}:{stop_signal.generation}"
            signal_gate = by_name.get("kill_switch")
            if signal_gate is not None and expected_ref not in signal_gate.evidence_refs:
                blockers.append("kill-switch evidence is not current")
        if risk_class in self.policy.permanently_forbidden:
            blockers.append(f"risk class {risk_class.value} is forbidden")
        elif risk_class not in self.policy.allowed_risk_classes:
            blockers.append(f"risk class {risk_class.value} is not permitted by policy")
        if self.policy.require_human_merge:
            blockers.append("human merge approval is required by policy")
        evidence_payload = {gate.name: gate.to_dict() for gate in normalized}
        evidence_hash = canonical_hash(evidence_payload)
        status = DecisionStatus.BLOCKED if blockers else DecisionStatus.ELIGIBLE
        authorization = (
            AuthorizationStatus.NOT_AUTHORIZED
            if blockers
            else AuthorizationStatus.AUTHORIZED
        )
        identity_payload = {
            "schema_version": "1.0",
            "project_id": project_id,
            "task_id": task_id,
            "base_sha": base_sha,
            "delivery_sha": delivery_sha,
            "risk_class": risk_class.value,
            "gates": [gate.to_dict() for gate in normalized],
            "authority_generation": self.policy.authority_generation,
            "kill_switch_active": stop_signal.active,
            "policy_id": self.policy.policy_id,
            "policy_version": self.policy.version,
            "constitution_id": constitution_id,
            "expiry": expiry,
            "policy_hash": self.policy.policy_hash,
        }
        decision_id = f"decision-{canonical_hash(identity_payload)[:32]}"
        decision = MergeDecision(
            schema_version="1.0",
            decision_id=decision_id,
            project_id=project_id,
            task_id=task_id,
            base_sha=base_sha,
            delivery_sha=delivery_sha,
            risk_class=risk_class,
            gates=normalized,
            policy_id=self.policy.policy_id,
            policy_version=self.policy.version,
            constitution_id=constitution_id,
            decision_status=status,
            authorization_status=authorization,
            blocking_reasons=tuple(sorted(set(blockers))),
            evidence_hash=evidence_hash,
            integrity_hash="",
            expiry=expiry,
            policy_hash=self.policy.policy_hash,
            risk_assessment=None if risk_assessment is None else risk_assessment.to_dict(),
            authority_generation=self.policy.authority_generation,
            kill_switch_active=stop_signal.active,
        )
        decision = _with_integrity(decision)
        decision.validate()
        return decision


def merge_policy_from_verified_active(project_id: str) -> MergePolicy:
    """Load and verify the external policy before enabling its risk matrix."""
    from .policy_authority import PolicyActivationError, PolicyAuthority

    try:
        active_policy = PolicyAuthority().resolve(project_id)
        authority = PolicyStateStore(Path.home() / ".agf-orchestrator", read_only=True)
        snapshot = authority.authority_snapshot(project_id)
        if snapshot is None:
            raise MergeValidationError("verified authority generation is required")
        stop_signal = KillSwitchSnapshot(
            bool(snapshot["kill_switch_active"]), int(snapshot["generation"]),
            f"stop-{snapshot['operation_id']}", snapshot["changed_at"], snapshot["reason"],
        )
    except (PolicyActivationError, PolicyStateError) as exc:
        raise MergeValidationError("verified active policy is required") from exc
    if not active_policy.allows_autonomous_merge("HIGH"):
        raise MergeValidationError("active policy does not authorize autonomous HIGH")
    return MergePolicy._from_verified_authority(
        stop_signal=stop_signal,
        authority_generation=int(snapshot["generation"]),
        policy_id=active_policy.policy_id,
        version=active_policy.version,
        allowed_risk_classes=(RiskClass.LOW, RiskClass.MEDIUM, RiskClass.HIGH),
        require_human_merge=False,
        permanently_forbidden=(RiskClass.CRITICAL, RiskClass.UNKNOWN),
        policy_hash=active_policy.policy_hash,
    )


def aggregate_merge_gates(**kwargs: Any) -> MergeDecision:
    """Convenience entry point for callers that already have policy fields."""
    policy = kwargs.pop("policy")
    if not isinstance(policy, MergePolicy):
        raise MergeValidationError("policy must be a MergePolicy")
    return MergePolicyEngine(policy).evaluate(**kwargs)


def _normalize_gates(
    gates: Iterable[GateEvidence] | Mapping[str, GateEvidence | Mapping[str, Any]],
) -> tuple[GateEvidence, ...]:
    values = gates.values() if isinstance(gates, Mapping) else gates
    normalized: list[GateEvidence] = []
    for item in values:
        if isinstance(item, GateEvidence):
            gate = item
        elif isinstance(item, Mapping):
            try:
                gate = GateEvidence(
                    name=item["name"],
                    status=GateStatus(item["status"]),
                    evidence_refs=tuple(item["evidence_refs"]),
                    observed_at=item.get("observed_at", ""),
                    freshness=item.get("freshness", ""),
                    detail=item.get("detail", ""),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise MergeValidationError("invalid gate evidence") from exc
        else:
            raise MergeValidationError("invalid gate evidence")
        gate.validate()
        normalized.append(gate)
    if not normalized:
        raise MergeValidationError("gates cannot be empty")
    if len({gate.name for gate in normalized}) != len(normalized):
        raise MergeValidationError("gate names must be unique")
    return tuple(sorted(normalized, key=lambda item: item.name))


def _with_integrity(decision: MergeDecision) -> MergeDecision:
    return MergeDecision(
        **{**decision.__dict__, "integrity_hash": canonical_hash(decision.integrity_payload())}
    )
