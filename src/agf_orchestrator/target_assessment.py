"""Evidence-bound target assessment and architecture decomposition.

This module is deliberately read-only with respect to the target repository.
It turns a repository baseline into immutable evidence and only accepts an
explicit, evidence-backed architecture proposal for executable work.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .models import PlanStatus, RepositoryContext, Task
from .remote_identity import RemoteIdentityError, canonical_remote_identity


class AssessmentError(ValueError):
    """Raised when assessment or decomposition cannot be proven safe."""


class DeterministicArchitect:
    """Safe architect for explicitly named, evidence-backed paths.

    Provider-backed architects can implement the same ``propose`` contract;
    this fallback never invents a path from repository metadata.
    """

    provider_selection = {
        "mode": "deterministic-architect",
        "status": "SUPPORTED",
        "capabilities": ["assessment", "scope-decomposition"],
    }

    def propose(self, goal: str, assessment: "TargetAssessment") -> dict[str, Any] | None:
        match = re.search(r"(?:path|file)\s*:\s*([^\s,]+)", goal, re.IGNORECASE)
        if match is None:
            return None
        path = match.group(1).replace("\\", "/")
        return {
            "rationale": "The objective explicitly names an evidenced target path.",
            "tasks": [{
                "task_id": "task-001",
                "title": "Implement the explicitly scoped objective",
                "objective": " ".join(goal.split()),
                "allowed_paths": [path],
                "dependencies": [],
                "acceptance_criteria": ["The explicitly scoped objective is addressed."],
                "validation_commands": ["python -m pytest"],
                "risk_level": "medium",
                "assigned_role": "Implementer",
                "requirement_refs": [],
            }],
            "prohibited_paths": [".git", "secrets", "keys", "credentials"],
            "required_evidence": ["assessment evidence", "validation results"],
            "validation_requirements": ["python -m pytest"],
        }


_SECRET_NAME = re.compile(
    r"(?i)(^|[._-])(env|secret|credential|credentials|token|key|password)([._-]|$)"
)
_SENSITIVE_DIRS = frozenset({
    "secrets", "keys", "credentials", "private", "certificates",
})
_PROTECTED_PREFIXES = (".git",)
_IGNORED_DIRS = frozenset({
    ".venv", "venv", "node_modules", "dist", "build", "coverage", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "__pycache__",
})


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AssessmentError(f"git {' '.join(args)} failed") from exc
    return result.stdout.strip()


@dataclass(frozen=True)
class TargetAssessment:
    schema_version: str
    project_id: str
    repository_root: str
    baseline_sha: str
    repository_structure: tuple[str, ...]
    project_type: str
    languages: tuple[str, ...]
    architecture_markers: tuple[str, ...]
    tests_validators: tuple[str, ...]
    ci_markers: tuple[str, ...]
    governance_files: tuple[str, ...]
    documentation: tuple[str, ...]
    default_branch: str
    clean: bool
    protected_paths: tuple[str, ...]
    unknowns: tuple[str, ...]
    evidence_hash: str
    repository_origin: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self, repository: RepositoryContext) -> None:
        if self.schema_version != "1.0":
            raise AssessmentError("assessment schema is unsupported")
        if self.repository_root != str(Path(repository.root).resolve()):
            raise AssessmentError("assessment repository binding does not match")
        if self.baseline_sha != repository.head_sha:
            raise AssessmentError("assessment baseline SHA is stale")
        if self.default_branch != repository.branch:
            raise AssessmentError("assessment branch binding does not match")
        if self.clean != repository.clean:
            raise AssessmentError("assessment cleanliness does not match")
        if self.repository_origin is None:
            raise AssessmentError("assessment repository origin is missing")
        try:
            if canonical_remote_identity(self.repository_origin) != canonical_remote_identity(
                repository.origin
            ):
                raise AssessmentError("assessment repository origin does not match")
        except RemoteIdentityError as exc:
            raise AssessmentError("assessment repository origin is invalid") from exc
        if self.evidence_hash != _hash_payload(self.payload()):
            raise AssessmentError("assessment evidence hash does not match evidence")

    def payload(self) -> dict[str, Any]:
        result = self.to_dict()
        result.pop("evidence_hash")
        return result


@dataclass(frozen=True)
class ArchitectureDecision:
    schema_version: str
    status: str
    requires_architect: bool
    rationale: str
    bounded_objective: str
    target_project_id: str
    baseline_sha: str
    assessment_hash: str
    delivery_branch: str
    tasks: tuple[dict[str, Any], ...]
    prohibited_paths: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    required_evidence: tuple[str, ...]
    validation_requirements: tuple[str, ...]
    risk_indicators: tuple[str, ...]
    provider_selection: dict[str, Any]
    planning_outcome: str = "BOUNDED_IMPLEMENTATION"
    scope_authorization_id: str | None = None
    scope_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self, assessment: TargetAssessment) -> None:
        if self.schema_version != "1.0" or self.target_project_id != assessment.project_id:
            raise AssessmentError("architecture evidence binding is invalid")
        if self.baseline_sha != assessment.baseline_sha:
            raise AssessmentError("architecture evidence is stale")
        if self.assessment_hash != assessment.evidence_hash:
            raise AssessmentError("architecture assessment hash does not match")
        if self.status not in {"approved", "BLOCKED"}:
            raise AssessmentError("architecture status is invalid")
        if self.scope_authorization_id is not None and not self.scope_authorization_id.startswith(
            "scope-"
        ):
            raise AssessmentError("architecture scope authorization binding is invalid")
        if self.scope_id is not None and not self.scope_id.startswith("phase-"):
            raise AssessmentError("architecture scope identity is invalid")
        if self.planning_outcome not in {"BOUNDED_IMPLEMENTATION", "NO_JUSTIFIED_WORK", "BLOCKED"}:
            raise AssessmentError("architecture planning outcome is invalid")
        if self.status == "approved":
            if self.requires_architect or not self.tasks:
                raise AssessmentError("approved architecture must produce executable tasks")
            for item in self.tasks:
                if str(item.get("risk_level", "")).lower() not in {
                    "low", "medium", "high", "critical"
                }:
                    raise AssessmentError("architecture task risk is invalid")
                paths = item.get("allowed_paths", [])
                if not paths or any(path in assessment.protected_paths for path in paths):
                    raise AssessmentError("architecture contains unsafe or empty allowed paths")
                for path in paths:
                    _validate_scoped_path(path)
                    if path not in assessment.repository_structure:
                        raise AssessmentError(f"architecture path lacks evidence: {path}")

    def consume_scope_authorization(
        self, authorization: Any, project: Any, repository: str, *, session_id: str | None = None
    ) -> None:
        """Verify, but never replace, the Owner scope decision used by this architecture."""
        if not self.scope_authorization_id or not self.scope_id:
            raise AssessmentError("architecture has no scope authorization binding")
        if authorization.authorization_id != self.scope_authorization_id:
            raise AssessmentError("architecture scope authorization identity mismatch")
        from .scope_authorization import ScopeAuthorizationError, verify_scope_authorization
        try:
            verify_scope_authorization(
                authorization, project, repository, target_sha=self.baseline_sha,
                scope_id=self.scope_id, session_id=session_id,
            )
        except ScopeAuthorizationError as exc:
            raise AssessmentError("architecture scope authorization is invalid") from exc


def _hash_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _validate_scoped_path(value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise AssessmentError("architecture allowed path is invalid")
    path = value.replace("\\", "/")
    parts = PurePosixPath(path).parts
    if (
        path in {".", "*"}
        or path.startswith("/")
        or ".." in parts
        or any("*" in part or "?" in part for part in parts)
        or ".git" in parts
    ):
        raise AssessmentError("architecture allowed paths must be concrete and bounded")


def assess_repository(
    repository: RepositoryContext,
    project_id: str,
    *,
    registered_project: Any | None = None,
) -> TargetAssessment:
    """Inspect names and repository metadata only; never read secret contents."""
    if registered_project is None:
        raise AssessmentError("registered project binding is required")
    root = Path(repository.root).resolve()
    if registered_project is not None:
        if registered_project.project_id != project_id:
            raise AssessmentError("assessment project binding does not match")
        try:
            if str(root) != str(Path(registered_project.repository_root).resolve()):
                raise AssessmentError("assessment repository root does not match registration")
            if canonical_remote_identity(repository.origin) != canonical_remote_identity(
                registered_project.origin_url
            ):
                raise AssessmentError("assessment repository origin does not match registration")
        except RemoteIdentityError as exc:
            raise AssessmentError("assessment repository origin is invalid") from exc
    files: list[str] = []
    protected: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        parts = relative.split("/")
        if any(part in _IGNORED_DIRS for part in parts):
            continue
        if any(part == ".git" or part.startswith(".git") for part in parts):
            continue
        files.append(relative)
        if any(
            part.startswith(_PROTECTED_PREFIXES)
            or part in _SENSITIVE_DIRS
            or _SECRET_NAME.search(part)
            for part in parts
        ):
            protected.append(relative)
    suffixes = {Path(item).suffix.lower() for item in files}
    languages = tuple(sorted({
        ".py": "python", ".js": "javascript", ".ts": "typescript", ".go": "go",
        ".rs": "rust", ".java": "java", ".rb": "ruby", ".md": "markdown",
        ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    }.get(suffix, "unknown") for suffix in suffixes))
    markers = tuple(item for item in files if item in {
        "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "Makefile",
        "README.md", "AGENTS.md", "CONTRIBUTING.md", "docs/CONSTITUTION.md",
    } or item.startswith(".github/workflows/"))
    tests = tuple(
        item for item in files
        if item.startswith(("tests/", "test/")) or Path(item).name.startswith("test_")
    )
    ci = tuple(
        item for item in files
        if item.startswith(".github/workflows/")
        or Path(item).name in {".gitlab-ci.yml", "Jenkinsfile"}
    )
    governance = tuple(
        item for item in files
        if Path(item).name in {"AGENTS.md", "CONTRIBUTING.md", "CODEOWNERS", "SECURITY.md"}
        or "/adr/" in f"/{item}"
    )
    docs = tuple(item for item in files if Path(item).suffix.lower() in {".md", ".rst", ".adoc"})
    if "pyproject.toml" in files:
        project_type = "python"
    elif "package.json" in files:
        project_type = "javascript"
    elif "Cargo.toml" in files:
        project_type = "rust"
    elif "go.mod" in files:
        project_type = "go"
    else:
        project_type = "unknown"
    unknowns = []
    if project_type == "unknown":
        unknowns.append("project type is not established by a recognized manifest")
    if not tests:
        unknowns.append("tests or validators were not found by repository names")
    if not ci:
        unknowns.append("CI configuration was not found by repository names")
    if not governance:
        unknowns.append("governance files were not found by repository names")
    if not docs:
        unknowns.append("documentation was not found by repository names")
    if not any(Path(item).name.upper() in {"ROADMAP", "ISSUES"} for item in files):
        unknowns.append("roadmap and issue evidence is unavailable locally")
    payload = {
        "schema_version": "1.0",
        "project_id": project_id, "repository_root": str(root), "baseline_sha": repository.head_sha,
        "repository_origin": repository.origin,
        "repository_structure": files, "project_type": project_type, "languages": languages,
        "architecture_markers": markers, "tests_validators": tests, "ci_markers": ci,
        "governance_files": governance, "documentation": docs, "default_branch": repository.branch,
        "clean": repository.clean, "protected_paths": sorted(set(protected)), "unknowns": unknowns,
    }
    return TargetAssessment(evidence_hash=_hash_payload(payload), **payload)


def derive_architecture(
    goal: str,
    repository: RepositoryContext,
    assessment: TargetAssessment,
    *,
    proposal: dict[str, Any] | None = None,
    provider_selection: dict[str, Any] | None = None,
    scope_authorization_id: str | None = None,
    scope_id: str | None = None,
) -> ArchitectureDecision:
    """Accept only an explicit bounded proposal; never invent wildcard scope."""
    assessment.validate(repository)
    branch_slug = re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")[:40]
    branch = f"agf/assess-{branch_slug or 'target'}"
    provider = provider_selection or {"mode": "deterministic-read-only", "status": "SUPPORTED"}
    if not assessment.clean:
        return ArchitectureDecision(
            "1.0", "BLOCKED", True,
            "A clean target baseline is required before architecture can scope mutation.",
            " ".join(goal.split()), assessment.project_id, assessment.baseline_sha,
            assessment.evidence_hash, branch, (), (), (),
            ("clean target baseline",), ("scope review",),
            ("dirty baseline",), provider,
            planning_outcome="BLOCKED", scope_authorization_id=scope_authorization_id,
            scope_id=scope_id,
        )
    if proposal is None:
        outcome = str(provider.get("planning_outcome", "BLOCKED"))
        return ArchitectureDecision(
            "1.0", "BLOCKED", True,
            "No target-specific implementation proposal is justified by assessment evidence.",
            " ".join(goal.split()), assessment.project_id, assessment.baseline_sha,
            assessment.evidence_hash, branch, (), (), (),
            ("assessment evidence",), ("scope review",),
            ("missing bounded proposal",), provider, outcome,
        )
    tasks = tuple(proposal.get("tasks", ()))
    decision = ArchitectureDecision(
        "1.0", "approved", False, str(proposal.get("rationale", "bounded evidence-backed scope")),
        " ".join(goal.split()), assessment.project_id, assessment.baseline_sha,
        assessment.evidence_hash,
        branch, tasks, tuple(sorted(set(proposal.get("prohibited_paths", ())))),
        tuple(proposal.get("acceptance_criteria", ())),
        tuple(proposal.get("required_evidence", ())),
        tuple(proposal.get("validation_requirements", ("python -m pytest",))),
        tuple(proposal.get("risk_indicators", ())), provider,
        str(provider.get("planning_outcome", "BOUNDED_IMPLEMENTATION")),
        scope_authorization_id,
        scope_id,
    )
    decision.validate(assessment)
    return decision


def architecture_to_tasks(decision: ArchitectureDecision) -> list[Task]:
    return [
        Task(**{**item, "status": PlanStatus(item.get("status", "READY"))})
        for item in decision.tasks
    ]
