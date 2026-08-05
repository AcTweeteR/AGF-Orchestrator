import json
from pathlib import Path

import pytest

from agf_orchestrator.roadmap_models import roadmap_from_dict
from agf_orchestrator.scheduler_models import SchedulerStatus
from agf_orchestrator.scheduler_safety import (
    ProgressObservation,
    SafetyGateError,
    SafetyStatus,
    assess_progress,
    assess_roadmap,
)

ROOT = Path(__file__).parent


def roadmap_payload():
    return json.loads((ROOT / "fixtures/roadmaps/valid_roadmap.json").read_text())


def observation(sequence, item="item-backlog"):
    return ProgressObservation("scheduler-main", SchedulerStatus.RUNNING, item, sequence)


def test_no_progress_gate_blocks_repeated_identical_observations():
    observations = (observation(1), observation(1), observation(1))
    decision = assess_progress(observations, max_stalls=3)

    assert decision.status is SafetyStatus.BLOCKED
    assert decision.stalled_observations == 3


def test_progress_gate_honors_human_and_terminal_boundaries():
    human = ProgressObservation("scheduler-main", SchedulerStatus.HUMAN_REQUIRED, None, 2)
    complete = ProgressObservation("scheduler-main", SchedulerStatus.COMPLETED, None, 3)
    assert assess_progress((human,), max_stalls=2).status is SafetyStatus.HUMAN_REQUIRED
    assert assess_progress((complete,), max_stalls=2).status is SafetyStatus.COMPLETED
    assert (
        assess_progress((observation(1), observation(2)), max_stalls=2).status
        is SafetyStatus.PROCEED
    )


def test_roadmap_gate_detects_dependency_block_and_completion():
    ready = roadmap_from_dict(roadmap_payload())
    assert assess_roadmap(ready).status is SafetyStatus.PROCEED

    blocked_payload = roadmap_payload()
    blocked_payload["items"][0]["status"] = "TODO"
    blocked = roadmap_from_dict(blocked_payload)
    assert assess_roadmap(blocked).status is SafetyStatus.BLOCKED

    complete_payload = roadmap_payload()
    complete_payload["items"][1]["status"] = "COMPLETED"
    complete = roadmap_from_dict(complete_payload)
    assert assess_roadmap(complete).status is SafetyStatus.COMPLETED


def test_safety_gate_bounds_observations():
    with pytest.raises(SafetyGateError, match="max_stalls"):
        assess_progress((observation(1),), max_stalls=0)
