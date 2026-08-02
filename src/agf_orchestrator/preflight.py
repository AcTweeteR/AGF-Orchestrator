"""Read-only Git repository preflight checks."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .models import RepositoryContext


class PreflightError(RuntimeError):
    """Raised when repository context cannot be safely collected."""


class DirtyRepositoryError(PreflightError):
    """Raised when a dirty repository is not explicitly allowed."""


def _git(path: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "").strip() or str(exc)
        raise PreflightError(f"git {' '.join(args)} failed: {detail}") from exc
    return result.stdout.strip()


def collect_repository(path: str | Path, *, allow_dirty: bool = False) -> RepositoryContext:
    """Collect read-only repository metadata and enforce cleanliness."""
    requested = Path(path).expanduser().resolve()
    root = Path(_git(requested, "rev-parse", "--show-toplevel")).resolve()
    branch = _git(root, "branch", "--show-current")
    origin_result = subprocess.run(
        ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
    )
    if origin_result.returncode not in (0, 1):
        detail = origin_result.stderr.strip() or "unable to read origin URL"
        raise PreflightError(f"git config --get remote.origin.url failed: {detail}")
    origin = origin_result.stdout.strip() or None
    status = _git(root, "status", "--porcelain")
    clean = not bool(status)
    head_sha = _git(root, "rev-parse", "HEAD")
    context = RepositoryContext(
        root=str(root), branch=branch, origin=origin, clean=clean, head_sha=head_sha
    )
    if not clean and not allow_dirty:
        raise DirtyRepositoryError(
            "repository is dirty: pass --allow-dirty to plan without a clean-tree guarantee"
        )
    return context
