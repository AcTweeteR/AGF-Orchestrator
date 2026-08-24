"""Read-only budget and fleet observability for governed execution."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class FleetObservabilityError(ValueError):
    """Raised when cost/fleet evidence is invalid."""


@dataclass(frozen=True)
class BudgetObservation:
    provider_id: str
    procedure_id: str
    task_id: str
    budget: Decimal
    used: Decimal
    kill_switch_active: bool

    @property
    def remaining(self) -> Decimal:
        return max(Decimal("0"), self.budget - self.used)

    @property
    def within_budget(self) -> bool:
        return self.used <= self.budget

    @property
    def execution_available(self) -> bool:
        return self.within_budget and not self.kill_switch_active

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "procedure_id": self.procedure_id,
            "task_id": self.task_id,
            "budget": str(self.budget),
            "used": str(self.used),
            "remaining": str(self.remaining),
            "within_budget": self.within_budget,
            "kill_switch_active": self.kill_switch_active,
            "execution_available": self.execution_available,
            "authority_effect": "NONE",
        }


def observe_budget(
    *,
    provider_id: str,
    procedure_id: str,
    task_id: str,
    budget: str | int | Decimal,
    used: str | int | Decimal,
    kill_switch_active: bool,
) -> BudgetObservation:
    try:
        budget_value = Decimal(str(budget))
        used_value = Decimal(str(used))
    except (InvalidOperation, ValueError) as exc:
        raise FleetObservabilityError("budget evidence is invalid") from exc
    if not budget_value.is_finite() or not used_value.is_finite():
        raise FleetObservabilityError("budget evidence must be finite")
    if budget_value < 0 or used_value < 0:
        raise FleetObservabilityError("budget evidence must be non-negative")
    for label, value in (
        ("provider_id", provider_id),
        ("procedure_id", procedure_id),
        ("task_id", task_id),
    ):
        if not isinstance(value, str) or not value.strip():
            raise FleetObservabilityError(f"{label} is invalid")
    if not isinstance(kill_switch_active, bool):
        raise FleetObservabilityError("kill_switch_active is invalid")
    return BudgetObservation(
        provider_id=provider_id,
        procedure_id=procedure_id,
        task_id=task_id,
        budget=budget_value,
        used=used_value,
        kill_switch_active=kill_switch_active,
    )


def rank_eligible_by_cost(
    candidates: tuple[tuple[str, Decimal, bool], ...],
) -> tuple[str, ...]:
    """Rank only candidates already declared eligible by upstream governance."""
    eligible = [
        (provider_id, cost)
        for provider_id, cost, is_eligible in candidates
        if is_eligible
    ]
    ranked = sorted(eligible, key=lambda item: (item[1], item[0]))
    return tuple(provider_id for provider_id, _ in ranked)
