from types import SimpleNamespace

from agf_orchestrator.capability_profiles import CapabilityStatus
from agf_orchestrator.cli import _authority_generation, _capability_probe_passed


def test_architect_probe_statuses_are_enum_values():
    assert _capability_probe_passed(
        {
            "repository-understanding": CapabilityStatus.SUPPORTED,
            "structured-output": CapabilityStatus.SUPPORTED,
            "reasoning": CapabilityStatus.SUPPORTED,
            "context-capacity": CapabilityStatus.SUPPORTED,
        }
    )
    assert not _capability_probe_passed(
        {"reasoning": CapabilityStatus.UNKNOWN}
    )


def test_ed25519_authority_generation_overrides_legacy_policy_snapshot():
    assert _authority_generation(
        SimpleNamespace(context=SimpleNamespace(generation_number=4)), {"generation": 3}
    ) == 4
    assert _authority_generation(SimpleNamespace(context=None), {"generation": 3}) == 3
