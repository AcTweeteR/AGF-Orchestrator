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
