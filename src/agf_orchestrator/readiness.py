"""Deterministic, observational mission-readiness diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping


class ReadinessStatus(StrEnum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    status: ReadinessStatus
    blocking: bool
    detail: str

    @property
    def blocks(self) -> bool:
        return self.blocking and self.status is not ReadinessStatus.PASS


@dataclass(frozen=True)
class ReadinessReport:
    checks: tuple[ReadinessCheck, ...]

    @property
    def ready(self) -> bool:
        return bool(self.checks) and not any(check.blocks for check in self.checks)

    @property
    def informational_score(self) -> int:
        """Human-facing score only; it never authorizes or overrides a gate."""
        if not self.checks:
            return 0
        passed = sum(check.status is ReadinessStatus.PASS for check in self.checks)
        return round(100 * passed / len(self.checks))

    @property
    def blockers(self) -> tuple[ReadinessCheck, ...]:
        return tuple(check for check in self.checks if check.blocks)

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "informational_score": self.informational_score,
            "checks": [
                {
                    "name": check.name,
                    "status": check.status.value,
                    "blocking": check.blocking,
                    "detail": check.detail,
                }
                for check in self.checks
            ],
        }


REQUIRED_READINESS_CHECKS = (
    "objective_and_roadmap_valid",
    "provider_eligible",
    "procedure_available",
    "validations_declared",
    "risk_and_policy_gates_pass",
    "evidence_fresh",
    "budget_available",
    "human_required_clear",
    "state_consistent",
    "kill_switch_clear",
)


def evaluate_readiness(
    evidence: Mapping[str, bool | None],
    *,
    details: Mapping[str, str] | None = None,
) -> ReadinessReport:
    """Evaluate explicit persisted evidence without mutation or inference.

    True maps to PASS, False to BLOCKED, and absent/None to UNKNOWN. UNKNOWN is
    blocking for every required check so missing evidence fails closed.
    """
    details = details or {}
    checks: list[ReadinessCheck] = []
    for name in REQUIRED_READINESS_CHECKS:
        value = evidence.get(name)
        if value is True:
            status = ReadinessStatus.PASS
        elif value is False:
            status = ReadinessStatus.BLOCKED
        else:
            status = ReadinessStatus.UNKNOWN
        checks.append(
            ReadinessCheck(
                name=name,
                status=status,
                blocking=True,
                detail=details.get(name, "persisted evidence"),
            )
        )
    return ReadinessReport(tuple(checks))


def doctor(evidence: Mapping[str, bool | None], *, details: Mapping[str, str] | None = None) -> dict[str, object]:
    """Return an observational doctor report; this function performs no writes."""
    report = evaluate_readiness(evidence, details=details)
    return {
        **report.to_dict(),
        "remediation": [
            f"resolve:{check.name}"
            for check in report.blockers
        ],
        "authority_effect": "NONE",
    }
