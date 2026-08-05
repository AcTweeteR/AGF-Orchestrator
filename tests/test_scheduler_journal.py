import json

import pytest

from agf_orchestrator.scheduler_journal import (
    InboxItem,
    SchedulerJournal,
    SchedulerJournalError,
)
from agf_orchestrator.scheduler_loop import SchedulerEvent


def journal(tmp_path):
    return SchedulerJournal(tmp_path, "project-agf-orchestrator", "scheduler-main")


def event(sequence):
    return SchedulerEvent(
        f"event-{sequence:06d}", sequence, "STATE_TRANSITION", "RUNNING", "PAUSED", "step completed"
    )


def inbox(inbox_id="inbox-000001"):
    return InboxItem(
        inbox_id, "project-agf-orchestrator", "scheduler-main", "Human action",
        "A bounded action is required.", "Review and resume.",
    )


def test_journal_persists_events_and_inbox_with_idempotent_retries(tmp_path):
    store = journal(tmp_path)
    store.append_event(event(1))
    store.append_event(event(1))
    store.add_inbox(inbox())
    store.add_inbox(inbox())

    assert store.audit() == (event(1),)
    assert store.open_inbox() == (inbox(),)
    assert json.loads(store.path.read_text())["events"][0]["event_id"] == "event-000001"


def test_journal_requires_monotonic_events_and_is_project_isolated(tmp_path):
    store = journal(tmp_path)
    store.append_event(event(1))
    with pytest.raises(SchedulerJournalError, match="monotonic"):
        store.append_event(event(3))

    foreign = InboxItem(
        "inbox-000002", "project-other", "scheduler-main", "Title", "Summary", "Action"
    )
    with pytest.raises(SchedulerJournalError, match="identity"):
        store.add_inbox(foreign)


def test_journal_rejects_secret_text_and_unbounded_reads(tmp_path):
    store = journal(tmp_path)
    with pytest.raises(SchedulerJournalError, match="invalid"):
        store.add_inbox(
            InboxItem(
                "inbox-000002", "project-agf-orchestrator", "scheduler-main",
                "token: value", "Summary", "Action",
            )
        )
    with pytest.raises(SchedulerJournalError, match="limit"):
        store.audit(limit=501)
