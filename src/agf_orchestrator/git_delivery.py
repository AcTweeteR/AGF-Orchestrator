"""Safe branch, patch application, commit, push, and draft-PR delivery."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .executor import _changed_paths, _run_validations, _status_lines
from .models import Task


class GitDeliveryError(RuntimeError):
    """A delivery operation was blocked or failed."""


class RemoteBranchClassification(StrEnum):
    """Authoritative classification of one exact remote branch ref."""

    ABSENT = "ABSENT"
    PRESENT = "PRESENT"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class RemoteBranchEvidence:
    """Bounded evidence from one live remote branch query."""

    classification: RemoteBranchClassification
    queried_ref: str
    exit_code: int
    matched_sha: str | None
    stderr_category: str
    source: str
    freshness: str

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "classification": self.classification.value,
            "queried_ref": self.queried_ref,
            "exit_code": self.exit_code,
            "matched_sha": self.matched_sha,
            "stderr_category": self.stderr_category,
            "source": self.source,
            "freshness": self.freshness,
        }


def _git(repository: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", repository, *args],
        check=check,
        capture_output=True,
        text=True,
        shell=False,
    )


def sanitize_branch_name(plan_id: str, task_id: str) -> str:
    def clean(value: str) -> str:
        result = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
        return result or "item"

    return f"agf/{clean(plan_id)}/{clean(task_id)}"


_REMOTE_SHA = re.compile(r"^[0-9a-fA-F]{40,64}$")


def _stderr_category(stderr: str) -> str:
    value = stderr.casefold()
    if not value.strip():
        return "NONE"
    if any(item in value for item in ("could not resolve host", "name or service", "network")):
        return "DNS_OR_NETWORK"
    if any(item in value for item in ("permission denied", "authentication", "unauthorized")):
        return "AUTHORIZATION"
    return "REMOTE_ERROR"


def classify_remote_branch(repository: str, branch: str) -> RemoteBranchEvidence:
    """Classify one exact live remote branch query without using cached refs."""
    queried_ref = f"refs/heads/{branch}"
    result = _git(repository, "ls-remote", "--heads", "origin", branch, check=False)
    stderr_category = _stderr_category(result.stderr)
    if result.returncode != 0 or stderr_category != "NONE":
        return RemoteBranchEvidence(
            RemoteBranchClassification.UNCERTAIN,
            queried_ref,
            result.returncode,
            None,
            stderr_category,
            "git ls-remote --heads origin",
            "live",
        )
    lines = result.stdout.splitlines()
    if not lines:
        return RemoteBranchEvidence(
            RemoteBranchClassification.ABSENT,
            queried_ref,
            result.returncode,
            None,
            stderr_category,
            "git ls-remote --heads origin",
            "live",
        )
    if len(lines) != 1:
        classification = RemoteBranchClassification.UNCERTAIN
        matched_sha = None
    else:
        fields = lines[0].split("\t")
        candidate_sha = fields[0] if len(fields) == 2 and _REMOTE_SHA.fullmatch(fields[0]) else None
        classification = (
            RemoteBranchClassification.PRESENT
            if candidate_sha is not None and fields[1] == queried_ref
            else RemoteBranchClassification.UNCERTAIN
        )
        matched_sha = (
            candidate_sha if classification is RemoteBranchClassification.PRESENT else None
        )
    return RemoteBranchEvidence(
        classification,
        queried_ref,
        result.returncode,
        matched_sha,
        stderr_category,
        "git ls-remote --heads origin",
        "live",
    )


@dataclass(frozen=True)
class GitDeliveryResult:
    branch: str
    commit_sha: str | None
    push_status: str
    changed_files: list[str]
    validation_results: list[str]
    blocking_issues: list[str]


class DraftPRCreator:
    """Create a draft PR with local gh, or simulate it for local canaries."""

    def __init__(self, *, simulate: bool = False, executable: str = "gh"):
        self.simulate = simulate
        self.executable = executable

    def create(self, repository: str, branch: str, title: str, body: str) -> str:
        if self.simulate:
            return f"local://draft-pr/{branch}"
        if shutil.which(self.executable) is None:
            raise GitDeliveryError("GitHub CLI is unavailable; pushed branch retained")
        result = subprocess.run(
            [self.executable, "pr", "create", "--draft", "--base", "main",
             "--head", branch, "--title", title, "--body", body],
            cwd=repository, check=False, capture_output=True, text=True, shell=False,
        )
        if result.returncode != 0:
            raise GitDeliveryError("draft PR creation failed; pushed branch retained")
        url = result.stdout.strip().splitlines()
        if not url:
            raise GitDeliveryError("draft PR creation returned no URL; pushed branch retained")
        return url[-1]


class GitDelivery:
    """Apply a reviewed patch in a fresh worktree, then commit and push it."""

    def __init__(self, *, push: bool = True):
        self.push = push

    def validate_target(self, repository: str, base_sha: str, branch: str) -> None:
        """Validate the delivery target before any agent execution occurs."""
        if branch in {"main", "master"} or branch.startswith(("main/", "master/")):
            raise GitDeliveryError("delivery cannot modify main or master")
        local = _git(repository, "show-ref", "--verify", f"refs/heads/{branch}", check=False)
        if local.returncode == 0:
            raise GitDeliveryError(f"delivery branch already exists: {branch}")
        remote = classify_remote_branch(repository, branch)
        if remote.classification is RemoteBranchClassification.PRESENT:
            raise GitDeliveryError(f"delivery branch already exists remotely: {branch}")
        if remote.classification is RemoteBranchClassification.UNCERTAIN:
            raise GitDeliveryError(
                "remote branch state is uncertain: "
                f"{remote.queried_ref}; stderr_category={remote.stderr_category}"
            )
        current = _git(repository, "rev-parse", "HEAD").stdout.strip()
        if current != base_sha:
            raise GitDeliveryError("base SHA drifted before delivery")
        if _status_lines(repository):
            raise GitDeliveryError("caller repository is not clean before delivery")

    def deliver(
        self,
        repository: str,
        base_sha: str,
        branch: str,
        patch_path: str,
        task: Task,
        *,
        expected_patch_sha256: str | None = None,
        validation_timeout: float = 60.0,
    ) -> GitDeliveryResult:
        self.validate_target(repository, base_sha, branch)
        patch_bytes = Path(patch_path).read_bytes()
        if (
            expected_patch_sha256
            and hashlib.sha256(patch_bytes).hexdigest() != expected_patch_sha256
        ):
            raise GitDeliveryError("patch hash mismatch")

        worktree = tempfile.mkdtemp(prefix="agf-delivery-")
        shutil.rmtree(worktree)
        commit_sha: str | None = None
        validation_results: list[str] = []
        changed_files: list[str] = []
        try:
            _git(repository, "worktree", "add", "-b", branch, worktree, base_sha)
            check = subprocess.run(
                ["git", "-C", worktree, "apply", "--check", patch_path],
                check=False, capture_output=True, text=True, shell=False,
            )
            if check.returncode != 0:
                raise GitDeliveryError("reviewed patch does not apply cleanly")
            _git(worktree, "apply", patch_path)
            changed_files = _changed_paths([], _status_lines(worktree))
            if not set(changed_files).issubset(set(task.allowed_paths)):
                raise GitDeliveryError("applied patch changed paths outside allowed_paths")
            validation_evidence, passed, blockers = _run_validations(
                task.validation_commands, worktree, validation_timeout
            )
            validation_results.extend(validation_evidence)
            if not passed:
                raise GitDeliveryError(
                    "validation failed after patch application: " + "; ".join(blockers)
                )
            _git(worktree, "add", "--", *changed_files)
            _git(worktree, "commit", "-m", f"AGF: {task.title}")
            commit_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
            if self.push:
                pushed = _git(worktree, "push", "-u", "origin", branch, check=False)
                if pushed.returncode != 0:
                    raise GitDeliveryError("push failed; local delivery branch retained")
                push_status = "PUSHED"
            else:
                push_status = "NOT_REQUESTED"
            return GitDeliveryResult(branch, commit_sha, push_status, changed_files,
                                     validation_results, [])
        except (OSError, subprocess.CalledProcessError) as exc:
            raise GitDeliveryError("git delivery command failed") from exc
        finally:
            _git(repository, "worktree", "remove", "--force", worktree, check=False)
