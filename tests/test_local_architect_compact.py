"""Adversarial tests for locally compiled, source-bound Architect proposals."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agf_orchestrator.adapters.local_architect import LocalArchitectAdapter
from agf_orchestrator.adapters.local_architect_contract import (
    expand_compact_response,
    prepare_compact_request,
)
from agf_orchestrator.architect_planning import (
    build_architect_request,
    validate_architect_response,
)
from agf_orchestrator.cli import _AdapterArchitectProvider
from agf_orchestrator.model_gateway import GatewayResult
from tests.test_target_assessment import assess, context, repo


def _ready(tmp_path):
    root=repo(tmp_path)
    source=context(root)
    binding=SimpleNamespace(
        project_id="project-test",repository_root=str(root),origin_url=source.origin,
    )
    request=build_architect_request(
        "Improve file:README.md to document the sample project purpose",
        source, assess(source), registered_project=binding,
    )
    instruction=_AdapterArchitectProvider._instruction(request)
    prompt, original=prepare_compact_request(instruction,str(root))
    return root,request,instruction,prompt,original


def _response(name="README.md"):
    return json.dumps({
        "path":name,
        "objective":"Improve README with an accurate brief usage description.",
        "rationale":"README exists in the independently verified repository inventory.",
        "confidence":0.6,
    })


def test_compact_plan_preserves_full_architect_validation(tmp_path):
    root,request,instruction,prompt,original=_ready(tmp_path)
    assert "README.md" in prompt and ".git/config" not in prompt
    expanded=expand_compact_response(_response(),original)
    result=validate_architect_response(expanded,request)
    assert result["tasks"][0]["allowed_paths"] == ["README.md"]
    assert result["tasks"][0]["risk_level"] == "medium"
    assert result["tasks"][0]["validation_commands"] == ["python -m pytest"]
    assert result["tasks"][0]["dependencies"] == []
    assert result["tasks"][0]["task_id"] == "task-001"


@pytest.mark.parametrize("untrusted_path", [
    ".git/config", "../README.md", "private/secrets.json", "untracked.py",
])
def test_compact_cannot_expand_to_unapproved_path(tmp_path, untrusted_path):
    _,_,_,_,original=_ready(tmp_path)
    with pytest.raises(ValueError, match="local Architect"):
        expand_compact_response(_response(untrusted_path), original)


def test_compact_rejects_tampered_request_hash_and_unknown_binding(tmp_path):
    root,request,instruction,_,original=_ready(tmp_path)
    changed=json.loads(instruction.rsplit("\n",1)[-1])
    changed["objective"]="Different unsanctioned mission"
    with pytest.raises(ValueError, match="canonically bound"):
        prepare_compact_request(
            instruction.rsplit("\n",1)[0]+"\n"+json.dumps(changed),str(root),
        )
    changed=json.loads(instruction.rsplit("\n",1)[-1])
    changed["objective_validation_bindings"] = [{
        "criterion_id":"criterion-1","task_ids":["task-002"],
        "validation_commands":["python -m pytest"],
    }]
    from agf_orchestrator.architect_planning import architect_request_hash
    changed["request_hash"]=architect_request_hash(changed)
    with pytest.raises(ValueError, match="canonically bound"):
        prepare_compact_request(
            instruction.rsplit("\n",1)[0]+"\n"+json.dumps(changed),str(root),
        )


def test_compact_adapter_calls_only_zero_cost_model_once(tmp_path):
    root,request,instruction,_,_=_ready(tmp_path)
    mockreply=GatewayResult(
        model="ollama/qwen3.5:9b-q4_K_M",provider="ollama",
        content=_response(),prompt_tokens=50,completion_tokens=28,
    )
    with patch(
        "agf_orchestrator.adapters.local_architect.ModelGatewayProvider.complete",
        return_value=mockreply,
    ) as complete:
        answer=LocalArchitectAdapter().execute(instruction,str(root))
    complete.assert_called_once()
    assert answer.invocation_verified is True
    assert answer.execution_observation["monetary_cost_eur"] == 0
    validated = validate_architect_response(answer.final_message, request)
    assert validated["tasks"][0]["task_id"] == "task-001"


def test_compact_adapter_never_claims_validity_from_untrusted_path(tmp_path):
    root,_,instruction,_,_=_ready(tmp_path)
    mockreply=GatewayResult(
        model="ollama/qwen3.5:9b-q4_K_M",provider="ollama",
        content=_response("UNAUTHORIZED.py"),prompt_tokens=50,completion_tokens=28,
    )
    with patch(
        "agf_orchestrator.adapters.local_architect.ModelGatewayProvider.complete",
        return_value=mockreply,
    ) as complete:
        answer=LocalArchitectAdapter().execute(instruction,str(root))
    complete.assert_called_once()
    assert answer.invocation_verified is False
    assert answer.transport_error=="LOCAL_ARCHITECT_GATEWAY_FAILED"



def test_large_real_git_inventory_is_compacted_before_model_call(tmp_path):
    """Kodi has 3,310 paths / 450k host characters; only ~8k is model input."""
    import subprocess
    from types import SimpleNamespace

    from agf_orchestrator.adapters.local_architect import (
        MAX_CANONICAL_INPUT_CHARS,
        MAX_MODEL_INPUT_CHARS,
    )

    root = repo(tmp_path)
    many = root / "tests" / "many"
    many.mkdir(parents=True)
    for index in range(1300):
        (many / ("document_" + str(index).zfill(5) + "_" + "x" * 49 + ".md")).write_text(
            "file placeholder\n"
        )
    subprocess.run(["git", "-C", str(root), "add", "tests/many"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "large-readonly-inventory"],
        check=True, capture_output=True,
    )
    repository = context(root)
    request = build_architect_request(
        "Improve file:README.md with accurate usage context and no source edits",
        repository, assess(repository), registered_project=SimpleNamespace(
            project_id="project-test", repository_root=str(root),
            origin_url=repository.origin,
        ),
    )
    source = _AdapterArchitectProvider._instruction(request)
    assert 64000 < len(source) < MAX_CANONICAL_INPUT_CHARS
    prompt, original = prepare_compact_request(source, str(root))
    assert len(prompt) < MAX_MODEL_INPUT_CHARS
    assert len(prompt) < len(source) // 4
    observed = GatewayResult(
        model="ollama/qwen3.5:9b-q4_K_M", provider="ollama",
        content=_response(), prompt_tokens=80, completion_tokens=30,
    )
    with patch(
        "agf_orchestrator.adapters.local_architect.ModelGatewayProvider.complete",
        return_value=observed,
    ) as provider:
        result = LocalArchitectAdapter().execute(source, str(root))
    assert result.invocation_verified is True
    assert result.transport_error is None
    actual = validate_architect_response(result.final_message, request)
    assert actual["tasks"][0]["allowed_paths"] == ["README.md"]
    passed_message = provider.call_args.kwargs["messages"][1]["content"]
    assert passed_message == prompt
    assert len(passed_message) < MAX_MODEL_INPUT_CHARS
    assert result.execution_observation["monetary_cost_eur"] == 0


def test_full_host_budget_and_large_noncompact_payloads_fail_before_gateway(tmp_path):
    from agf_orchestrator.adapters.local_architect import (
        MAX_CANONICAL_INPUT_CHARS,
        MAX_MODEL_INPUT_CHARS,
    )

    root, _, _, _, _ = _ready(tmp_path)
    with patch(
        "agf_orchestrator.adapters.local_architect.ModelGatewayProvider.complete"
    ) as remote:
        too_large = LocalArchitectAdapter().execute(
            "A" * (MAX_CANONICAL_INPUT_CHARS + 1), str(root),
        )
        not_compactable = LocalArchitectAdapter().execute(
            "A" * (MAX_MODEL_INPUT_CHARS + 1), str(root),
        )
    remote.assert_not_called()
    assert too_large.invocation_verified is False and too_large.human_required
    assert not_compactable.invocation_verified is False
    assert not_compactable.transport_error == "LOCAL_ARCHITECT_GATEWAY_FAILED"


def test_large_tampered_canonical_source_is_never_sent_to_provider(tmp_path):
    from agf_orchestrator.adapters.local_architect import MAX_MODEL_INPUT_CHARS

    root, _, instruction, _, _ = _ready(tmp_path)
    source = json.loads(instruction.rsplit("\n", 1)[-1])
    # Increase size without recalculating the canonical request hash.
    source["assessment"]["repository_structure"] += [
        "other-" + str(i).zfill(5) + "_" + "x" * 55 + ".py" for i in range(1000)
    ]
    forged = instruction.rsplit("\n", 1)[0] + "\n" + json.dumps(source)
    assert len(forged) > MAX_MODEL_INPUT_CHARS
    with patch(
        "agf_orchestrator.adapters.local_architect.ModelGatewayProvider.complete"
    ) as remote:
        result = LocalArchitectAdapter().execute(forged, str(root))
    remote.assert_not_called()
    assert result.invocation_verified is False
    assert result.transport_error == "LOCAL_ARCHITECT_GATEWAY_FAILED"



def test_exact_owner_file_is_the_only_model_candidate(tmp_path):
    root, _, instruction, _, source = _ready(tmp_path)
    prompt, trusted = prepare_compact_request(instruction, str(root))
    assert 'Allowed evidenced paths: ["README.md"]' in prompt
    assert "tests/test_smoke.py" not in prompt
    assert trusted["objective"].startswith("Improve file:README.md")


def test_model_cannot_substitute_another_tracked_file_for_explicit_request(tmp_path):
    root, request, instruction, _, original = _ready(tmp_path)
    assert "tests/test_smoke.py" in request.assessment.repository_structure
    with pytest.raises(ValueError, match="compact proposal is invalid"):
        expand_compact_response(_response("tests/test_smoke.py"), original)


@pytest.mark.parametrize("explicit_goal", [
    "Improve file:missing.md with complete safe documentation",
    "Review file:.git/config without modifying anything",
    "Change file:../README.md and inspect the scope",
    "Improve file:README.md and path:tests/test_smoke.py together",
    "Review file:README.md* and show the output",
])
def test_invalid_owner_scoped_file_never_reaches_the_local_model(tmp_path, explicit_goal):
    from agf_orchestrator.architect_planning import architect_request_hash

    root, _, instruction, _, _ = _ready(tmp_path)
    original = json.loads(instruction.rsplit("\n", 1)[-1])
    original["objective"] = explicit_goal
    original["request_hash"] = architect_request_hash(original)
    host_request = instruction.rsplit("\n", 1)[0] + "\n" + json.dumps(original)
    with patch(
        "agf_orchestrator.adapters.local_architect.ModelGatewayProvider.complete"
    ) as transport:
        response = LocalArchitectAdapter().execute(host_request, str(root))
    transport.assert_not_called()
    assert response.invocation_verified is False
    assert response.transport_error == "LOCAL_ARCHITECT_GATEWAY_FAILED"


def test_provenance_binds_model_transport_and_compact_scope_contract(tmp_path):
    from pathlib import Path

    from agf_orchestrator.adapters.local_architect import local_architect_provenance

    canonical = local_architect_provenance()
    read = Path.read_bytes

    def changed(self):
        content = read(self)
        if self.name in {"local_architect_contract.py", "model_gateway.py"}:
            return content + b"\n# altered logic"
        return content

    with patch.object(Path, "read_bytes", changed):
        modified = local_architect_provenance()
    assert modified != canonical
    assert len(modified.split(":")[-1]) == 64
