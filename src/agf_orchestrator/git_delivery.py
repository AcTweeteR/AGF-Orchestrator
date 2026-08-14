"""Safe branch, patch application, commit, push, and draft-PR delivery."""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from .authority_context import AuthorityContextError, resolve_authority
from .constitution import ConstitutionVerificationError
from .executor import _changed_paths, _run_validations, _status_lines
from .merge_models import (
    AuthorizationStatus,
    DecisionStatus,
    MergeDecision,
    MergeValidationError,
    RiskClass,
    decision_from_dict,
)
from .merge_policy import REQUIRED_GATES
from .models import Task
from .policy_authority import PolicyActivationError
from .policy_state_store import PolicyStateError, PolicyStateStore
from .risk_models import risk_from_dict
from .scheduler_journal import InboxItem, SchedulerJournal


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
    uncertainty_kind: str = "NONE"

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "classification": self.classification.value,
            "queried_ref": self.queried_ref,
            "exit_code": self.exit_code,
            "matched_sha": self.matched_sha,
            "stderr_category": self.stderr_category,
            "source": self.source,
            "freshness": self.freshness,
            "uncertainty_kind": self.uncertainty_kind,
        }

    def inbox_payload(self, *, project_id: str, task_id: str) -> dict[str, str]:
        """Return bounded Director-inbox evidence for remote uncertainty."""
        if self.classification is not RemoteBranchClassification.UNCERTAIN:
            raise GitDeliveryError("only uncertain remote evidence can enter the inbox")
        if (
            not re.fullmatch(r"project-[0-9a-f]{16}", project_id)
            or not re.fullmatch(r"task-[a-z0-9][a-z0-9-]{2,127}", task_id)
            or not re.fullmatch(r"refs/heads/[A-Za-z0-9._/-]{1,200}", self.queried_ref)
            or len(self.stderr_category) > 64
        ):
            raise GitDeliveryError("remote uncertainty identity is invalid")
        return {
            "title": "Remote state requires reconciliation",
            "project_id": project_id,
            "task_id": task_id,
            "summary": (
                f"Remote ref {self.queried_ref} is UNCERTAIN; "
                f"kind={self.uncertainty_kind}; source={self.source}; "
                f"category={self.stderr_category}."
            ),
            "required_action": "Reconcile remote identity and branch state before delivery.",
            "evidence_ref": self.queried_ref,
            "classification": self.classification.value,
            "uncertainty_kind": self.uncertainty_kind,
        }


def persist_remote_uncertainty(
    evidence: RemoteBranchEvidence, *, project_id: str, task_id: str,
    state_dir: str | Path | None = None,
) -> InboxItem:
    """Route one uncertain remote observation into the bounded Director inbox."""
    payload = evidence.inbox_payload(project_id=project_id, task_id=task_id)
    digest = hashlib.sha256(
        f"{project_id}:{task_id}:{evidence.queried_ref}".encode("utf-8")
    ).hexdigest()
    inbox_id = "inbox-" + str(int(digest, 16))[:16]
    item = InboxItem(
        inbox_id=inbox_id,
        project_id=project_id,
        scheduler_id="scheduler-delivery",
        title=payload["title"],
        summary=payload["summary"],
        required_action=payload["required_action"],
        decision_id="decision-" + digest[:32],
        task_id=task_id,
        risk_class="HIGH",
        failed_gates=("remote_state",),
        evidence_refs=(evidence.queried_ref,),
        policy_id="remote-state-policy",
        policy_hash=digest,
        uncertainty_kind=payload["uncertainty_kind"],
    )
    return SchedulerJournal(
        state_dir or (Path.home() / ".agf-orchestrator"), project_id, "scheduler-delivery"
    ).add_inbox(item)

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
            "UNAVAILABLE",
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
            "ABSENT",
        )
    if len(lines) != 1:
        classification = RemoteBranchClassification.UNCERTAIN
        matched_sha = None
        uncertainty_kind = "DIVERGENT"
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
        uncertainty_kind = (
            "NONE" if classification is not RemoteBranchClassification.UNCERTAIN
            else "CONTRADICTORY"
        )
    return RemoteBranchEvidence(
        classification,
        queried_ref,
        result.returncode,
        matched_sha,
        stderr_category,
        "git ls-remote --heads origin",
        "live",
        uncertainty_kind,
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

    def validate_target(
        self, repository: str, base_sha: str, branch: str,
        *, uncertainty_handler: Callable[[RemoteBranchEvidence], None] | None = None,
    ) -> RemoteBranchEvidence:
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
            if uncertainty_handler is not None:
                uncertainty_handler(remote)
            raise GitDeliveryError(
                "remote branch state is uncertain: "
                f"{remote.queried_ref}; stderr_category={remote.stderr_category}"
            )
        current = _git(repository, "rev-parse", "HEAD").stdout.strip()
        if current != base_sha:
            raise GitDeliveryError("base SHA drifted before delivery")
        if _status_lines(repository):
            raise GitDeliveryError("caller repository is not clean before delivery")
        return remote

    def deliver(
        self,
        repository: str,
        base_sha: str,
        branch: str,
        patch_path: str,
        task: Task,
        *,
        merge_decision: MergeDecision | dict[str, object] | None = None,
        project_id: str | None = None,
        expected_patch_sha256: str | None = None,
        validation_timeout: float = 60.0,
        before_push: Callable[[str, str, list[str]], None] | None = None,
    ) -> GitDeliveryResult:
        handler = (
            None
            if project_id is None
            else lambda evidence: persist_remote_uncertainty(
                evidence, project_id=project_id, task_id=task.task_id
            )
        )
        self.validate_target(repository, base_sha, branch, uncertainty_handler=handler)
        patch_bytes = Path(patch_path).read_bytes()
        if (
            expected_patch_sha256
            and hashlib.sha256(patch_bytes).hexdigest() != expected_patch_sha256
        ):
            raise GitDeliveryError("patch hash mismatch")
        if task.task_id == "E6-T2" and merge_decision is None:
            raise GitDeliveryError("fully evidenced LOW merge decision is required")
        if merge_decision is None and project_id is not None:
            try:
                active = resolve_authority(project_id).policy
            except (PolicyActivationError, AuthorityContextError) as exc:
                raise GitDeliveryError("active policy is not verified") from exc
            if active is not None:
                raise GitDeliveryError("active policy requires an integrity-bound MergeDecision")
        if merge_decision is None and project_id is None:
            if (Path.home() / ".agf-orchestrator" / "policy-state.sqlite3").exists():
                raise GitDeliveryError("delivery project identity is required")
        validated_decision: MergeDecision | None = None
        if merge_decision is not None:
            if project_id is None:
                raise GitDeliveryError("delivery project identity is required")
            validated_decision = _validate_delivery_authorization(
                merge_decision,
                task_id=task.task_id,
                base_sha=base_sha,
                delivery_sha=hashlib.sha256(patch_bytes).hexdigest(),
                project_id=project_id,
            )

        authority_stack = ExitStack()
        worktree: str | None = None
        commit_sha: str | None = None
        validation_results: list[str] = []
        changed_files: list[str] = []
        try:
            worktree = tempfile.mkdtemp(prefix="agf-delivery-")
            shutil.rmtree(worktree)
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
            if validated_decision is not None and project_id is not None:
                store = PolicyStateStore(Path.home() / ".agf-orchestrator")
                store.reserve_delivery(
                    project_id,
                    operation_id=validated_decision.decision_id,
                    expected_generation=validated_decision.authority_generation,
                )
                commit_token = store.begin_delivery_commit(
                    project_id,
                    operation_id=validated_decision.decision_id,
                    expected_generation=validated_decision.authority_generation,
                )
                authority_stack.enter_context(
                    store.delivery_transaction(
                        project_id,
                        operation_id=validated_decision.decision_id,
                        expected_generation=validated_decision.authority_generation,
                        commit_token=commit_token,
                    )
                )
            _ensure_emergency_stop_clear(project_id)
            _git(worktree, "add", "--", *changed_files)
            _git(worktree, "commit", "-m", f"AGF: {task.title}")
            commit_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
            if before_push is not None:
                before_push(commit_sha, worktree, changed_files)
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
            authority_stack.close()
            if worktree is not None:
                _git(repository, "worktree", "remove", "--force", worktree, check=False)


def _validate_delivery_authorization(
    decision: MergeDecision | dict[str, object],
    *,
    task_id: str,
    base_sha: str,
    delivery_sha: str,
    project_id: str | None = None,
) -> MergeDecision:
    """Require an intact, complete LOW decision before patch mutation."""
    _ensure_emergency_stop_clear(project_id)
    try:
        validated = (
            decision
            if isinstance(decision, MergeDecision)
            else decision_from_dict(decision)
        )
        validated.validate()
    except (MergeValidationError, TypeError, ValueError) as exc:
        raise GitDeliveryError(f"invalid merge authorization: {exc}") from exc
    if not validated.verify_integrity():
        raise GitDeliveryError("merge authorization integrity check failed")
    if project_id is not None and validated.project_id != project_id:
        raise GitDeliveryError("merge authorization project does not match delivery")
    if project_id is not None:
        try:
            snapshot = resolve_authority(project_id).snapshot
        except (PolicyStateError, sqlite3.Error) as exc:
            raise GitDeliveryError("authority state is unreadable") from exc
        if snapshot is None:
            raise GitDeliveryError("authority state is not bootstrapped")
        if int(snapshot["generation"]) != validated.authority_generation:
            raise GitDeliveryError("authorization generation is stale")
        if int(snapshot["kill_switch_active"]):
            raise GitDeliveryError("kill switch is active")
    if validated.risk_class in {RiskClass.CRITICAL, RiskClass.UNKNOWN}:
        raise GitDeliveryError("CRITICAL/UNKNOWN delivery requires human approval")
    if validated.risk_class in {RiskClass.MEDIUM, RiskClass.HIGH} and not validated.policy_hash:
        raise GitDeliveryError(
            "active policy hash is required for MEDIUM/HIGH delivery; legacy path is LOW-only"
        )
    if (Path.home() / ".agf-orchestrator" / "policy-state.sqlite3").exists():
        try:
            active = resolve_authority(validated.project_id).policy
        except (PolicyActivationError, AuthorityContextError) as exc:
            raise GitDeliveryError("active policy is not verified") from exc
    else:
        active = None
    if active is None and validated.policy_hash:
        raise GitDeliveryError("merge authorization policy was superseded or rolled back")
    if active is not None:
        if not validated.policy_hash:
            raise GitDeliveryError("legacy authorization is stale under active policy")
        if (
            active.policy_id != validated.policy_id
            or active.version != validated.policy_version
            or active.policy_hash != validated.policy_hash
        ):
            raise GitDeliveryError("merge authorization policy identity is stale")
        _validate_freshness(validated, active.freshness_limits)
        if validated.risk_assessment is None:
            raise GitDeliveryError("Risk Engine assessment is required")
        try:
            assessment = risk_from_dict(validated.risk_assessment)
        except (TypeError, ValueError) as exc:
            raise GitDeliveryError("Risk Engine assessment is invalid") from exc
        if assessment.project_id != validated.project_id or assessment.task_id != validated.task_id:
            raise GitDeliveryError("Risk Engine assessment identity is stale")
        if assessment.level.name != validated.risk_class.value:
            raise GitDeliveryError("Risk Engine assessment does not match decision")
    human_blocked = (
        validated.decision_status is DecisionStatus.BLOCKED
        and validated.authorization_status is AuthorizationStatus.NOT_AUTHORIZED
        and validated.blocking_reasons == ("human merge approval is required by policy",)
    )
    if validated.decision_status is not DecisionStatus.ELIGIBLE and not human_blocked:
        raise GitDeliveryError("merge decision is not eligible")
    if (
        validated.authorization_status is not AuthorizationStatus.AUTHORIZED
        and not human_blocked
    ):
        raise GitDeliveryError("merge decision is not authorized")
    if validated.task_id != task_id:
        raise GitDeliveryError("merge decision task does not match delivery task")
    if validated.base_sha != base_sha:
        raise GitDeliveryError("merge decision base SHA does not match delivery")
    if validated.delivery_sha != delivery_sha:
        raise GitDeliveryError("merge decision delivery SHA does not match patch")
    gate_map = {gate.name: gate for gate in validated.gates}
    missing = [name for name in REQUIRED_GATES if name not in gate_map]
    failed = [name for name, gate in gate_map.items() if gate.status.value != "PASS"]
    if missing or failed:
        details = [f"missing gates: {', '.join(missing)}"] if missing else []
        if failed:
            details.append(f"non-PASS gates: {', '.join(sorted(failed))}")
        raise GitDeliveryError(
            "merge authorization evidence is incomplete (" + "; ".join(details) + ")"
        )
    return validated


def _ensure_emergency_stop_clear(project_id: str | None) -> None:
    """Re-read the owner signal at each consequential delivery boundary."""
    if project_id is None:
        if (Path.home() / ".agf-orchestrator" / "policy-state.sqlite3").exists():
            raise GitDeliveryError("delivery project identity is required")
        return
    try:
        snapshot = resolve_authority(project_id).snapshot
    except (
        AuthorityContextError,
        ConstitutionVerificationError,
        PolicyStateError,
        sqlite3.Error,
        OSError,
    ) as exc:
        raise GitDeliveryError("authority state is unreadable") from exc
    if snapshot is None:
        raise GitDeliveryError("authority state is not bootstrapped")
    if int(snapshot["kill_switch_active"]):
        raise GitDeliveryError(
            "emergency stop is active: "
            f"stop-{snapshot['operation_id']} generation {snapshot['generation']}"
        )


def _validate_freshness(decision: MergeDecision, limits: dict[str, Any]) -> None:
    try:
        gate_seconds = int(limits["gate_seconds"])
        policy_seconds = int(limits["policy_seconds"])
        expiry = datetime.fromisoformat(decision.expiry)
    except (KeyError, TypeError, ValueError) as exc:
        raise GitDeliveryError("active policy freshness evidence is invalid") from exc
    now = datetime.now(UTC)
    if (
        expiry.tzinfo is None
        or expiry <= now
        or expiry - now > timedelta(seconds=policy_seconds)
    ):
        raise GitDeliveryError("merge authorization is expired")
    for gate in decision.gates:
        try:
            observed = datetime.fromisoformat(gate.observed_at)
        except (TypeError, ValueError) as exc:
            raise GitDeliveryError("gate freshness evidence is invalid") from exc
        if (
            observed.tzinfo is None
            or observed > now
            or now - observed > timedelta(seconds=gate_seconds)
        ):
            raise GitDeliveryError(f"gate evidence is stale: {gate.name}")
