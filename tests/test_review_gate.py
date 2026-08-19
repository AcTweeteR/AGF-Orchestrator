import pytest

from agf_orchestrator.review_gate import (
    ReviewGateError,
    parse_independent_review,
    review_gate_ready,
)


def evidence(**overrides):
    value = {
        "schema_version": "1.0",
        "project_id": "project-ai-fund",
        "repository": "AcTweeteR/ai-virtual-fund",
        "head_sha": "a" * 40,
        "implementer": "AcTweeteR",
        "reviewer": "qwen3.5:9b-q4_K_M",
        "decision": "APPROVE",
        "findings": [],
        "evidence_id": "review-1",
        "provenance": "provider-intelligence:reviewer-canary-1",
        "issued_at": "2026-08-19T10:00:00Z",
    }
    value.update(overrides)
    return value


def test_valid_independent_review_is_accepted():
    result = parse_independent_review(
        evidence(), expected_project_id="project-ai-fund",
        expected_repository="AcTweeteR/ai-virtual-fund", expected_head_sha="a" * 40,
        implementer="AcTweeteR",
    )
    assert result.reviewer == "qwen3.5:9b-q4_K_M"


@pytest.mark.parametrize("change", [
    {"reviewer": "AcTweeteR"},
    {"head_sha": "b" * 40},
    {"decision": "COMMENT"},
    {"project_id": "other"},
])
def test_invalid_or_stale_review_is_rejected(change):
    with pytest.raises(ReviewGateError):
        parse_independent_review(
            evidence(**change), expected_project_id="project-ai-fund",
            expected_repository="AcTweeteR/ai-virtual-fund", expected_head_sha="a" * 40,
            implementer="AcTweeteR",
        )


def test_unresolved_p1_p2_and_replay_are_rejected():
    with pytest.raises(ReviewGateError):
        parse_independent_review(
            evidence(findings=[{"severity": "P1", "status": "OPEN"}]),
            expected_project_id="project-ai-fund",
            expected_repository="AcTweeteR/ai-virtual-fund", expected_head_sha="a" * 40,
            implementer="AcTweeteR",
        )
    with pytest.raises(ReviewGateError):
        parse_independent_review(
            evidence(), expected_project_id="project-ai-fund",
            expected_repository="AcTweeteR/ai-virtual-fund", expected_head_sha="a" * 40,
            implementer="AcTweeteR", seen_evidence_ids={"review-1"},
        )


def test_formal_approval_is_only_required_when_policy_requires_it():
    review = parse_independent_review(
        evidence(), expected_project_id="project-ai-fund",
        expected_repository="AcTweeteR/ai-virtual-fund", expected_head_sha="a" * 40,
        implementer="AcTweeteR",
    )
    assert review_gate_ready(
        ci_passed=True, github_state="OPEN", github_approved=False,
        independent_review=review, formal_github_approval_required=False,
    )
    assert not review_gate_ready(
        ci_passed=True, github_state="OPEN", github_approved=False,
        independent_review=review, formal_github_approval_required=True,
    )
