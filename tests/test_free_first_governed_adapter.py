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


def test_multiple_approved_paths_are_patched_atomically(tmp_path):
    (tmp_path / "a.py").write_text("def answer():\n    return 0\n")
    (tmp_path / "b.xml").write_text('<addon name="Before"/>\n')
    adapter = FreeFirstGatewayAdapter()
    expected = GatewayResult(
        model="ollama/qwen3.5:9b-q4_K_M",
        provider="ollama",
        content='{"changes":[{"file":"a.py","find":"return 0","replace":"return 42"},'
        '{"file":"b.xml","find":"Before","replace":"After"}]}',
        prompt_tokens=15,
        completion_tokens=25,
    )
    with patch(
        "agf_orchestrator.adapters.free_first.ModelGatewayProvider.complete", return_value=expected
    ):
        outcome = adapter.execute(instruction(adapter, tmp_path, ["a.py", "b.xml"]), str(tmp_path))
    assert outcome.exit_code == 0
    assert "return 42" in (tmp_path / "a.py").read_text()
    assert 'name="After"' in (tmp_path / "b.xml").read_text()


def test_unapproved_path_in_model_result_is_rejected_atomically(tmp_path):
    (tmp_path / "a.py").write_text("x=1\n")
    (tmp_path / "b.xml").write_text('<addon name="Before"/>\n')
    expected = GatewayResult(
        model="ollama/qwen3.5:9b-q4_K_M",
        provider="ollama",
        content='{"changes":[{"file":"a.py","find":"x=1","replace":"x=2"},'
        '{"file":"secrets.py","find":"x","replace":"y"}]}',
        prompt_tokens=5,
        completion_tokens=5,
    )
    adapter = FreeFirstGatewayAdapter()
    with patch(
        "agf_orchestrator.adapters.free_first.ModelGatewayProvider.complete", return_value=expected
    ):
        outcome = adapter.execute(instruction(adapter, tmp_path, ["a.py", "b.xml"]), str(tmp_path))
    assert outcome.exit_code == 1
    assert (tmp_path / "a.py").read_text() == "x=1\n"
    assert (tmp_path / "b.xml").read_text() == '<addon name="Before"/>\n'


def test_rejects_unapproved_provider_or_mode(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        FreeFirstGatewayAdapter(model="openai/expensive")
    adapter = FreeFirstGatewayAdapter()
    result = adapter.execute("unverified", str(tmp_path), sandbox="danger-full-access")
    assert result.human_required
