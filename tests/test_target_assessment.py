import subprocess
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agf_orchestrator.director import Director
from agf_orchestrator.models import PlanStatus, RepositoryContext
from agf_orchestrator.target_assessment import (
    AssessmentError,
    DeterministicArchitect,
    assess_repository,
    derive_architecture,
)


def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    for key, value in (("user.name", "Test"), ("user.email", "test@example.invalid")):
        subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
    (root / "README.md").write_text("bounded project\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_smoke.py").write_text("def test_smoke(): pass\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    return root


def context(root):
    sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    return RepositoryContext(str(root), "main", "https://example.invalid/project.git", True, sha)


def assess(repository, project_id="project-test"):
    return assess_repository(
        repository,
        project_id,
        registered_project=SimpleNamespace(
            project_id=project_id,
            repository_root=repository.root,
            origin_url=repository.origin,
        ),
    )


def test_assessment_is_evidence_bound_and_read_only(tmp_path):
    root = repo(tmp_path)
    before = context(root)
    evidence = assess(before)
    assert evidence.project_type == "unknown"
    assert "README.md" in evidence.documentation
    assert "tests/test_smoke.py" in evidence.tests_validators
    assert context(root) == before


def test_missing_proposal_blocks_without_inventing_scope(tmp_path):
    root = repo(tmp_path)
    repository = context(root)
    evidence = assess(repository)
    decision = derive_architecture("identify a useful improvement", repository, evidence)
    assert decision.status == "BLOCKED"
    assert decision.tasks == ()
    assert decision.delivery_branch.startswith("agf/assess-")


def test_architect_produces_bounded_task_and_plan(tmp_path):
    root = repo(tmp_path)
    repository = context(root)
    evidence = assess(repository)
    decision = derive_architecture(
        "document the bounded project",
        repository,
        evidence,
        proposal={
            "rationale": "README is the only evidenced documentation surface.",
            "tasks": [{
                "task_id": "task-001",
                "title": "Document bounded project",
                "objective": "Document the bounded project in its README.",
                "allowed_paths": ["README.md"],
                "dependencies": [],
                "acceptance_criteria": ["README documents the project."],
                "validation_commands": ["python -m pytest"],
                "risk_level": "LOW",
                "assigned_role": "Implementer",
                "requirement_refs": [],
            }],
            "prohibited_paths": [".git", "tests"],
        },
    )
    assert decision.status == "approved"
    plan = Director().create_assessed_plan(
        "document the bounded project", repository, evidence, decision
    )
    assert plan.status is PlanStatus.READY
    assert plan.architecture_impact["requires_architect"] is False
    assert plan.scope["delivery_branch"] == decision.delivery_branch
    assert plan.tasks[0].allowed_paths == ["README.md"]


def test_deterministic_architect_requires_explicit_evidence_path(tmp_path):
    root = repo(tmp_path)
    evidence = assess(context(root))
    architect = DeterministicArchitect()
    assert architect.propose("improve the project", evidence) is None
    proposal = architect.propose("improve file:README.md", evidence)
    assert proposal["tasks"][0]["allowed_paths"] == ["README.md"]


def test_wildcard_scope_is_rejected(tmp_path):
    root = repo(tmp_path)
    repository = context(root)
    evidence = assess(repository)
    with pytest.raises(AssessmentError, match="concrete and bounded"):
        derive_architecture(
            "unsafe",
            repository,
            evidence,
            proposal={
                "tasks": [{
                    "task_id": "task-001", "allowed_paths": ["*"], "risk_level": "low"
                }]
            },
        )


def test_protected_and_unevidenced_paths_are_rejected(tmp_path):
    root = repo(tmp_path)
    repository = context(root)
    evidence = assess(repository)
    with pytest.raises(AssessmentError, match="lacks evidence"):
        derive_architecture(
            "unsafe",
            repository,
            evidence,
            proposal={
                "tasks": [{
                    "task_id": "task-001", "allowed_paths": ["missing.py"], "risk_level": "low"
                }]
            },
        )
    (root / ".env").write_text("ignored\n")
    dirty = replace(repository, clean=False)
    protected = assess(dirty)
    assert ".env" in protected.protected_paths


def test_stale_architecture_evidence_is_rejected(tmp_path):
    root = repo(tmp_path)
    repository = context(root)
    evidence = assess(repository)
    subprocess.run(
        ["git", "-C", str(root), "checkout", "-b", "change"],
        check=True,
        capture_output=True,
    )
    (root / "README.md").write_text("changed\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "change"],
        check=True,
        capture_output=True,
    )
    current = context(root)
    with pytest.raises(AssessmentError, match="baseline SHA"):
        derive_architecture("document", current, evidence, proposal={"tasks": []})
