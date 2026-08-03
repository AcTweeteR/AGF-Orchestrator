"""Neutral reviewer interfaces and deterministic MVP reviewers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from .adapters.codex import CodexAdapter
from .models import ExecutionPlan, Task
from .review_models import ReviewFinding, ReviewReport, ReviewStatus

REVIEW_FIELDS = {"status", "summary", "findings"}
FINDING_FIELDS = {"severity", "code", "path", "line", "message", "required_change"}
LEGACY_REVIEW_FIELDS = {
    "schema_version", "status", "summary", "findings", "checks_performed", "residual_risks"
}
LEGACY_FINDING_FIELDS = {
    "finding_id", "category", "severity", "message", "affected_paths", "evidence", "required_change"
}
MAX_REVIEW_SUMMARY = 2000
MAX_REVIEW_FINDINGS = 50
MAX_REVIEW_CODE = 80
MAX_REVIEW_PATH = 500
MAX_REVIEW_MESSAGE = 1200
MAX_REVIEW_CHANGE = 1200
REVIEW_SCHEMA = (
    '{"status":"APPROVE | REQUEST_CHANGES | REJECT | HUMAN_REQUIRED",'
    '"summary":"bounded string","findings":[{"severity":"P0 | P1 | P2 | P3",'
    '"code":"BOUNDED_MACHINE_CODE","path":"relative/path/or/null",'
    '"line":integer_or_null,"message":"bounded actionable string",'
    '"required_change":"bounded string or null"}]}'
)
SECRET_VALUE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)(\s*[:=]\s*)([^\s,;]+)"
)
STATUS_ALIASES = {
    "approve": ReviewStatus.APPROVE,
    "approved": ReviewStatus.APPROVE,
    "request_changes": ReviewStatus.REQUEST_CHANGES,
    "requested_changes": ReviewStatus.REQUEST_CHANGES,
    "reject": ReviewStatus.REJECT,
    "rejected": ReviewStatus.REJECT,
    "human_required": ReviewStatus.HUMAN_REQUIRED,
    "requires_human": ReviewStatus.HUMAN_REQUIRED,
}


def _redact(value: str) -> str:
    return SECRET_VALUE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)


def _normalize_status(value: object) -> tuple[ReviewStatus | None, str | None]:
    if not isinstance(value, str) or not value.strip():
        return None, "REVIEW_STATUS_INVALID: review status is invalid"
    normalized = re.sub(r"[\s-]+", "_", value.strip().casefold())
    status = STATUS_ALIASES.get(normalized)
    if status is None:
        return None, "REVIEW_STATUS_INVALID: review status is invalid"
    if value != status.value:
        return status, f"review status normalized: {_redact(value)} -> {status.value}"
    return status, None


def _invalid(reviewer: str, code: str) -> ReviewReport:
    return ReviewReport(reviewer, ReviewStatus.HUMAN_REQUIRED, [], [], [code])


def _parse_legacy_review(payload: dict, reviewer: str) -> ReviewReport:
    """Read the pre-1.0 external shape for existing stored/test fixtures only."""
    if payload.get("schema_version") != "1.0":
        return _invalid(reviewer, "REVIEW_SCHEMA_INVALID: legacy schema version is invalid")
    status, evidence = _normalize_status(payload.get("status"))
    if status is None or not isinstance(payload.get("summary"), str):
        return _invalid(reviewer, "REVIEW_STATUS_INVALID: legacy status or summary is invalid")
    findings = []
    for raw in payload.get("findings", []):
        if not isinstance(raw, dict) or set(raw) != LEGACY_FINDING_FIELDS:
            return _invalid(reviewer, "REVIEW_FINDING_SCHEMA_INVALID: legacy finding is invalid")
        findings.append(ReviewFinding(
            raw["finding_id"], raw["category"], raw["severity"], _redact(raw["message"]),
            [_redact(path) for path in raw["affected_paths"]], _redact(raw["evidence"]),
            _redact(raw["required_change"]), True,
        ))
    blockers = [item for item in findings if item.severity in {"blocker", "major"}]
    if status is ReviewStatus.APPROVE and blockers:
        return _invalid(reviewer, "REVIEW_APPROVE_FINDINGS: APPROVE contains blocking findings")
    if status is ReviewStatus.REQUEST_CHANGES and not blockers:
        return _invalid(reviewer, "REVIEW_CHANGES_NOT_ACTIONABLE: no blocking finding")
    return ReviewReport(
        reviewer, status, findings, [item for item in [evidence] if item], [],
        _redact(payload["summary"]), payload.get("checks_performed", []),
        payload.get("residual_risks", []),
    )


def parse_structured_review(output: str, reviewer: str = "codex-reviewer") -> ReviewReport:
    """Parse exactly one strict JSON review object; never infer a decision."""
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return _invalid(reviewer, "REVIEW_JSON_INVALID: response is not one JSON object")
    if isinstance(payload, dict) and set(payload) == LEGACY_REVIEW_FIELDS:
        return _parse_legacy_review(payload, reviewer)
    if not isinstance(payload, dict) or set(payload) != REVIEW_FIELDS:
        return _invalid(
            reviewer, "REVIEW_SCHEMA_INVALID: top-level fields do not match the contract"
        )
    status, normalization_evidence = _normalize_status(payload.get("status"))
    if status is None:
        return _invalid(reviewer, "REVIEW_STATUS_INVALID: status is invalid")
    summary = payload["summary"]
    raw_findings = payload["findings"]
    if not isinstance(summary, str) or not summary.strip() or len(summary) > MAX_REVIEW_SUMMARY:
        return _invalid(reviewer, "REVIEW_FIELDS_INVALID: summary is missing or unbounded")
    if not isinstance(raw_findings, list) or len(raw_findings) > MAX_REVIEW_FINDINGS:
        return _invalid(reviewer, "REVIEW_FINDINGS_INVALID: findings is not bounded")

    severity_map = {"P0": "blocker", "P1": "major", "P2": "minor", "P3": "minor"}
    findings: list[ReviewFinding] = []
    for raw in raw_findings:
        if not isinstance(raw, dict) or set(raw) != FINDING_FIELDS:
            return _invalid(
                reviewer,
                "REVIEW_FINDING_SCHEMA_INVALID: finding fields do not match the contract",
            )
        path = raw["path"]
        line = raw["line"]
        if (
            raw["severity"] not in severity_map
            or not isinstance(raw["code"], str)
            or not raw["code"].strip()
            or len(raw["code"]) > MAX_REVIEW_CODE
            or (path is not None and (not isinstance(path, str) or not path.strip()))
            or (isinstance(path, str) and (
                len(path) > MAX_REVIEW_PATH or path.startswith("/") or "\\" in path
                or ".." in path.split("/")
            ))
            or (line is not None and (
                isinstance(line, bool) or not isinstance(line, int) or line < 1
            ))
            or not isinstance(raw["message"], str)
            or not raw["message"].strip()
            or len(raw["message"]) > MAX_REVIEW_MESSAGE
            or (raw["required_change"] is not None and (
                not isinstance(raw["required_change"], str)
                or len(raw["required_change"]) > MAX_REVIEW_CHANGE
            ))
        ):
            return _invalid(
                reviewer,
                "REVIEW_FINDING_VALUES_INVALID: finding values are invalid or unbounded",
            )
        findings.append(
            ReviewFinding(
                raw["code"],
                "REVIEW",
                severity_map[raw["severity"]],
                _redact(raw["message"]),
                [_redact(path)] if path is not None else [],
                raw["code"] + (f" at {path}:{line}" if path is not None else ""),
                _redact(raw["required_change"] or ""),
                True,
            )
        )

    blockers = [finding for finding in findings if finding.severity in {"blocker", "major"}]
    actionable = [finding for finding in findings if finding.required_change]
    if status is ReviewStatus.APPROVE and findings:
        return _invalid(reviewer, "REVIEW_APPROVE_FINDINGS: APPROVE requires findings=[]")
    if status is ReviewStatus.REQUEST_CHANGES and not actionable:
        return _invalid(
            reviewer,
            "REVIEW_CHANGES_NOT_ACTIONABLE: REQUEST_CHANGES requires an actionable finding",
        )
    if status is ReviewStatus.REJECT and not blockers:
        return _invalid(
            reviewer,
            "REVIEW_REJECT_BLOCKING_FINDING: REJECT requires a P0 or P1 finding",
        )
    return ReviewReport(
        reviewer,
        status,
        findings,
        [item for item in [normalization_evidence] if item is not None],
        [],
        _redact(summary),
        [],
        [],
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

    def review(self, plan, task, changed_files, patch, validation_results, previous_findings=None):
        findings: list[ReviewFinding] = []
        unauthorized = sorted(set(changed_files) - set(task.allowed_paths))
        if unauthorized:
            findings.append(ReviewFinding(
                "REV-SCOPE", "SCOPE", "blocker", "Changed paths exceed task allowed_paths.",
                unauthorized, "changed-file scope evidence", "Remove unauthorized changes.",
            ))
        if not patch.strip():
            findings.append(ReviewFinding(
                "REV-PATCH", "CORRECTNESS", "blocker", "Patch is empty.", [],
                "empty unified patch", "Produce the approved change.",
            ))
        if any("exit_code=0" not in result for result in validation_results):
            findings.append(ReviewFinding(
                "REV-TESTS", "TESTS", "blocker", "An approved validation did not pass.", [],
                "validation evidence has a non-zero exit code",
                "Make the approved validation pass.",
            ))
        if findings:
            return ReviewReport(
                self.name, ReviewStatus.REQUEST_CHANGES, findings,
                ["deterministic review checks completed"],
                [finding.message for finding in findings],
            )
        return ReviewReport(
            self.name, ReviewStatus.APPROVE, [],
            ["task objective checked", "allowed paths checked", "acceptance criteria checked",
             "validation evidence checked", "architecture constraints checked",
             "security and regression scope checked"], [],
        )


class CodexReviewerAdapter:
    """Provider-specific reviewer with one bounded schema-repair retry."""

    name = "codex-reviewer"

    def __init__(self, adapter: CodexAdapter | None = None):
        self.adapter = adapter or CodexAdapter()

    def _instruction(self, plan, task, changed_files, patch, validation_results, previous_findings):
        return (
            "Review the supplied AGF patch. Return exactly one JSON object, no Markdown fences, "
            "and no commentary. Use only the canonical status values APPROVE, REQUEST_CHANGES, "
            "REJECT, or HUMAN_REQUIRED; do not use aliases. APPROVE requires findings=[]; "
            "REQUEST_CHANGES requires an actionable finding; REJECT requires a P0/P1 finding; "
            "HUMAN_REQUIRED requires a precise summary.\n"
            f"Task objective: {task.objective}\nAcceptance criteria: {task.acceptance_criteria}\n"
            f"Allowed paths: {task.allowed_paths}\nChanged paths: {changed_files}\n"
            f"Unified patch:\n{patch}\nExact validation results: {validation_results}\n"
            f"Architecture impact: {plan.architecture_impact}\n"
            f"Previous accepted findings: {previous_findings or []}\n"
            f"Required JSON schema: {REVIEW_SCHEMA}"
        )

    def _process_report(self, process):
        if process.human_required:
            return _invalid(self.name, "CODEX_REVIEW_TRANSPORT_UNVERIFIED: invocation not verified")
        if process.timed_out:
            return _invalid(self.name, "CODEX_REVIEW_TIMEOUT: reviewer timed out")
        if process.exit_code != 0:
            return _invalid(self.name, "CODEX_REVIEW_PROCESS_FAILED: non-zero exit code")
        if process.transport_error or process.final_message is None:
            return _invalid(
                self.name,
                process.transport_error or "FINAL_MESSAGE_MISSING: final-message artifact missing",
            )
        return parse_structured_review(process.final_message, self.name)

    def review(self, plan, task, changed_files, patch, validation_results, previous_findings=None):
        instruction = self._instruction(
            plan, task, changed_files, patch, validation_results, previous_findings
        )
        process = self.adapter.execute(instruction, plan.repository.root, sandbox="read-only")
        report = self._process_report(process)
        if report.status is not ReviewStatus.HUMAN_REQUIRED:
            return report
        schema_error = next(
            (item for item in report.blocking_issues if item.startswith("REVIEW_")), None
        )
        if schema_error is None:
            return report
        repair = (
            "The previous response failed validation with code " + schema_error.split(":", 1)[0]
            + ". Return exactly one JSON object, with no Markdown fences or commentary.\n"
            f"Required JSON schema: {REVIEW_SCHEMA}"
        )
        repaired = self.adapter.execute(repair, plan.repository.root, sandbox="read-only")
        repaired_report = self._process_report(repaired)
        if repaired_report.status is ReviewStatus.HUMAN_REQUIRED:
            return ReviewReport(
                self.name,
                ReviewStatus.HUMAN_REQUIRED,
                repaired_report.findings,
                ["schema repair attempted once"],
                repaired_report.blocking_issues,
                repaired_report.summary,
                repaired_report.checks_performed,
                repaired_report.residual_risks,
            )
        return ReviewReport(
            repaired_report.reviewer,
            repaired_report.status,
            repaired_report.findings,
            ["schema repair attempted once", *repaired_report.evidence],
            repaired_report.blocking_issues,
            repaired_report.summary,
            repaired_report.checks_performed,
            repaired_report.residual_risks,
        )
