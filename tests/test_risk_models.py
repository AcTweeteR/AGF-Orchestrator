import json
from pathlib import Path

import pytest

from agf_orchestrator.risk_models import (
    RiskLevel,
    RiskValidationError,
    risk_from_dict,
)

FIXTURES = Path(__file__).parent / "fixtures" / "risk"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_valid_risk_fixture_round_trips():
    assessment = risk_from_dict(load_fixture("valid_assessment.json"))
    assert assessment.level is RiskLevel.LOW
    assert assessment.to_dict() == load_fixture("valid_assessment.json")


def test_unknown_signal_cannot_be_understated():
    with pytest.raises(RiskValidationError, match="CRITICAL"):
        risk_from_dict(load_fixture("invalid_unknown.json"))


def test_protected_path_and_rollback_unknown_require_conservative_level():
    payload = load_fixture("valid_assessment.json")
    payload["protected_paths"] = ["src/agf_orchestrator/constitution.py"]
    payload["level"] = "HIGH"
    assessment = risk_from_dict(payload)
    assert assessment.level is RiskLevel.HIGH

    payload["rollback_difficulty"] = "UNKNOWN"
    with pytest.raises(RiskValidationError, match="CRITICAL"):
        risk_from_dict(payload)


def test_secret_and_unknown_schema_fields_are_rejected():
    payload = load_fixture("valid_assessment.json")
    payload["signals"][0]["value"] = "token: do-not-store"
    with pytest.raises(RiskValidationError, match="invalid"):
        risk_from_dict(payload)

    payload = load_fixture("valid_assessment.json")
    payload["opinion"] = "safe"
    with pytest.raises(RiskValidationError, match="missing or contains unknown"):
        risk_from_dict(payload)
