import json

from agf_orchestrator.adapters.codex import CodexProcessResult
from agf_orchestrator.models import ExecutionPlan, PlanStatus, RepositoryContext, Task
from agf_orchestrator.review_models import ReviewStatus
from agf_orchestrator.reviewer import (
    CodexReviewerAdapter,
    DeterministicReviewer,
    parse_structured_review,
)


def plan_and_task():
    task = Task(
        "task-001", "Update file", "Update allowed.txt", ["allowed.txt"], [],
        ["allowed.txt contains after"], ["python -B -c \"assert True\""],
        "low", "Implementer", PlanStatus.READY,
    )
    plan = ExecutionPlan(
        "1.0", "plan-review", "1970-01-01T00:00:00Z",
        RepositoryContext("/repo", "feature", "origin", True, "abc"),
        "Update file", {"in": ["allowed.txt"]}, [], [],
        {"status": "approved", "requires_architect": False}, [task], [],
        [[task.task_id]], [], [], [], PlanStatus.READY,
    )
    return plan, task


def test_deterministic_reviewer_approves_bounded_validated_patch():
    plan, task = plan_and_task()
    report = DeterministicReviewer().review(
        plan, task, ["allowed.txt"], "@@ -1 +1 @@\n-before\n+after\n",
        ["validation python: exit_code=0; stdout=; stderr="],
    )
    assert report.status is ReviewStatus.APPROVE
    assert report.findings == []


def test_deterministic_reviewer_requests_changes_for_scope_or_validation():
    plan, task = plan_and_task()
    report = DeterministicReviewer().review(
        plan, task, ["allowed.txt", "secret.txt"], "patch",
        ["validation false: exit_code=1; stdout=; stderr="],
    )
    assert report.status is ReviewStatus.REQUEST_CHANGES
    assert {finding.code for finding in report.findings} == {"REV-SCOPE", "REV-TESTS"}


def structured(status="APPROVE", findings=None):
    return (
        '{"schema_version":"1.0","status":"%s","summary":"reviewed",'
        '"findings":%s,"checks_performed":["scope"],"residual_risks":["optional"]}'
    ) % (status, json.dumps(findings or []))


def finding(severity="blocker", path="allowed.txt"):
    return {
        "finding_id": "REV-001",
        "category": "CORRECTNESS",
        "severity": severity,
        "message": "specific defect",
        "affected_paths": [path],
        "evidence": "patch line 1",
        "required_change": "replace the incorrect value",
    }


def test_structured_approve_and_request_changes_preserve_exact_findings():
    approved = parse_structured_review(structured())
    assert approved.status is ReviewStatus.APPROVE
    requested = parse_structured_review(structured("REQUEST_CHANGES", [finding()]))
    assert requested.status is ReviewStatus.REQUEST_CHANGES
    assert requested.findings[0].finding_id == "REV-001"
    assert requested.findings[0].required_change == "replace the incorrect value"


def test_invalid_or_ambiguous_structured_review_requires_human():
    assert parse_structured_review("not json").status is ReviewStatus.HUMAN_REQUIRED
    missing = '{"schema_version":"1.0","status":"APPROVE"}'
    assert parse_structured_review(missing).status is ReviewStatus.HUMAN_REQUIRED
    assert (
        parse_structured_review(structured("APPROVE", [finding()])).status
        is ReviewStatus.HUMAN_REQUIRED
    )
    assert (
        parse_structured_review(structured("REQUEST_CHANGES", [])).status
        is ReviewStatus.HUMAN_REQUIRED
    )
    assert parse_structured_review("APPROVE: looks good").status is ReviewStatus.HUMAN_REQUIRED


def test_optional_minor_finding_does_not_block_approval():
    report = parse_structured_review(structured("APPROVE", [finding("minor")]))
    assert report.status is ReviewStatus.APPROVE
    assert report.findings[0].severity == "minor"


class StubCodex:
    def __init__(self, output, final_message=None):
        self.output = output
        self.final_message = final_message if final_message is not None else output
        self.instruction = ""

    def execute(self, instruction, repository, *, sandbox):
        self.instruction = instruction
        return CodexProcessResult("review", 0, self.output, "", final_message=self.final_message)


def test_codex_reviewer_prompt_contains_exact_review_context_and_redacts_findings():
    plan, task = plan_and_task()
    stub = StubCodex(structured("REQUEST_CHANGES", [finding()]))
    report = CodexReviewerAdapter(stub).review(
        plan, task, ["allowed.txt"], "unified patch", ["exit_code=0"], []
    )
    assert report.status is ReviewStatus.REQUEST_CHANGES
    assert "Acceptance criteria" in stub.instruction
    assert "unified patch" in stub.instruction
    assert report.findings[0].finding_id == "REV-001"


def test_status_aliases_normalize_to_canonical_values_and_record_evidence():
    cases = {
        "APPROVE": ReviewStatus.APPROVE,
        "approve": ReviewStatus.APPROVE,
        "approved": ReviewStatus.APPROVE,
        "ApPrOvEd": ReviewStatus.APPROVE,
        "request_changes": ReviewStatus.REQUEST_CHANGES,
        "requested changes": ReviewStatus.REQUEST_CHANGES,
        "rejected": ReviewStatus.REJECT,
        "human required": ReviewStatus.HUMAN_REQUIRED,
    }
    for value, expected in cases.items():
        findings = [finding()] if expected is ReviewStatus.REQUEST_CHANGES else []
        report = parse_structured_review(structured(value, findings))
        assert report.status is expected
        if value != expected.value:
            assert f"review status normalized: {value} -> {expected.value}" in report.evidence


def test_unknown_sentence_and_empty_status_are_rejected():
    assert (
        parse_structured_review(structured("approved and safe")).status
        is ReviewStatus.HUMAN_REQUIRED
    )
    assert parse_structured_review(structured("")).status is ReviewStatus.HUMAN_REQUIRED
    assert parse_structured_review(structured("maybe")).status is ReviewStatus.HUMAN_REQUIRED


def test_final_message_artifact_takes_precedence_over_stdout_diagnostics():
    plan, task = plan_and_task()
    final = structured("approved")
    report = CodexReviewerAdapter(StubCodex("not json", final)).review(
        plan, task, ["allowed.txt"], "patch", ["exit_code=0"], []
    )
    assert report.status is ReviewStatus.APPROVE


def test_missing_final_message_artifact_has_precise_transport_failure():
    plan, task = plan_and_task()
    stub = StubCodex(structured("APPROVE"), final_message=None)
    stub.final_message = None
    report = CodexReviewerAdapter(stub).review(
        plan, task, ["allowed.txt"], "patch", ["exit_code=0"], []
    )
    assert report.status is ReviewStatus.HUMAN_REQUIRED
    assert report.blocking_issues == ["FINAL_MESSAGE_MISSING: final-message artifact missing"]


def test_normalized_semantic_rules_still_apply():
    blocked = parse_structured_review(structured("approved", [finding()]))
    assert blocked.status is ReviewStatus.HUMAN_REQUIRED
    not_actionable = parse_structured_review(structured("requested changes"))
    assert not_actionable.status is ReviewStatus.HUMAN_REQUIRED
