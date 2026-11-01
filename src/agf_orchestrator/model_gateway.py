"""Provider-neutral read/route boundary for an explicitly eligible model gateway.

This module does not grant AGF task authority, approve providers or authorize
billing. A caller must supply its own allowed models and verified free/paid
policy before dispatch. Unknown or substituted effective models fail closed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class ModelGatewayError(RuntimeError):
    """Gateway routing or provenance is unverified."""


@dataclass(frozen=True)
class GatewayResult:
    model: str
    provider: str
    content: str
    prompt_tokens: int
    completion_tokens: int
    gateway: str = "omniroute"


class ModelGatewayProvider:
    """Read-only provider catalog and allowlisted chat completions interface."""

    def __init__(self, base_url: str = "http://127.0.0.1:20128", *, token: str | None = None):
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ModelGatewayError("pilot gateway must use local loopback HTTP")
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _read(self, path: str, payload: dict | None = None) -> dict:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = Request(
            self.base_url + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=90) as response:
                value = json.load(response)
        except (ValueError, OSError) as exc:
            raise ModelGatewayError("model gateway request failed") from exc
        if not isinstance(value, dict):
            raise ModelGatewayError("gateway returned non-object payload")
        return value

    def health(self) -> bool:
        return self._read("/api/health").get("status") == "ok"

    def catalog(self) -> list[str]:
        response = self._read("/v1/models")
        items = response.get("data")
        if not isinstance(items, list):
            raise ModelGatewayError("model catalog invalid")
        return [m["id"] for m in items if isinstance(m, dict) and isinstance(m.get("id"), str)]

    def complete(
        self,
        *,
        model: str,
        messages: list[dict],
        eligible_models: frozenset[str],
        verified_zero_cost: bool = False,
    ) -> GatewayResult:
        if not model or model == "auto" or model not in eligible_models:
            raise ModelGatewayError("requested model is not AGF-approved")
        if not verified_zero_cost:
            raise ModelGatewayError("zero-cost model eligibility evidence missing")
        if not messages:
            raise ModelGatewayError("empty messages")
        response = self._read(
            "/v1/chat/completions",
            {
                "model": model,
                "messages": messages,
                "temperature": 0,
                "max_tokens": 2400,
                "think": False,
                "response_format": {"type": "json_object"},
                "stream": False,
            },
        )
        effective = response.get("model")
        # Local Ollama passthrough reports the bare upstream model name.
        allowed_effective = {model}
        if model.startswith("ollama/"):
            allowed_effective.add(model.removeprefix("ollama/"))
        if effective not in allowed_effective:
            raise ModelGatewayError("gateway selected a different effective model")
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelGatewayError("gateway response lacks content") from exc
        if not isinstance(content, str):
            raise ModelGatewayError("gateway response text invalid")
        usage = response.get("usage") or {}
        return GatewayResult(
            model=model,
            provider=model.split("/", 1)[0] if "/" in model else "unknown",
            content=content,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )
