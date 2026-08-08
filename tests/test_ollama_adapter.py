import json
import os

import pytest

from agf_orchestrator.adapters.ollama import (
    LocalModel,
    OllamaProviderError,
    detect_local_model,
    local_ollama_environment,
)


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(
            {
                "models": [
                    {"name": "qwen3.5:9b-q4_K_M", "size": 6600000000, "digest": "sha256:test"}
                ]
            }
        ).encode()


def test_detects_preferred_installed_model(monkeypatch):
    monkeypatch.setattr(
        "agf_orchestrator.adapters.ollama.urlopen", lambda *_args, **_kwargs: Response()
    )
    model = detect_local_model()
    assert model == LocalModel("qwen3.5:9b-q4_K_M", 6600000000, "sha256:test")


def test_missing_model_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "agf_orchestrator.adapters.ollama.urlopen", lambda *_args, **_kwargs: Response()
    )
    with pytest.raises(OllamaProviderError, match="not installed"):
        detect_local_model("missing:latest")


def test_environment_is_scoped_and_uses_openai_compatible_endpoint(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    model = LocalModel("qwen3.5:9b-q4_K_M", 6600000000, "sha256:test")
    with local_ollama_environment(model):
        assert os.environ["LLM_API_KEY"] == "local-ollama"
        assert os.environ["LLM_MODEL"] == "openai/qwen3.5:9b-q4_K_M"
        assert os.environ["LLM_BASE_URL"] == "http://127.0.0.1:11434/v1"
    assert "LLM_MODEL" not in os.environ


def test_invalid_inventory_fails_closed(monkeypatch):
    class InvalidResponse(Response):
        def read(self):
            return b"{\"models\": {}}"

    monkeypatch.setattr(
        "agf_orchestrator.adapters.ollama.urlopen", lambda *_args, **_kwargs: InvalidResponse()
    )
    with pytest.raises(OllamaProviderError, match="invalid model inventory"):
        detect_local_model()


def test_remote_endpoint_is_rejected_before_network_access(monkeypatch):
    monkeypatch.setenv("AGF_OLLAMA_URL", "https://example.invalid")
    with pytest.raises(OllamaProviderError, match="local loopback"):
        detect_local_model()
