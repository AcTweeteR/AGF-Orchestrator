"""Reject invalid resource bounds before either adapter can invoke a provider."""

import pytest

from agf_orchestrator.adapters.codex import CodexAdapter
from agf_orchestrator.adapters.openhands import OpenHandsAdapter


@pytest.mark.parametrize("adapter", [CodexAdapter, OpenHandsAdapter])
@pytest.mark.parametrize(
    "timeout", [float("nan"), float("inf"), float("-inf"), 0, -1, True, None, "90"],
)
def test_invalid_timeout_fails_before_invocation(adapter, timeout):
    with pytest.raises(ValueError, match="finite and positive"):
        adapter(timeout=timeout)


@pytest.mark.parametrize("adapter", [CodexAdapter, OpenHandsAdapter])
@pytest.mark.parametrize("timeout", [0.01, 90, 300.0])
def test_finite_positive_timeout_is_preserved(adapter, timeout):
    assert adapter(timeout=timeout).timeout == timeout
