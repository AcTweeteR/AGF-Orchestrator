import json
from pathlib import Path

import pytest

from agf_orchestrator.scheduler_commands import (
    SchedulerCommand,
    SchedulerCommandError,
    SchedulerCommandService,
)
from agf_orchestrator.scheduler_models import scheduler_from_dict
from agf_orchestrator.scheduler_store import SchedulerStore

FIXTURE = Path(__file__).parent / "fixtures" / "scheduler" / "valid_state.json"


def service(tmp_path):
    state = scheduler_from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))
    store = SchedulerStore(tmp_path, state.project_id, state.scheduler_id)
    store.create(state)
    return SchedulerCommandService(store)


def test_commands_start_pause_resume_and_cancel_with_idempotent_retry(tmp_path):
    commands = service(tmp_path)

    assert commands.execute(SchedulerCommand.START).state.status.value == "RUNNING"
    paused = commands.execute(SchedulerCommand.PAUSE)
    assert paused.state.status.value == "PAUSED"
    assert commands.execute(SchedulerCommand.PAUSE).changed is False
    assert commands.execute(SchedulerCommand.RESUME).state.status.value == "RUNNING"
    cancelled = commands.execute(SchedulerCommand.CANCEL)
    assert cancelled.state.status.value == "CANCELLED"
    assert commands.execute(SchedulerCommand.CANCEL).changed is False


def test_status_and_audit_are_read_only_bounded_snapshots(tmp_path):
    commands = service(tmp_path)
    before = commands.execute(SchedulerCommand.STATUS)
    audit = commands.execute(SchedulerCommand.AUDIT)

    assert before.changed is False
    assert audit.changed is False
    assert audit.evidence == ("audit snapshot", "event_sequence=0")
    assert commands.execute(SchedulerCommand.STATUS).state == before.state


def test_forbidden_command_and_missing_store_fail_closed(tmp_path):
    commands = service(tmp_path)
    with pytest.raises(SchedulerCommandError, match="not allowed"):
        commands.execute(SchedulerCommand.PAUSE)

    missing = SchedulerCommandService(
        SchedulerStore(tmp_path, "project-other", "scheduler-other")
    )
    with pytest.raises(SchedulerCommandError, match="not found"):
        missing.execute(SchedulerCommand.STATUS)
