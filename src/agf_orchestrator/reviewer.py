"""Neutral reviewer interfaces and deterministic MVP reviewers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .adapters.codex import CodexAdapter
from .models import ExecutionPlan, Task
from .review_models import ReviewFinding, ReviewReport, ReviewStatus


class Reviewer(Protocol):
    name: str

    def review(
        self,
        plan: ExecutionPlan,
        task: Task,
        changed_files: list[str],
        patch: str,
        validation_results: list[str],
    ) -> ReviewReport: ...


@dataclass
class DeterministicReviewer:
    """A non-model reviewer suitable for tests and local dry runs."""

    name: str = "deterministic-reviewer"

    def review(
        self, plan, task, changed_files, patch, validation_results
    ) -> ReviewReport:
        findings: list[ReviewFinding] = []
        allowed = set(task.allowed_paths)
        unauthorized = sorted(set(changed_files) - allowed)
        if unauthorized:
            findings.append(
                ReviewFinding(
                    "SCOPE", "blocker", "Changed paths exceed task allowed_paths.", unauthorized
                )
            )
        if not patch.strip():
            findings.append(ReviewFinding("EMPTY_PATCH", "blocker", "Patch is empty.", []))
        if any("exit_code=0" not in result for result in validation_results):
            findings.append(
                ReviewFinding(
                    "VALIDATION", "blocker", "An approved validation did not pass.", []
                )
            )
        if findings:
            return ReviewReport(
                self.name, ReviewStatus.REQUEST_CHANGES, findings,
                ["deterministic review checks completed"],
                [finding.message for finding in findings],
            )
        return ReviewReport(
            self.name, ReviewStatus.APPROVE, [],
            [
                "task objective checked",
                "allowed paths checked",
                "acceptance criteria checked",
                "validation evidence checked",
                "architecture constraints checked",
                "security and regression scope checked",
            ], [],
        )


class CodexReviewerAdapter:
    """Provider-specific reviewer behind the neutral Reviewer interface."""

    name = "codex-reviewer"

    def __init__(self, adapter: CodexAdapter | None = None):
        self.adapter = adapter or CodexAdapter()

    def review(self, plan, task, changed_files, patch, validation_results):
        instruction = (
            "Review the supplied AGF patch. Do not modify files. Return APPROVE only "
            "when objective, allowed paths, acceptance criteria, validations, architecture, "
            "security, regressions, and scope are all satisfactory; otherwise return "
            "REQUEST_CHANGES with exact findings.\n"
            f"Objective: {task.objective}\nAllowed paths: {task.allowed_paths}\n"
            f"Changed files: {changed_files}\nValidation evidence: {validation_results}\n"
            f"Patch:\n{patch}\n"
        )
        process = self.adapter.execute(instruction, plan.repository.root, sandbox="read-only")
        if process.human_required:
            return ReviewReport(
                self.name, ReviewStatus.HUMAN_REQUIRED, [], [],
                ["Codex reviewer invocation could not be verified"],
            )
        if process.timed_out or process.exit_code != 0:
            return ReviewReport(
                self.name, ReviewStatus.HUMAN_REQUIRED, [], [],
                ["Codex reviewer did not complete successfully"],
            )
        output = f"{process.stdout_summary}\n{process.stderr_summary}"
        if "APPROVE" in output and "REQUEST_CHANGES" not in output:
            return ReviewReport(
                self.name,
                ReviewStatus.APPROVE,
                [],
                ["Codex review output received"],
                [],
            )
        return ReviewReport(
            self.name, ReviewStatus.REQUEST_CHANGES,
            [ReviewFinding("CODEX", "blocker", "Codex reviewer requested changes.", [])],
            ["Codex review output received"], ["Codex reviewer did not approve the patch"],
        )
