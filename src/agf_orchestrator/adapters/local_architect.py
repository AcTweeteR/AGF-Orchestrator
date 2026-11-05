"""Read-only local Architect transport; AGF remains the sole planning authority.

This class returns untrusted structured proposals, never opens target files,
selects paid fallbacks, or signs eligibility records.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ..model_gateway import ModelGatewayError, ModelGatewayProvider
from .codex import CodexProcessResult
from .local_architect_contract import expand_compact_response, prepare_compact_request

LOCAL_ARCHITECT_MODEL = "ollama/qwen3.5:9b-q4_K_M"
LOCAL_ARCHITECT_PROVIDER_ID = "provider-omniroute-local"
# Signed repository inventories may exceed a typical model input window.
# Compact the verified host request FIRST; never send this full payload to LLM.
MAX_CANONICAL_INPUT_CHARS = 1_000_000
MAX_MODEL_INPUT_CHARS = 64_000


def local_architect_provenance() -> str:
    """Bind eligibility to the complete local adapter, contract and gateway."""
    components = (
        Path(__file__),
        Path(__file__).with_name("local_architect_contract.py"),
        Path(__file__).resolve().parents[1] / "model_gateway.py",
    )
    digest = hashlib.sha256()
    for part in components:
        data = part.read_bytes()
        digest.update(part.name.encode("utf-8") + b"\0")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return f"runtime-canary:omniroute-local:{LOCAL_ARCHITECT_MODEL}:{digest.hexdigest()}"


class LocalArchitectAdapter:
    name = "omniroute-local-architect"

    def __init__(self, *, timeout: float = 90.0, model: str = LOCAL_ARCHITECT_MODEL):
        if model != LOCAL_ARCHITECT_MODEL:
            raise ValueError("only the exact approved zero-cost local model is supported")
        if timeout <= 0 or timeout > 90:
            raise ValueError("local Architect timeout exceeds validated budget")
        self.timeout = timeout
        self.model = model

    def execute(
        self, instruction: str, repository: str, *, sandbox: str = "read-only",
        output_schema=None,
    ) -> CodexProcessResult:
        if sandbox != "read-only" or output_schema is not None:
            return CodexProcessResult(
                "local Architect mode denied", 2, "", "read-only mode required",
                human_required=True,
            )
        # The full validated inventory is host-side evidence, never a model
        # context. A 3000-file project legitimately exceeds 64k characters.
        # Keep a separate bound on the canonical JSON processed by AGF.
        if (
            not Path(repository).resolve().is_dir()
            or len(instruction) > MAX_CANONICAL_INPUT_CHARS
        ):
            return CodexProcessResult(
                "local Architect input denied", 2, "", "invalid bounded request",
                human_required=True,
            )
        try:
            compact = prepare_compact_request(instruction, repository)
            payload = compact[0] if compact is not None else instruction
            if len(payload) > MAX_MODEL_INPUT_CHARS:
                raise ValueError("model request exceeds bounded compact context")
            observation = ModelGatewayProvider(
                token=os.environ.get("AGF_OMNIROUTE_KEY")
            ).complete(
                model=self.model, eligible_models=frozenset({self.model}),
                verified_zero_cost=True,
                max_output_tokens=240 if compact is not None else 256,
                messages=[
                    {"role": "system", "content": "Return one strict JSON object only. "
                     "Use only the supplied observed evidence; do not invent repository paths."},
                    {"role": "user", "content": payload},
                ],
            )
            normalized = (
                expand_compact_response(observation.content, compact[1])
                if compact is not None else observation.content
            )
        except (ModelGatewayError, ValueError, OSError) as exc:
            return CodexProcessResult(
                "local Architect gateway failed", 1, "",
                type(exc).__name__, transport_error="LOCAL_ARCHITECT_GATEWAY_FAILED",
                process_started=True, process_completed=True,
            )
        return CodexProcessResult(
            "local Architect read-only verified gateway",
            0, "bounded local JSON response", "", final_message=normalized,
            process_started=True, process_completed=True, invocation_verified=True,
            execution_observation={
                "source": "omniroute-local",
                "provider": observation.provider,
                "model": observation.model,
                "monetary_cost_eur": 0,
                "token_usage": {
                    "prompt_tokens": observation.prompt_tokens,
                    "completion_tokens": observation.completion_tokens,
                },
            },
        )
