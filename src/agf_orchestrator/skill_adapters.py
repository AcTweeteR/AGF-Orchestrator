"""Import Agent Skill documents as untrusted inputs to governed procedures."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .capability_extensions import InvocationPolicy, ProcedureProfile, seal
from .risk_models import RiskLevel


class SkillAdapterError(ValueError):
    """Raised when a skill document cannot be imported safely."""


@dataclass(frozen=True)
class SkillDocument:
    name: str
    description: str
    body: str
    source_sha256: str
    model_invocation_disabled: bool


@dataclass(frozen=True)
class SkillGovernanceEnvelope:
    project_id: str
    profile_version: int
    capabilities: tuple[str, ...]
    max_risk: RiskLevel
    allowed_paths: tuple[str, ...]
    provider_requirements: tuple[str, ...]
    required_evidence: tuple[str, ...]
    observed_at: str
    expires_at: str | None
    invocation_policy: InvocationPolicy


_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_MAX_SKILL_BYTES = 256_000
_ALLOWED_FRONTMATTER = frozenset(
    {"name", "description", "disable-model-invocation", "argument-hint"}
)


def parse_skill_markdown(content: str) -> SkillDocument:
    """Parse a bounded SKILL.md subset without trusting it as governance metadata."""
    if not isinstance(content, str) or not content.strip():
        raise SkillAdapterError("skill document is empty")
    if len(content.encode("utf-8")) > _MAX_SKILL_BYTES:
        raise SkillAdapterError("skill document exceeds bounded size")
    lines = content.splitlines()
    if len(lines) < 4 or lines[0].strip() != "---":
        raise SkillAdapterError("skill document requires YAML-style frontmatter")
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration as exc:
        raise SkillAdapterError("skill frontmatter is not terminated") from exc
    metadata: dict[str, str] = {}
    for raw in lines[1:end]:
        if not raw.strip():
            continue
        if ":" not in raw:
            raise SkillAdapterError("skill frontmatter line is invalid")
        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key not in _ALLOWED_FRONTMATTER:
            raise SkillAdapterError(f"unsupported skill frontmatter field: {key}")
        if key in metadata:
            raise SkillAdapterError(f"duplicate skill frontmatter field: {key}")
        metadata[key] = _unquote(value)
    name = metadata.get("name", "")
    description = metadata.get("description", "")
    if not _NAME.fullmatch(name):
        raise SkillAdapterError("skill name is invalid")
    if not description or len(description) > 2000:
        raise SkillAdapterError("skill description is invalid")
    disabled_raw = metadata.get("disable-model-invocation", "false").lower()
    if disabled_raw not in {"true", "false"}:
        raise SkillAdapterError("disable-model-invocation must be boolean")
    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        raise SkillAdapterError("skill body is empty")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return SkillDocument(
        name=name,
        description=description,
        body=body,
        source_sha256=digest,
        model_invocation_disabled=disabled_raw == "true",
    )


def skill_to_procedure(
    skill: SkillDocument,
    envelope: SkillGovernanceEnvelope,
) -> ProcedureProfile:
    """Bind untrusted skill instructions to separately supplied AGF governance."""
    procedure_id = f"procedure-skill-{skill.name}"
    provenance = f"agent-skill:{skill.name}:sha256:{skill.source_sha256}"
    profile = seal(
        ProcedureProfile(
            schema_version="1.0",
            procedure_id=procedure_id,
            project_id=envelope.project_id,
            profile_version=envelope.profile_version,
            capabilities=envelope.capabilities,
            max_risk=envelope.max_risk,
            allowed_paths=envelope.allowed_paths,
            provider_requirements=envelope.provider_requirements,
            required_evidence=envelope.required_evidence,
            invocation_policy=envelope.invocation_policy,
            provenance_source=provenance,
            observed_at=envelope.observed_at,
            expires_at=envelope.expires_at,
            profile_sha256="",
        )
    )
    profile.validate(now=envelope.observed_at)
    return profile


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
