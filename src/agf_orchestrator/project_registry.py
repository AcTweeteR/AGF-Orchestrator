"""Filesystem-backed project registry with read-only repository inspection."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from .project_models import Project, ProjectPolicy, ProjectStatus, project_from_dict


class ProjectRegistryError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=True,
            shell=False,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "").strip() or str(exc)
        raise ProjectRegistryError(f"git {' '.join(args)} failed: {detail}") from exc
    return result.stdout.strip()


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    old_mode = None
    try:
        old_mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        pass
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, old_mode or 0o600)
        os.replace(temporary, path)
    except OSError:
        if temporary:
            temporary.unlink(missing_ok=True)
        raise


class ProjectRegistry:
    schema_version = "1.0"

    def __init__(self, state_dir: str | Path | None = None):
        configured = state_dir or os.environ.get("AGF_STATE_DIR") or "~/.agf-orchestrator"
        self.state_dir = Path(configured).expanduser().resolve()
        self.path = self.state_dir / "projects.json"

    def _load(self) -> list[Project]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != self.schema_version:
                raise ProjectRegistryError("HUMAN_REQUIRED: unsupported project registry schema")
            return [project_from_dict(item) for item in payload.get("projects", [])]
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ProjectRegistryError):
                raise
            raise ProjectRegistryError(f"HUMAN_REQUIRED: invalid project registry: {exc}") from exc

    def _save(self, projects: list[Project]) -> None:
        _atomic_write(
            self.path,
            {
                "schema_version": self.schema_version,
                "projects": [project.to_dict() for project in projects],
            },
        )

    def list(self) -> list[Project]:
        return sorted(self._load(), key=lambda item: (item.name, item.project_id))

    def get(self, name_or_id: str) -> Project:
        matches = [p for p in self._load() if p.name == name_or_id or p.project_id == name_or_id]
        if len(matches) != 1:
            raise ProjectRegistryError("project selection is missing or ambiguous")
        return matches[0]

    def add(
        self,
        name: str,
        repository: str | Path,
        *,
        policy: ProjectPolicy | None = None,
        metadata: dict | None = None,
        accept_duplicate_origin: bool = False,
    ) -> Project:
        requested = Path(repository).expanduser()
        root = requested.resolve(strict=True)
        if requested.is_symlink():
            raise ProjectRegistryError(
                "symlink or non-canonical repository path is not registrable"
            )
        if (
            root == self.state_dir
            or self.state_dir in root.parents
            or root in self.state_dir.parents
        ):
            raise ProjectRegistryError("repository must be outside AGF state directory")
        if root.name == ".git" or (root / ".git").is_file() and root.name == ".git":
            raise ProjectRegistryError(".git directory cannot be registered as a project")
        top = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
        if top != root:
            raise ProjectRegistryError("repository path must be the canonical repository root")
        branch = _git(root, "branch", "--show-current")
        if not branch:
            raise ProjectRegistryError("detached HEAD is not registrable")
        origin = _git(root, "config", "--get", "remote.origin.url")
        if not origin:
            raise ProjectRegistryError("origin remote is required")
        parsed = urlparse(origin)
        if parsed.username or parsed.password:
            raise ProjectRegistryError("origin must not contain embedded credentials")
        if parsed.scheme not in {"https", "ssh", "git", "file", ""}:
            raise ProjectRegistryError("unsupported origin scheme")
        selected_policy = policy or ProjectPolicy()
        if selected_policy.allowed_remote_hosts:
            host = parsed.hostname or ""
            if host not in selected_policy.allowed_remote_hosts:
                raise ProjectRegistryError("origin host is not allowed by project policy")
        head = _git(root, "rev-parse", "HEAD")
        projects = self._load()
        for existing in projects:
            old = Path(existing.repository_root)
            if old == root or old in root.parents or root in old.parents:
                raise ProjectRegistryError("repository duplicates or nests a registered project")
            if existing.origin_url == origin and not accept_duplicate_origin:
                raise ProjectRegistryError("origin is already registered by another project")
        project_id = "project-" + hashlib.sha256(str(root).encode()).hexdigest()[:16]
        if any(p.name == name or p.project_id == project_id for p in projects):
            raise ProjectRegistryError("project name or identity already exists")
        timestamp = _now()
        project = Project(
            project_id,
            name,
            str(root),
            origin,
            branch,
            head,
            timestamp,
            timestamp,
            ProjectStatus.ACTIVE,
            selected_policy,
            metadata or {},
        )
        self._save(projects + [project])
        return project

    def verify(self, name_or_id: str) -> Project:
        project = self.get(name_or_id)
        root = Path(project.repository_root)
        try:
            origin = _git(root, "config", "--get", "remote.origin.url")
            head = _git(root, "rev-parse", "HEAD")
            branch = _git(root, "branch", "--show-current")
        except ProjectRegistryError as exc:
            return self._mark(project, ProjectStatus.STALE, str(exc))
        if (
            origin != project.origin_url
            or branch != project.default_branch
            or head != project.current_head_sha
        ):
            return self._mark(project, ProjectStatus.STALE, "repository identity or HEAD changed")
        return self._mark(project, ProjectStatus.ACTIVE, None, verified_at=_now())

    def _mark(
        self,
        project: Project,
        status: ProjectStatus,
        reason: str | None,
        *,
        verified_at: str | None = None,
    ) -> Project:
        metadata = dict(project.metadata)
        if reason:
            metadata["last_verification_reason"] = reason
        updated = Project(
            project.project_id,
            project.name,
            project.repository_root,
            project.origin_url,
            project.default_branch,
            project.current_head_sha,
            project.registered_at,
            verified_at or project.verified_at,
            status,
            project.policy,
            metadata,
        )
        projects = [updated if p.project_id == project.project_id else p for p in self._load()]
        self._save(projects)
        return updated

    def remove(self, name_or_id: str) -> None:
        project = self.get(name_or_id)
        self._save([p for p in self._load() if p.project_id != project.project_id])

    def set_status(self, name_or_id: str, status: ProjectStatus) -> Project:
        project = self.get(name_or_id)
        updated = Project(
            project.project_id,
            project.name,
            project.repository_root,
            project.origin_url,
            project.default_branch,
            project.current_head_sha,
            project.registered_at,
            project.verified_at,
            status,
            project.policy,
            project.metadata,
        )
        self._save([updated if p.project_id == project.project_id else p for p in self._load()])
        return updated
