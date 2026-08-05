import dataclasses
import json
from pathlib import Path

import pytest

from agf_orchestrator.engineering_memory_models import memory_entry_hash, memory_from_dict
from agf_orchestrator.engineering_memory_store import (
    EngineeringMemoryStore,
    MemoryStoreError,
)

FIXTURE = Path(__file__).parent / "fixtures" / "memory" / "valid_entry.json"


def entry(entry_id, title=None, superseded_at=None):
    base = memory_from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))
    candidate = dataclasses.replace(
        base, entry_id=entry_id, title=title or base.title,
        superseded_at=superseded_at, content_sha256="0" * 64,
    )
    return dataclasses.replace(candidate, content_sha256=memory_entry_hash(candidate))


def test_put_is_atomic_restartable_and_idempotent(tmp_path):
    store = EngineeringMemoryStore(tmp_path, "project-agf-orchestrator")
    item = entry("memory-first")

    assert store.put(item) == item
    assert store.put(item) == item
    assert store.get(item.entry_id) == item
    assert json.loads(store.path.read_text()) ["entries"][0]["entry_id"] == item.entry_id


def test_search_is_bounded_deterministic_and_excludes_superseded(tmp_path):
    store = EngineeringMemoryStore(tmp_path, "project-agf-orchestrator")
    store.put(entry("memory-zeta", "Priority decision"))
    store.put(entry("memory-alpha", "Priority invariant"))
    store.put(entry("memory-old", "Priority old", "2026-08-05T01:00:00Z"))

    assert [item.entry_id for item in store.search("priority")] == [
        "memory-alpha", "memory-zeta"
    ]
    assert len(store.search("priority", limit=1)) == 1


def test_cross_project_writes_and_conflicting_retries_are_blocked(tmp_path):
    store = EngineeringMemoryStore(tmp_path, "project-agf-orchestrator")
    foreign_candidate = dataclasses.replace(
        entry("memory-foreign"), project_id="project-other", content_sha256="0" * 64
    )
    foreign = dataclasses.replace(
        foreign_candidate, content_sha256=memory_entry_hash(foreign_candidate)
    )
    with pytest.raises(MemoryStoreError, match="another project"):
        store.put(foreign)

    item = entry("memory-conflict")
    store.put(item)
    conflict = entry("memory-conflict", "Different content")
    with pytest.raises(MemoryStoreError, match="different content"):
        store.put(conflict)


@pytest.mark.parametrize("query, limit", [("x" * 401, 50), ("query", 0), ("query", 201)])
def test_search_bounds_are_enforced(tmp_path, query, limit):
    store = EngineeringMemoryStore(tmp_path, "project-agf-orchestrator")
    with pytest.raises(MemoryStoreError, match="invalid"):
        store.search(query, limit=limit)
