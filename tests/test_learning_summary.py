from dataclasses import replace

import pytest

from agf_orchestrator.learning_evidence import (
    LearningEvidence,
    OutcomeStatus,
    evidence_hash,
)
from agf_orchestrator.learning_summary import LearningSummaryError, summarize_evidence

PROJECT = "project-efc8e8ef7be7050b"


def evidence(observation, delta, observed_at="2026-08-10T12:00:00Z"):
    item = LearningEvidence(
        "1.0", f"learning-{observation}", PROJECT, f"observation-{observation}",
        "subject-codex", OutcomeStatus.SUCCESS, delta, "pilot:test", observed_at, "0" * 64,
    )
    return replace(item, content_sha256=evidence_hash(item))


def test_summary_is_bounded_and_deterministic():
    records = (evidence("001", 1), evidence("002", 2))
    first = summarize_evidence(records, now="2026-08-10T12:00:01Z")
    second = summarize_evidence(records, now="2026-08-10T12:00:01Z")
    assert first == second
    assert first.bounded_score == 15
    assert first.confidence == 40
    assert first.evidence_sha256 == tuple(item.content_sha256 for item in records)


def test_summary_is_order_independent_and_content_bound():
    records = (evidence("001", 1), evidence("002", 2))
    assert summarize_evidence(records, now="2026-08-10T12:00:00Z") == summarize_evidence(
        tuple(reversed(records)), now="2026-08-10T12:00:00Z"
    )
    changed = replace(records[0], source="pilot:changed", content_sha256="0" * 64)
    changed = replace(changed, content_sha256=evidence_hash(changed))
    altered = summarize_evidence(
        (changed, records[1]), now="2026-08-10T12:00:00Z"
    )
    assert altered.input_sha256 != summarize_evidence(
        records, now="2026-08-10T12:00:00Z"
    ).input_sha256


def test_single_result_cannot_create_extreme_score():
    summary = summarize_evidence((evidence("001", 10),), now="2026-08-10T12:00:00Z")
    assert -100 < summary.bounded_score < 100


def test_stale_future_and_contradictory_inputs_fail_closed():
    with pytest.raises(LearningSummaryError, match="stale"):
        summarize_evidence(
            (evidence("001", 1, "2026-08-08T12:00:00Z"),), now="2026-08-10T12:00:00Z"
        )
    with pytest.raises(LearningSummaryError, match="future"):
        summarize_evidence(
            (evidence("001", 1, "2026-08-10T13:00:00Z"),), now="2026-08-10T12:00:00Z"
        )
    with pytest.raises(LearningSummaryError, match="contradictory"):
        summarize_evidence((evidence("001", 1), evidence("001", 2)), now="2026-08-10T12:00:00Z")


def test_inconsistent_project_or_subject_is_rejected():
    other = evidence("002", 1)
    other = replace(other, subject_id="subject-other", content_sha256="0" * 64)
    other = replace(other, content_sha256=evidence_hash(other))
    with pytest.raises(LearningSummaryError, match="inconsistent"):
        summarize_evidence((evidence("001", 1), other), now="2026-08-10T12:00:00Z")


def test_regression_is_detected_without_mutating_prior_score():
    summary = summarize_evidence(
        (evidence("001", -10), evidence("002", -10)),
        now="2026-08-10T12:00:00Z", prior_score=50,
    )
    assert summary.regression_detected
    assert summary.bounded_score == -100


def test_prior_score_and_summary_hashes_are_bounded_and_typed():
    current = (evidence("001", 1),)
    with pytest.raises(LearningSummaryError, match="prior_score"):
        summarize_evidence(current, now="2026-08-10T12:00:00Z", prior_score=101)
    with pytest.raises(LearningSummaryError, match="prior_score"):
        summarize_evidence(current, now="2026-08-10T12:00:00Z", prior_score="bad")
    summary = summarize_evidence(current, now="2026-08-10T12:00:00Z")
    with pytest.raises(LearningSummaryError, match="evidence_sha256"):
        replace(summary, evidence_sha256=("z" * 64,)).validate()
