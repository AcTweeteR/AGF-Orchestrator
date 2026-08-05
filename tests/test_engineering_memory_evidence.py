import json
from pathlib import Path

import pytest

from agf_orchestrator.director import Director
from agf_orchestrator.engineering_memory_evidence import MemoryEvidenceError
from agf_orchestrator.engineering_memory_models import memory_from_dict
from agf_orchestrator.engineering_memory_store import EngineeringMemoryStore
from agf_orchestrator.models import RepositoryContext
from agf_orchestrator.reviewer import DeterministicReviewer

FIXTURE = Path(__file__).parent / "fixtures" / "memory" / "valid_entry.json"


def test_store_query_evidence_is_bounded_and_reused_by_plan_and_review(tmp_path):
    entry = memory_from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))
    store = EngineeringMemoryStore(tmp_path, entry.project_id)
    store.put(entry)
    results, evidence = store.search_with_evidence("roadmap", limit=10)

    assert [item.entry_id for item in results] == [entry.entry_id]
    assert evidence == (
        "memory-query: terms=roadmap; limit=10; "
        "result_ids=memory-roadmap-priority"
    )
    plan = Director().create_plan(
        "Define a bounded change",
        RepositoryContext("/repo", "feature", "https://github.com/example/repo.git", True, "abc"),
        memory_evidence=evidence,
    )
    assert evidence in plan.required_evidence
    task = plan.tasks[0]
    review = DeterministicReviewer().review(
        plan, task, task.allowed_paths, "patch", ["validation: exit_code=0"],
        memory_evidence=evidence,
    )
    assert evidence in review.evidence


def test_invalid_memory_query_evidence_is_rejected_by_planning_and_review():
    evidence = "memory-query: token:secret"
    with pytest.raises(ValueError, match="prohibited"):
        Director().create_plan(
            "Define a bounded change",
            RepositoryContext(
                "/repo", "feature", "https://github.com/example/repo.git", True, "abc"
            ),
            memory_evidence=evidence,
        )

    with pytest.raises(MemoryEvidenceError, match="prohibited"):
        from agf_orchestrator.engineering_memory_evidence import validate_query_evidence

        validate_query_evidence(evidence)
