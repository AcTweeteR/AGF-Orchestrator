"""Optional local Ollama provider for OpenHands."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, replace
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .openhands import OpenHandsSDKAdapter

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_LOCAL_MODEL = "qwen3.5:9b-q4_K_M"


class OllamaProviderError(RuntimeError):
    """Ollama or the requested local model is unavailable."""


@dataclass(frozen=True)
class LocalModel:
    name: str
    size: int
    digest: str


def _endpoint() -> str:
    endpoint = os.environ.get("AGF_OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/")
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or not parsed.netloc
    ):
        raise OllamaProviderError("Ollama endpoint must be local loopback HTTP")
    return endpoint


def detect_local_model(preferred: str | None = None) -> LocalModel:
    """Detect the approved local model without invoking a shell."""
    requested = preferred or os.environ.get("AGF_LOCAL_MODEL", DEFAULT_LOCAL_MODEL)
    request = Request(f"{_endpoint()}/api/tags", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=5.0) as response:
            payload = json.load(response)
    except (OSError, URLError, ValueError) as exc:
        raise OllamaProviderError("local Ollama endpoint is unavailable") from exc
    models = payload.get("models")
    if not isinstance(models, list):
        raise OllamaProviderError("Ollama returned an invalid model inventory")
    for item in models:
        if isinstance(item, dict) and item.get("name") == requested:
            return LocalModel(
                requested,
                int(item.get("size") or 0),
                str(item.get("digest") or ""),
            )
    raise OllamaProviderError(f"required local model is not installed: {requested}")


@contextmanager
def local_ollama_environment(model: LocalModel):
    """Set only ephemeral OpenHands variables for one local invocation."""
    values = {
        "LLM_API_KEY": "local-ollama",
        "LLM_MODEL": f"openai/{model.name}",
        "LLM_BASE_URL": f"{_endpoint()}/v1",
    }
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class OllamaOpenHandsAdapter(OpenHandsSDKAdapter):
    """OpenHands adapter that discovers and uses one local Ollama model."""

    name = "openhands"

    def execute(self, instruction: str, repository: str, *, sandbox: str = "workspace-write"):
        try:
            model = detect_local_model()
        except OllamaProviderError as exc:
            from .codex import CodexProcessResult

            return CodexProcessResult(
                "ollama local provider discovery",
                None,
                "",
                str(exc),
                human_required=True,
                transport_error="OLLAMA_PROVIDER_UNAVAILABLE",
            )
        with local_ollama_environment(model):
            result = super().execute(instruction, repository, sandbox=sandbox)
        return replace(
            result,
            command_summary=f"ollama model={model.name}; {result.command_summary}",
        )
