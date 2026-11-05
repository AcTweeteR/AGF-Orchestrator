"""Strict deterministic expansion of a local model's minimal advisory plan.

The untrusted model selects one evidenced path and explains a bounded intent.
AGF, not the model, assembles the required owner-bound Architect schema.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..architect_planning import architect_request_hash

_SAFE_SUFFIXES = frozenset({
    ".py", ".md", ".xml", ".json", ".js", ".ts", ".swift", ".toml", ".yaml", ".yml",
})


def _explicit_owner_file(objective: object) -> str | None:
    """A single file:/path: directive is exact scope, never an LLM suggestion."""
    if not isinstance(objective, str):
        raise ValueError("local Architect objective is not text")
    matches = re.findall(
        r"(?i)\b(?:file|path)\s*:\s*([^\s,]+)", objective,
    )
    if len(matches) > 1:
        raise ValueError("local Architect only permits one exact requested file")
    return matches[0] if matches else None


def prepare_compact_request(instruction: str, repository: str) -> tuple[str, dict] | None:
    if not instruction.startswith("Return exactly one JSON object matching this schema"):
        return None
    try:
        source = json.loads(instruction.rsplit("\n", 1)[-1])
        repo = source["repository"]
        assessment = source["assessment"]
        if (
            Path(repo["root"]).resolve() != Path(repository).resolve()
            or repo["head_sha"] != assessment["baseline_sha"]
            or source["request_hash"] != architect_request_hash(source)
        ):
            raise ValueError("Architect request source identity differs")
        allowed = [
            item for item in assessment["repository_structure"]
            if isinstance(item, str)
            and Path(item).suffix.lower() in _SAFE_SUFFIXES
            and item not in assessment["protected_paths"]
            and not item.startswith(".github/")
            and not any(segment in {".git", "secrets", "keys", "credentials"}
                        for segment in Path(item).parts)
        ]
        if not allowed:
            raise ValueError("no evidenced source files for Architect")
        explicit = _explicit_owner_file(source["objective"])
        if explicit is not None:
            if explicit not in allowed:
                raise ValueError("explicit owner file is absent or protected")
            allowed = [explicit]
        elif len(allowed) > 120:
            terms = source["objective"].casefold().split()
            allowed.sort(
                key=lambda p: (-sum(t in p.casefold() for t in terms), p)
            )
            allowed = allowed[:120]
        bindings = source.get("objective_validation_bindings", [])
        if any(
            not isinstance(b, dict) or set(b.get("task_ids", [])) != {"task-001"}
            for b in bindings
        ):
            raise ValueError("multi-task owner validation binding needs a fuller Architect")
    except (KeyError, ValueError, TypeError, IndexError, json.JSONDecodeError) as exc:
        raise ValueError("local Architect request is not canonically bound") from exc
    prompt = (
        "You are a read-only software Architect. Produce ONE JSON object with exactly "
        "these keys: path, objective, rationale, confidence. path MUST match exactly "
        "one entry of the supplied allowed file list. objective and rationale are "
        "short nonempty strings. confidence is a number between 0 and 1. "
        "Select one smallest justified target only. No source edits, no new files, "
        "no extra keys or markdown. Do not claim the objective is completed. "
        f"Objective: {source['objective'][:1200]}\n"
        "Allowed evidenced paths: " + json.dumps(allowed, ensure_ascii=False)
    )
    return prompt, source


def expand_compact_response(model_content: str, source: dict) -> str:
    try:
        reply = json.loads(model_content)
        if not isinstance(reply, dict) or set(reply) != {
            "path", "objective", "rationale", "confidence",
        }:
            raise ValueError("local Architect proposal is not the strict compact schema")
        name = reply["path"]
        objective = reply["objective"]
        rationale = reply["rationale"]
        confidence = reply["confidence"]
        assessment = source["assessment"]
        explicit = _explicit_owner_file(source["objective"])
        if explicit is not None and name != explicit:
            raise ValueError("local Architect changed the exact requested file")
        if (
            not isinstance(name, str)
            or name not in assessment["repository_structure"]
            or name in assessment["protected_paths"]
            or name.startswith(".github/")
            or Path(name).suffix.lower() not in _SAFE_SUFFIXES
            or any(part in {".git", "secrets", "keys", "credentials"}
                   for part in Path(name).parts)
            or not isinstance(objective, str) or not 5 <= len(objective) <= 1200
            or not isinstance(rationale, str) or not 5 <= len(rationale) <= 1200
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            raise ValueError("local Architect proposal exceeds observed scope")
        if not name or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("local Architect proposal path is invalid")
        bindings = source.get("objective_validation_bindings", ())
        if bindings:
            commands = list(dict.fromkeys(
                command for b in bindings for command in b["validation_commands"]
            ))
        else:
            # Untrusted output never supplies executable commands. The real
            # acceptance gate must still check the owner's validation criteria.
            commands = (
                ["python -m pytest"]
                if any(
                    path.startswith("tests/") and path.endswith(".py")
                    for path in assessment["repository_structure"]
                ) else ["git diff --check"]
            )
        if not commands:
            raise ValueError("local Architect has no owner validation commands")
        output = {
            "assessment_summary": f"AGF independently observed tracked path {name}.",
            "proposed_outcome": "BOUNDED_IMPLEMENTATION",
            "rationale": rationale,
            "confidence": confidence,
            "proposed_tasks": [{
                "objective": objective,
                "justification": rationale,
                "dependencies": [],
                "allowed_paths": [name],
                "prohibited_paths": list(assessment["protected_paths"]),
                "acceptance_criteria": [
                    "Satisfy the stated bounded objective within the selected file."
                ],
                "validation_requirements": commands,
                "evidence_references": [name],
                "risk_level": "medium",
            }],
            "architecture_implications": ["One evidenced tracked file; scope cannot expand."],
            "preliminary_risk_indicators": ["Conservative medium risk; review required."],
            "evidence_references": [name],
            "unresolved_unknowns": [],
        }
        return json.dumps(output, ensure_ascii=False, sort_keys=True)
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("local Architect compact proposal is invalid") from exc
