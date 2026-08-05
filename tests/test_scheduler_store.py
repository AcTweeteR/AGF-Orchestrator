import json
from pathlib import Path

import pytest

from agf_orchestrator.scheduler_models import SchedulerStatus, scheduler_from_dict
from agf_orchestrator.scheduler_store import SchedulerStore, SchedulerStoreError

FIXTURE = Path(__file__).parent / "fixtures" / "scheduler" / "valid_state.json"


def state():
    return scheduler_from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))


def test_scheduler_store_persists_and_resumes_after_restart(tmp_path):
    store = SchedulerStore(tmp_path, "project-agf-orchestrator", "scheduler-main")
    initial = state()

    assert store.create(initial) == initial
    running = store.transition(SchedulerStatus.RUNNING)
    assert SchedulerStore(tmp_path, initial.project_id, initial.scheduler_id).load() == running
    assert store.transition(SchedulerStatus.RUNNING) == running
    assert json.loads(store.path.read_text())["status"] == "RUNNING"


def test_scheduler_store_rejects_invalid_or_conflicting_transitions(tmp_path):
    store = SchedulerStore(tmp_path, "project-agf-orchestrator", "scheduler-main")
    store.create(state())

    with pytest.raises(SchedulerStoreError, match="invalid scheduler transition"):
        store.transition(SchedulerStatus.COMPLETED)

    conflict = state()
    conflict = conflict.transition(SchedulerStatus.RUNNING)
    conflict = conflict.transition(SchedulerStatus.PAUSED)
    with pytest.raises(SchedulerStoreError, match="already exists"):
        store.create(conflict)


def test_scheduler_store_is_project_isolated(tmp_path):
    first = SchedulerStore(tmp_path, "project-agf-orchestrator", "scheduler-main")
    second = SchedulerStore(tmp_path, "project-other", "scheduler-main")
    first.create(state())

    with pytest.raises(SchedulerStoreError, match="not found"):
        second.load()
    assert first.load().project_id == "project-agf-orchestrator"
