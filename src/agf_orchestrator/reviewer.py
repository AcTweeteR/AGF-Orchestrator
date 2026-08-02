"""Neutral reviewer interfaces and deterministic MVP reviewers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from .adapters.codex import CodexAdapter
from .models import ExecutionPlan, Task
from .review_models import ReviewFinding, ReviewReport, ReviewStatus

REVIEW_CATEGORIES = {
    "SCOPE",
    "CORRECTNESS",
    "TESTS",
    "SECURITY",
    "ARCHITECTURE",
    "REGRESSION",
    "QUALITY",
}
REVIEW_SEVERITIES = {"blocker", "major", "minor"}
REVIEW_FIELDS = {
    "schema_version",
    "status",
    "summary",
    "findings",
    "checks_performed",
    "residual_risks",
}
FINDING_FIELDS = {
    "finding_id",
    "category",
    "severity",
    "message",
    "affected_paths",
    "evidence",
    "required_change",
}
SECRET_VALUE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)(\s*[:=]\s*)([^\s,;]+)"
)


def _redact(value: str) -> str:
    return SECRET_VALUE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)


def parse_structured_review(output: str, reviewer: str = "codex-reviewer") -> ReviewReport:
    """Parse exactly one strict JSON review object; never infer a decision."""
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError) as exc:
        return ReviewReport(
            reviewer, ReviewStatus.HUMAN_REQUIRED, [], [], [f"invalid review JSON: {exc}"]
        )
    if not isinstance(payload, dict) or set(payload) != REVIEW_FIELDS:
        return ReviewReport(
            reviewer,
            ReviewStatus.HUMAN_REQUIRED,
            [],
            [],
            ["review schema is missing or contains unknown fields"],
        )
    if payload.get("schema_version") != "1.0":
        return ReviewReport(
            reviewer, ReviewStatus.HUMAN_REQUIRED, [], [], ["review schema_version must be 1.0"]
        )
    try:
        status = ReviewStatus(payload["status"])
    except (KeyError, ValueError, TypeError):
        return ReviewReport(
            reviewer, ReviewStatus.HUMAN_REQUIRED, [], [], ["review status is invalid"]
        )
    if (
        not isinstance(payload["summary"], str)
        or not isinstance(payload["checks_performed"], list)
        or not isinstance(payload["residual_risks"], list)
    ):
        return ReviewReport(
            reviewer, ReviewStatus.HUMAN_REQUIRED, [], [], ["review fields have invalid types"]
        )
    findings: list[ReviewFinding] = []
    for raw in payload["findings"] if isinstance(payload["findings"], list) else []:
        if not isinstance(raw, dict) or set(raw) != FINDING_FIELDS:
            return ReviewReport(
                reviewer, ReviewStatus.HUMAN_REQUIRED, [], [], ["review finding schema is invalid"]
            )
        if (
            not isinstance(raw["finding_id"], str)
            or not isinstance(raw["category"], str)
            or raw["category"] not in REVIEW_CATEGORIES
            or not isinstance(raw["severity"], str)
            or raw["severity"] not in REVIEW_SEVERITIES
            or not isinstance(raw["message"], str)
            or not isinstance(raw["affected_paths"], list)
            or not all(isinstance(path, str) for path in raw["affected_paths"])
            or not isinstance(raw["evidence"], str)
            or not isinstance(raw["required_change"], str)
        ):
            return ReviewReport(
                reviewer, ReviewStatus.HUMAN_REQUIRED, [], [], ["review finding values are invalid"]
            )
        findings.append(
            ReviewFinding(
                raw["finding_id"],
                raw["category"],
                raw["severity"],
                _redact(raw["message"]),
                [_redact(path) for path in raw["affected_paths"]],
                _redact(raw["evidence"]),
                _redact(raw["required_change"]),
                True,
            )
        )
    blockers = [finding for finding in findings if finding.severity in {"blocker", "major"}]
    if status is ReviewStatus.APPROVE and blockers:
        return ReviewReport(
            reviewer,
            ReviewStatus.HUMAN_REQUIRED,
            findings,
            [],
            ["APPROVE contains blocker or major findings"],
        )
    if status is ReviewStatus.REQUEST_CHANGES and not blockers:
        return ReviewReport(
            reviewer,
            ReviewStatus.HUMAN_REQUIRED,
            findings,
            [],
            ["REQUEST_CHANGES has no actionable blocker or major finding"],
        )
    if status is ReviewStatus.HUMAN_REQUIRED and not payload["summary"].strip():
        return ReviewReport(
            reviewer,
            ReviewStatus.HUMAN_REQUIRED,
            findings,
            [],
            ["HUMAN_REQUIRED requires a precise summary"],
        )
    return ReviewReport(
        reviewer,
        status,
        findings,
        [_redact(item) for item in payload["checks_performed"]],
        [],
        _redact(payload["summary"]),
        [_redact(item) for item in payload["checks_performed"]],
        [_redact(item) for item in payload["residual_risks"]],
    )


class Reviewer(Protocol):
    name: str

    def review(
        self,
        plan: ExecutionPlan,
        task: Task,
        changed_files: list[str],
        patch: str,
        validation_results: list[str],
        previous_findings: list[ReviewFinding] | None = None,
    ) -> ReviewReport: ...


@dataclass
class DeterministicReviewer:
    """A non-model reviewer suitable for tests and local dry runs."""

    name: str = "deterministic-reviewer"

    def review(
        self, plan, task, changed_files, patch, validation_results, previous_findings=None
    ) -> ReviewReport:
        findings: list[ReviewFinding] = []
        allowed = set(task.allowed_paths)
        unauthorized = sorted(set(changed_files) - allowed)
        if unauthorized:
            findings.append(
                ReviewFinding(
                    "REV-SCOPE",
                    "SCOPE",
                    "blocker",
                    "Changed paths exceed task allowed_paths.",
                    unauthorized,
                    "changed-file scope evidence",
                    "Remove unauthorized changes.",
                )
            )
        if not patch.strip():
            findings.append(
                ReviewFinding(
                    "REV-PATCH",
                    "CORRECTNESS",
                    "blocker",
                    "Patch is empty.",
                    [],
                    "empty unified patch",
                    "Produce the approved change.",
                )
            )
        if any("exit_code=0" not in result for result in validation_results):
            findings.append(
                ReviewFinding(
                    "REV-TESTS",
                    "TESTS",
                    "blocker",
                    "An approved validation did not pass.",
                    [],
                    "validation evidence has a non-zero exit code",
                    "Make the approved validation pass.",
                )
            )
        if findings:
            return ReviewReport(
                self.name,
                ReviewStatus.REQUEST_CHANGES,
                findings,
                ["deterministic review checks completed"],
                [finding.message for finding in findings],
            )
        return ReviewReport(
            self.name,
            ReviewStatus.APPROVE,
            [],
            [
                "task objective checked",
                "allowed paths checked",
                "acceptance criteria checked",
                "validation evidence checked",
                "architecture constraints checked",
                "security and regression scope checked",
            ],
            [],
        )


class CodexReviewerAdapter:
    """Provider-specific reviewer behind the neutral Reviewer interface."""

    name = "codex-reviewer"

    def __init__(self, adapter: CodexAdapter | None = None):
        self.adapter = adapter or CodexAdapter()

    def review(self, plan, task, changed_files, patch, validation_results, previous_findings=None):
        instruction = (
            "Review the supplied AGF patch. Return exactly one JSON object and no Markdown. "
            "Distinguish blocking defects from optional suggestions. Do not request unrelated "
            "refactors, new features, style-only changes, or changes outside allowed paths.\n"
            f"Task objective: {task.objective}\nAcceptance criteria: {task.acceptance_criteria}\n"
            f"Allowed paths: {task.allowed_paths}\nChanged paths: {changed_files}\n"
            f"Unified patch:\n{patch}\nExact validation results: {validation_results}\n"
            f"Architecture impact: {plan.architecture_impact}\n"
            f"Previous accepted findings: {previous_findings or []}\n"
            "Required JSON schema: {schema_version,status,summary,findings,"
            "checks_performed,residual_risks}; finding fields: "
            "{finding_id,category,severity,message,affected_paths,evidence,required_change}."
        )
        process = self.adapter.execute(instruction, plan.repository.root, sandbox="read-only")
        if process.human_required:
            return ReviewReport(
                self.name,
                ReviewStatus.HUMAN_REQUIRED,
                [],
                [],
                ["Codex reviewer invocation could not be verified"],
            )
        if process.timed_out or process.exit_code != 0:
            return ReviewReport(
                self.name,
                ReviewStatus.HUMAN_REQUIRED,
                [],
                [],
                ["Codex reviewer did not complete successfully"],
            )
        report = parse_structured_review(process.stdout_summary, self.name)
        if any(
            path not in task.allowed_paths
            for finding in report.findings
            for path in finding.affected_paths
        ):
            return ReviewReport(
                self.name,
                ReviewStatus.HUMAN_REQUIRED,
                report.findings,
                report.evidence,
                ["review finding references a path outside allowed_paths"],
                report.summary,
                report.checks_performed,
                report.residual_risks,
            )
        return report
