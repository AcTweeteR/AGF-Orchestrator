"""Bounded, free-first harness. AGF Executor remains the governance authority."""

from __future__ import annotations

import json
from pathlib import Path

from ..model_gateway import ModelGatewayError, ModelGatewayProvider
from .codex import CodexAdapter, CodexProcessResult


class FreeFirstGatewayAdapter(CodexAdapter):
    name = "free-first"

    def __init__(self, *, model: str = "ollama/qwen3.5:9b-q4_K_M", timeout=300.0):
        super().__init__(timeout=timeout)
        if not model.startswith("ollama/"):
            raise ValueError("only approved local Ollama identifiers are supported")
        self.model = model

    @staticmethod
    def _target(instruction: str, root: Path) -> Path:
        start, stop = "\nAllowed paths:\n", "\nAcceptance criteria:\n"
        if start not in instruction or stop not in instruction:
            raise ValueError("approved AGF allowed-path section is missing")
        lines = instruction.split(start, 1)[1].split(stop, 1)[0].splitlines()
        names = [value.removeprefix("- ").strip() for value in lines]
        if len(names) != 1 or not names[0].endswith(".py"):
            raise ValueError("free-first governed adapter requires one Python file")
        value = Path(names[0])
        if value.is_absolute() or ".." in value.parts or ".git" in value.parts:
            raise ValueError("unapproved source path")
        target = root / value
        if (
            not target.is_file()
            or target.is_symlink()
            or not target.resolve().is_relative_to(root.resolve())
        ):
            raise ValueError("allowed source is not a safe regular file")
        return target

    def execute(
        self, instruction: str, repository: str, *, sandbox="workspace-write", output_schema=None
    ) -> CodexProcessResult:
        if sandbox != "workspace-write" or output_schema is not None:
            return CodexProcessResult(
                "unsupported free-first mode", 2, "", "mode denied", human_required=True
            )
        root = Path(repository).resolve()
        try:
            target = self._target(instruction, root)
            prompt = (
                "Return JSON object with content equal to the FULL corrected Python file. "
                "No markdown, no additional files. Preserve unrelated source.\n"
                f"Approved instruction:\n{instruction[-10000:]}\n"
                f"Current file:\n{target.read_text(encoding='utf-8')[:60000]}"
            )
            result = ModelGatewayProvider().complete(
                model=self.model,
                eligible_models=frozenset({self.model}),
                verified_zero_cost=True,
                messages=[
                    {
                        "role": "system",
                        "content": "Implement one Python file. Reply with JSON containing content.",
                    },
                    {"role": "user", "content": prompt},
                ],
            )
            payload = json.loads(result.content)
            code = payload.get("content") if isinstance(payload, dict) else None
            if not isinstance(code, str) or not code.strip() or len(code) > 120000:
                raise ValueError("gateway returned invalid source code")
            target.write_text(code, encoding="utf-8")
            return CodexProcessResult(
                f"free-first OmniRoute model={result.model}",
                0,
                "one scoped Python source file changed",
                "",
                final_message="AGF must validate code and check diff scope",
                process_started=True,
                process_completed=True,
                invocation_verified=True,
                execution_observation={
                    "source": "omniroute-ollama",
                    "authority_effect": "NONE",
                    "observed": {"provider": result.provider, "model": result.model},
                    "token_usage": {
                        "prompt_tokens": result.prompt_tokens,
                        "completion_tokens": result.completion_tokens,
                    },
                    "monetary_cost_eur": 0,
                },
            )
        except (ModelGatewayError, ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
            return CodexProcessResult(
                "free-first gateway failed",
                1,
                "",
                str(exc)[:500],
                transport_error="FREE_FIRST_GATEWAY_FAILED",
                process_started=True,
                process_completed=True,
            )
