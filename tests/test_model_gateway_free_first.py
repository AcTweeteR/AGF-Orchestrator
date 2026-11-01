"""Gateway boundary tests: no implicit billing, no unknown fallback, provenance."""

import json
from io import BytesIO
from unittest.mock import patch

import pytest

from agf_orchestrator.model_gateway import ModelGatewayError, ModelGatewayProvider


class Response(BytesIO):
    pass


def test_requires_explicit_eligible_verified_zero_cost_model():
    gw = ModelGatewayProvider()
    messages = [{"role": "user", "content": "hi"}]
    with pytest.raises(ModelGatewayError, match="approved"):
        gw.complete(model="auto", messages=messages, eligible_models=frozenset())
    with pytest.raises(ModelGatewayError, match="evidence"):
        gw.complete(
            model="ollama/qwen", messages=messages, eligible_models=frozenset({"ollama/qwen"})
        )


def test_rejects_external_gateway():
    with pytest.raises(ModelGatewayError, match="loopback"):
        ModelGatewayProvider("https://untrusted.example.com")


def test_valid_zero_cost_completion_and_token_evidence():
    expected = {
        "model": "ollama/qwen",
        "choices": [{"message": {"content": '{"content":"ok"}'}}],
        "usage": {"prompt_tokens": 13, "completion_tokens": 4},
    }
    with patch(
        "agf_orchestrator.model_gateway.urlopen",
        return_value=Response(json.dumps(expected).encode()),
    ):
        result = ModelGatewayProvider().complete(
            model="ollama/qwen",
            messages=[{"role": "user", "content": "hi"}],
            eligible_models=frozenset({"ollama/qwen"}),
            verified_zero_cost=True,
        )
    assert result.provider == "ollama"
    assert result.model == "ollama/qwen"
    assert (result.prompt_tokens, result.completion_tokens) == (13, 4)


def test_gateway_substitution_fails_closed():
    unexpected = {"model": "expensive/model", "choices": [{"message": {"content": "surprise"}}]}
    with patch(
        "agf_orchestrator.model_gateway.urlopen",
        return_value=Response(json.dumps(unexpected).encode()),
    ):
        with pytest.raises(ModelGatewayError, match="different"):
            ModelGatewayProvider().complete(
                model="ollama/qwen",
                messages=[{"role": "user", "content": "hi"}],
                eligible_models=frozenset({"ollama/qwen"}),
                verified_zero_cost=True,
            )


def test_local_ollama_bare_model_id_is_accepted_as_exact_alias():
    expected = {
        "model": "qwen3.5:9b-q4_K_M",
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
    }
    with patch(
        "agf_orchestrator.model_gateway.urlopen",
        return_value=Response(json.dumps(expected).encode()),
    ):
        result = ModelGatewayProvider().complete(
            model="ollama/qwen3.5:9b-q4_K_M",
            messages=[{"role": "user", "content": "hi"}],
            eligible_models=frozenset({"ollama/qwen3.5:9b-q4_K_M"}),
            verified_zero_cost=True,
        )
    assert result.provider == "ollama"
    assert result.model == "ollama/qwen3.5:9b-q4_K_M"
