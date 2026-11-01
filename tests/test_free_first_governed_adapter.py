"""The free-first provider remains subordinate to AGF's existing task scope."""

from unittest.mock import patch

from agf_orchestrator.adapters.free_first import FreeFirstGatewayAdapter
from agf_orchestrator.model_gateway import GatewayResult


def instruction(adapter, root, allowed):
    return adapter.build_instruction(
        repository=str(root),
        task_id="task-1",
        title="Add feature",
        objective="Make compute return 42",
        allowed_paths=allowed,
        acceptance_criteria=["tests pass"],
        validation_commands=["python3 -m unittest"],
        stop_conditions=["scope expansion"],
    )


def test_single_allowed_file_is_edited_under_parent_governance(tmp_path):
    (tmp_path / "compute.py").write_text("def compute():\n    return 0\n")
    adapter = FreeFirstGatewayAdapter()
    expected = GatewayResult(
        model="ollama/qwen3.5:9b-q4_K_M",
        provider="ollama",
        content='{"content":"def compute():\\n    return 42\\n"}',
        prompt_tokens=20,
        completion_tokens=10,
    )
    with patch(
        "agf_orchestrator.adapters.free_first.ModelGatewayProvider.complete", return_value=expected
    ) as call:
        result = adapter.execute(instruction(adapter, tmp_path, ["compute.py"]), str(tmp_path))
    assert result.exit_code == 0
    assert result.execution_observation["monetary_cost_eur"] == 0
    assert "return 42" in (tmp_path / "compute.py").read_text()
    assert call.call_args.kwargs["eligible_models"] == frozenset({expected.model})


def test_multiple_paths_are_rejected_without_calling_model(tmp_path):
    (tmp_path / "a.py").write_text("x=1\n")
    (tmp_path / "b.py").write_text("x=2\n")
    adapter = FreeFirstGatewayAdapter()
    with patch("agf_orchestrator.adapters.free_first.ModelGatewayProvider.complete") as call:
        result = adapter.execute(instruction(adapter, tmp_path, ["a.py", "b.py"]), str(tmp_path))
    assert result.exit_code == 1
    call.assert_not_called()
    assert (tmp_path / "a.py").read_text() == "x=1\n"


def test_rejects_unapproved_provider_or_mode(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        FreeFirstGatewayAdapter(model="openai/expensive")
    adapter = FreeFirstGatewayAdapter()
    result = adapter.execute("unverified", str(tmp_path), sandbox="danger-full-access")
    assert result.human_required
