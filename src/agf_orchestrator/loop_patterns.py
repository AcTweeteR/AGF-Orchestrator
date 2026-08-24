"""Reusable loop patterns that describe procedure composition without authority."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LoopPattern:
    pattern_id: str
    required_capabilities: tuple[str, ...]
    required_evidence: tuple[str, ...]
    external_mutation: bool
    auto_merge: bool
    respects_kill_switch: bool
    finite_progress_required: bool


PATTERNS = {
    "ci-repair": LoopPattern(
        pattern_id="ci-repair",
        required_capabilities=("repository-understanding", "ci-repair"),
        required_evidence=("failing-ci-evidence", "tests", "review"),
        external_mutation=False,
        auto_merge=False,
        respects_kill_switch=True,
        finite_progress_required=True,
    ),
    "pr-babysitter": LoopPattern(
        pattern_id="pr-babysitter",
        required_capabilities=("repository-understanding", "pr-observation"),
        required_evidence=("pr-state", "review"),
        external_mutation=False,
        auto_merge=False,
        respects_kill_switch=True,
        finite_progress_required=True,
    ),
    "issue-triage": LoopPattern(
        pattern_id="issue-triage",
        required_capabilities=("repository-understanding", "issue-triage"),
        required_evidence=("issue-evidence", "triage-report"),
        external_mutation=False,
        auto_merge=False,
        respects_kill_switch=True,
        finite_progress_required=True,
    ),
    "dependency-update": LoopPattern(
        pattern_id="dependency-update",
        required_capabilities=("dependency-analysis", "repository-understanding"),
        required_evidence=("dependency-evidence", "tests", "review"),
        external_mutation=False,
        auto_merge=False,
        respects_kill_switch=True,
        finite_progress_required=True,
    ),
    "release-prep": LoopPattern(
        pattern_id="release-prep",
        required_capabilities=("release-analysis", "repository-understanding"),
        required_evidence=("tests", "review", "release-readiness"),
        external_mutation=False,
        auto_merge=False,
        respects_kill_switch=True,
        finite_progress_required=True,
    ),
}


def get_pattern(pattern_id: str) -> LoopPattern:
    try:
        return PATTERNS[pattern_id]
    except KeyError as exc:
        raise ValueError("unknown governed loop pattern") from exc
