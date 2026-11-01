"""Bounded Free-First provider for AGF's governed Executor/DeliveryPipeline.

This adapter is an untrusted Implementer: AGF alone authorizes task scope,
isolated worktrees, validation, review, commits and external delivery.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..model_gateway import ModelGatewayError, ModelGatewayProvider
from .codex import CodexAdapter, CodexProcessResult

SOURCE_SUFFIXES = {".py", ".xml", ".js", ".ts", ".json", ".yml", ".yaml", ".swift", ".toml"}


class FreeFirstGatewayAdapter(CodexAdapter):
    name = "free-first"

    def __init__(self, *, model="ollama/qwen3.5:9b-q4_K_M", timeout=300.0):
        super().__init__(timeout=timeout)
        if not model.startswith("ollama/"):
            raise ValueError("only an explicit zero-cost local Ollama model is permitted")
        self.model = model

    @staticmethod
    def _targets(instruction: str, root: Path) -> dict[str, Path]:
        start, stop = "\nAllowed paths:\n", "\nAcceptance criteria:\n"
        if start not in instruction or stop not in instruction:
            raise ValueError("approved AGF allowed-path section is missing")
        lines = instruction.split(start, 1)[1].split(stop, 1)[0].splitlines()
        names = [value.removeprefix("- ").strip() for value in lines]
        if len(names) < 1 or len(names) > 4 or len(set(names)) != len(names):
            raise ValueError("free-first accepts one to four exact scoped source files")
        targets: dict[str, Path] = {}
        for name in names:
            value = Path(name)
            if (
                value.is_absolute()
                or ".." in value.parts
                or ".git" in value.parts
                or value.suffix.lower() not in SOURCE_SUFFIXES
            ):
                raise ValueError("unapproved source path or file format")
            path = root / value
            if (
                not path.is_file()
                or path.is_symlink()
                or not path.resolve().is_relative_to(root.resolve())
                or path.stat().st_size > 1000000
            ):
                raise ValueError("allowed source is not a safe regular file")
            targets[name] = path
        return targets

    @staticmethod
    def _patch_sources(sources: dict[str, str], response: dict) -> dict[str, str]:
        edits = response.get("changes")
        updated = sources.copy()
        if isinstance(edits, list) and 1 <= len(edits) <= 12:
            for edit in edits:
                if not isinstance(edit, dict):
                    raise ValueError("invalid source patch")
                name = edit.get("file")
                find = edit.get("find")
                replace = edit.get("replace")
                if (
                    name not in sources
                    or not isinstance(find, str)
                    or not find
                    or not isinstance(replace, str)
                    or len(replace) > 30000
                ):
                    raise ValueError("unauthorized patch")
                if updated[name].count(find) != 1:
                    raise ValueError("patch anchor is missing or ambiguous")
                updated[name] = updated[name].replace(find, replace, 1)
        elif (
            len(sources) == 1
            and isinstance(response.get("content"), str)
            and response["content"].strip()
        ):
            name = next(iter(sources))
            updated[name] = response["content"]
        else:
            raise ValueError("model response lacks scoped source changes")
        if all(updated[name] == sources[name] for name in sources):
            raise ValueError("model produced no effective changes")
        return updated

    def execute(
        self, instruction: str, repository: str, *, sandbox="workspace-write", output_schema=None
    ) -> CodexProcessResult:
        if sandbox != "workspace-write" or output_schema is not None:
            return CodexProcessResult(
                "unsupported free-first mode", 2, "", "mode denied", human_required=True
            )
        root = Path(repository).resolve()
        try:
            targets = self._targets(instruction, root)
            sources = {name: path.read_text(encoding="utf-8") for name, path in targets.items()}
            # The model gets only bounded snippets; the exact patch is applied
            # against full original files by this program, not executed as code.
            segments = []
            for name, content in sources.items():
                excerpt = content[:3200] if len(content) > 3200 else content
                segments.append(f"FILE {name}:\n{excerpt}")
            prompt = (
                "Modify only approved files. Reply with one JSON object with a "
                "changes array of {file,find,replace}. The find substring must "
                "match exactly once in the original supplied source. Never "
                "create additional files or change tests. For a single file, "
                "a content field containing its complete replacement is also accepted."
                f"\nApproved task:\n{instruction[-10000:]}"
                "\nCurrent source excerpts:\n" + "\n\n".join(segments)
            )
            # Smaller local models occasionally return JSON patches with
            # unapproved paths or ambiguous anchors. Retry the same scoped task
            # with precise feedback; never broaden the allowlist.
            last_error = ""
            used_tokens = {"prompt_tokens": 0, "completion_tokens": 0}
            for attempt in range(3):
                query = prompt
                if last_error:
                    query += (
                        "\nPREVIOUS ATTEMPT INVALID: "
                        + last_error[:350]
                        + "\nUse ONLY these exact file names: "
                        + ", ".join(targets)
                        + '\nExample: {"changes":[{"file":"'
                        + next(iter(targets))
                        + '","find":"old exact text",'
                        + '"replace":"new text"}]}'
                    )
                result = ModelGatewayProvider().complete(
                    model=self.model,
                    eligible_models=frozenset({self.model}),
                    verified_zero_cost=True,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Implement only the approved source files. JSON only. "
                                "Never invent filenames; use exact scoped paths."
                            ),
                        },
                        {"role": "user", "content": query},
                    ],
                )
                used_tokens["prompt_tokens"] += result.prompt_tokens
                used_tokens["completion_tokens"] += result.completion_tokens
                try:
                    response = json.loads(result.content)
                    if not isinstance(response, dict):
                        raise ValueError("model returned non-object JSON")
                    updated = self._patch_sources(sources, response)
                    break
                except (ValueError, KeyError, TypeError) as exc:
                    last_error = str(exc)
            else:
                raise ValueError("bounded local-model repair failed: " + last_error)
            if any(len(value) > 1000000 for value in updated.values()):
                raise ValueError("generated file is too large")
            for name, content in updated.items():
                if content != sources[name]:
                    targets[name].write_text(content, encoding="utf-8")
            return CodexProcessResult(
                f"free-first OmniRoute model={result.model}",
                0,
                f"{sum(updated[n] != sources[n] for n in updated)} scoped files updated",
                "",
                final_message="AGF must validate all changes, scope and tests",
                process_started=True,
                process_completed=True,
                invocation_verified=True,
                execution_observation={
                    "source": "omniroute-ollama",
                    "authority_effect": "NONE",
                    "observed": {"provider": result.provider, "model": result.model},
                    "token_usage": {
                        "prompt_tokens": used_tokens["prompt_tokens"],
                        "completion_tokens": used_tokens["completion_tokens"],
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
