"""Only explicitly named, tracked, non-sensitive paths enter initial Owner review."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from test_owner_objective_publication import setup

from agf_orchestrator.adapters.mock import MockAdapter
from agf_orchestrator.models import RepositoryContext
from tools.objective_review_bundle import _review_draft


def _git(root, *args):
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True, capture_output=True,
    )


def repository(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "config", "user.email", "test@example.invalid")
    (root / "README.md").write_text("# Bounded fixture\n")
    (root / "secret-token.env").write_text("SECRET")
    (root / "nested").mkdir()
    (root / "nested/example.py").write_text("def main(): pass\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "Fixture")
    return RepositoryContext(
        str(root), "main", "https://example.invalid/repo.git", True,
        subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip(),
    )


def test_explicit_tracked_readme_scope_is_bounded_and_uses_reviewed_validator(tmp_path):
    repo = repository(tmp_path)
    plan = MockAdapter().build_plan_inputs(
        "Review path:README.md and use git diff --check", repo,
    )
    task = plan["tasks"][0]
    assert task["allowed_paths"] == ["README.md"]
    assert task["validation_commands"] == ["git diff --check"]


@pytest.mark.parametrize(
    "goal",
    [
        "Please improve the codebase",
        "Review path:untracked.md and use git diff --check",
        "Review path:secret-token.env without changing other data",
        "Review path:.git/config and use git diff --check",
        "Review path:../../etc/passwd and use git diff --check",
        "Review path:nested and use git diff --check",
        "Review path:README.md plus path:nested/example.py together",
        "Review path:README.md* and use git diff --check",
    ],
)
def test_no_inferred_or_untracked_scope_is_approved(tmp_path, goal):
    plan = MockAdapter().build_plan_inputs(goal, repository(tmp_path))
    assert plan["tasks"][0]["allowed_paths"] == []


def test_symlink_target_is_not_evidenced(tmp_path):
    repo = repository(tmp_path)
    root = Path(repo.root)
    (root / "nested/link").symlink_to(root / "README.md")
    _git(root, "add", "nested/link")
    plan = MockAdapter().build_plan_inputs(
        "Change path:nested/link and run git diff --check", repo,
    )
    assert plan["tasks"][0]["allowed_paths"] == []


def test_owner_review_rejects_inert_placeholder_without_explicit_scope(
    tmp_path, monkeypatch,
):
    manager, project, session, store, _, _ = setup(tmp_path, monkeypatch)
    import json
    from dataclasses import replace
    from pathlib import Path

    from agf_orchestrator.authority_generation import AuthorityGenerationStore
    from agf_orchestrator.models import plan_from_dict

    plan = plan_from_dict(json.loads(Path(session.plan_path).read_text()))
    blank = replace(
        plan, tasks=[replace(task, allowed_paths=[]) for task in plan.tasks],
    )
    generation = AuthorityGenerationStore(store.root).active(project.project_id)
    with pytest.raises(ValueError, match="empty file scope"):
        _review_draft(session, project, blank, generation)
