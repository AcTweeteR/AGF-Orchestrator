"""Bounded confidence and regression summaries over accepted evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from .learning_evidence import LearningEvidence, LearningEvidenceError

_SHA256 = set("0123456789abcdef")


class LearningSummaryError(ValueError):
    """Raised when summary inputs are stale, contradictory, or out of bounds."""


@dataclass(frozen=True)
class LearningSummary:
    schema_version: str
    summary_id: str
    project_id: str
    subject_id: str
    sample_count: int
    bounded_score: int
    confidence: int
    regression_detected: bool
    evidence_ids: tuple[str, ...]
    evidence_sha256: tuple[str, ...]
    input_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "summary_id": self.summary_id,
            "project_id": self.project_id,
            "subject_id": self.subject_id,
            "sample_count": self.sample_count,
            "bounded_score": self.bounded_score,
            "confidence": self.confidence,
            "regression_detected": self.regression_detected,
            "evidence_ids": list(self.evidence_ids),
            "evidence_sha256": list(self.evidence_sha256),
            "input_sha256": self.input_sha256,
        }

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise LearningSummaryError("schema_version must be 1.0")
        if not isinstance(self.summary_id, str) or not self.summary_id.startswith("summary-"):
            raise LearningSummaryError("summary_id is invalid")
        if not isinstance(self.project_id, str) or not self.project_id.startswith("project-"):
            raise LearningSummaryError("project_id is invalid")
        if not isinstance(self.subject_id, str) or not self.subject_id.startswith("subject-"):
            raise LearningSummaryError("subject_id is invalid")
        if not isinstance(self.sample_count, int) or self.sample_count < 1:
            raise LearningSummaryError("sample_count is invalid")
        if not -100 <= self.bounded_score <= 100:
            raise LearningSummaryError("bounded_score is outside the bound")
        if not 0 <= self.confidence <= 100:
            raise LearningSummaryError("confidence is outside the bound")
        if not isinstance(self.regression_detected, bool):
            raise LearningSummaryError("regression_detected is invalid")
        if not self.evidence_ids or len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise LearningSummaryError("evidence_ids are invalid")
        if (
            not self.evidence_sha256
            or len(self.evidence_sha256) != len(self.evidence_ids)
            or any(
                not isinstance(item, str)
                or len(item) != 64
                or any(char not in _SHA256 for char in item)
                for item in self.evidence_sha256
            )
        ):
            raise LearningSummaryError("evidence_sha256 is invalid")
        if not isinstance(self.input_sha256, str) or len(self.input_sha256) != 64:
            raise LearningSummaryError("input_sha256 is invalid")
        if self.input_sha256 != summary_input_hash(self):
            raise LearningSummaryError("input_sha256 does not match inputs")


def summarize_evidence(
    evidence: Iterable[LearningEvidence],
    *,
    now: str,
    prior_score: int | None = None,
    max_age_seconds: int = 86_400,
) -> LearningSummary:
    """Return a deterministic bounded summary without mutating any ledger."""
    records = tuple(sorted(evidence, key=lambda item: (item.observation_id, item.evidence_id)))
    if not records:
        raise LearningSummaryError("evidence must not be empty")
    if not isinstance(max_age_seconds, int) or max_age_seconds < 0:
        raise LearningSummaryError("max_age_seconds is invalid")
    if prior_score is not None and (
        not isinstance(prior_score, int)
        or isinstance(prior_score, bool)
        or not -100 <= prior_score <= 100
    ):
        raise LearningSummaryError("prior_score is outside the bound")
    current = _parse_timestamp(now)
    first = records[0]
    for item in records:
        try:
            item.validate()
        except LearningEvidenceError as exc:
            raise LearningSummaryError(str(exc)) from exc
        if item.project_id != first.project_id or item.subject_id != first.subject_id:
            raise LearningSummaryError("evidence binding is inconsistent")
        age = (current - _parse_timestamp(item.observed_at)).total_seconds()
        if age < 0 or age > max_age_seconds:
            raise LearningSummaryError("evidence is stale or from the future")
    if len({item.observation_id for item in records}) != len(records):
        raise LearningSummaryError("duplicate evidence is contradictory")
    mean_delta = sum(item.score_delta for item in records) / len(records)
    scale = 2 if len(records) == 1 else 10
    bounded_score = max(-100, min(100, round(mean_delta * scale)))
    confidence = min(100, len(records) * 20)
    regression = prior_score is not None and bounded_score < prior_score - 20
    summary = LearningSummary(
        "1.0", f"summary-{first.subject_id.removeprefix('subject-')}", first.project_id,
        first.subject_id, len(records), bounded_score, confidence, regression,
        tuple(item.evidence_id for item in records),
        tuple(item.content_sha256 for item in records), "0" * 64,
    )
    summary = LearningSummary(**{**summary.__dict__, "input_sha256": summary_input_hash(summary)})
    summary.validate()
    return summary


def summary_input_hash(summary: LearningSummary) -> str:
    payload = summary.to_dict()
    payload["input_sha256"] = ""
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, allow_nan=False,
                   separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _parse_timestamp(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise LearningSummaryError("timestamp is invalid") from exc
