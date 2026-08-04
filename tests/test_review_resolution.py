import json

from agf_orchestrator.delivery import _review_resolution_states
from agf_orchestrator.review_models import (
    FindingResolution,
    ReviewFinding,
    ReviewReport,
    ReviewStatus,
    finding_identity,
)
from agf_orchestrator.reviewer import parse_structured_review


def review_finding(*, line=1, message="Python 3 is not explicit", required="Use python3"):
    return ReviewFinding(
        "BOUNDED_MACHINE_CODE",
        "REVIEW",
        "major",
        message,
        ["CONTRIBUTING.md"],
        f"CONTRIBUTING.md:{line}",
        required,
    )


def review(status, findings, resolved=None):
    return ReviewReport(
        "fake-codex",
        status,
        findings,
        [],
        [],
        "bounded review",
        [],
        [],
        resolved,
    )


def test_line_changes_do_not_change_finding_identity():
    assert finding_identity(review_finding(line=87)) == finding_identity(review_finding(line=91))


def test_prior_finding_explicitly_resolved():
    prior = review_finding()
    states, errors = _review_resolution_states(
        review(ReviewStatus.APPROVE, [], [finding_identity(prior)]), [prior]
    )
    assert errors == []
    assert states[finding_identity(prior)] is FindingResolution.RESOLVED


def test_prior_finding_remains_open():
    prior = review_finding()
    states, errors = _review_resolution_states(
        review(ReviewStatus.REQUEST_CHANGES, [review_finding(line=91)], []), [prior]
    )
    assert errors == []
    assert states[finding_identity(prior)] is FindingResolution.UNCHANGED


def test_materially_changed_finding_is_replaced_not_resolved_implicitly():
    prior = review_finding()
    replacement = review_finding(
        message="Offline modules are undocumented", required="Document modules"
    )
    states, errors = _review_resolution_states(
        review(ReviewStatus.REQUEST_CHANGES, [replacement], [finding_identity(prior)]), [prior]
    )
    assert errors == []
    assert states[finding_identity(prior)] is FindingResolution.RESOLVED
    assert states[finding_identity(replacement)] is FindingResolution.NEW


def test_new_finding_can_be_added_after_correction():
    prior = review_finding()
    added = review_finding(message="Offline modules are unclear", required="Document modules")
    states, errors = _review_resolution_states(
        review(ReviewStatus.REQUEST_CHANGES, [review_finding(line=91), added], []), [prior]
    )
    assert errors == []
    assert states[finding_identity(prior)] is FindingResolution.UNCHANGED
    assert states[finding_identity(added)] is FindingResolution.NEW


def test_unknown_resolved_id_is_rejected():
    prior = review_finding()
    _, errors = _review_resolution_states(
        review(ReviewStatus.APPROVE, [], ["unknown-finding"]), [prior]
    )
    assert "unknown finding" in errors[0]


def test_duplicate_resolved_id_is_rejected_by_schema():
    payload = {
        "status": "REQUEST_CHANGES",
        "summary": "bounded review",
        "findings": [],
        "resolved_finding_ids": ["same", "same"],
    }
    report = parse_structured_review(json.dumps(payload))
    assert report.status is ReviewStatus.HUMAN_REQUIRED
    assert report.blocking_issues == [
        "REVIEW_RESOLUTION_INVALID: resolved_finding_ids is invalid or duplicated"
    ]


def test_approve_with_unresolved_prior_finding_is_rejected():
    prior = review_finding()
    _, errors = _review_resolution_states(review(ReviewStatus.APPROVE, [], []), [prior])
    assert errors == ["APPROVE did not resolve every prior finding"]


def test_request_changes_with_partial_resolution_is_accepted():
    first = review_finding()
    second = review_finding(message="Offline modules are unclear", required="Document modules")
    states, errors = _review_resolution_states(
        review(
            ReviewStatus.REQUEST_CHANGES,
            [second],
            [finding_identity(first)],
        ),
        [first, second],
    )
    assert errors == []
    assert states[finding_identity(first)] is FindingResolution.RESOLVED
    assert states[finding_identity(second)] is FindingResolution.UNCHANGED


def test_missing_resolution_data_is_unverifiable():
    prior = review_finding()
    states, errors = _review_resolution_states(
        review(ReviewStatus.REQUEST_CHANGES, [], None), [prior]
    )
    assert states[finding_identity(prior)] is FindingResolution.UNVERIFIABLE
    assert errors == [
        "REQUEST_CHANGES omitted resolved_finding_ids",
        "prior finding was omitted without explicit resolution",
    ]
