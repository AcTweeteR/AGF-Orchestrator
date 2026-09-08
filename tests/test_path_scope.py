from dataclasses import replace

import pytest
from test_compliance import context
from test_executor import fake_codex, init_repo, make_plan

from agf_orchestrator.adapters.codex import CodexAdapter, CodexInvocationProfile
from agf_orchestrator.compliance import ComplianceChecker
from agf_orchestrator.delivery import _patch_policy
from agf_orchestrator.execution_models import ExecutionStatus
from agf_orchestrator.executor import Executor, _path_allowed
from agf_orchestrator.review_models import ComplianceStatus, ReviewStatus
from agf_orchestrator.reviewer import DeterministicReviewer


@pytest.mark.parametrize(("allowed", "changed", "expected"), [
    (["src/"], "src/file.txt", True),
    (["src/"], "src/nested/file.txt", True),
    (["src"], "src/file.txt", False),
    (["src/file.txt"], "src/file.txt", True),
    (["src/file.txt"], "src/file.txt/child", False),
    (["src/"], "src2/file.txt", False),
    (["src/"], "src", False),
    (["src/"], "src/../private.txt", False),
    (["src/"], "src/.git/config", False),
    (["src/"], "/src/file.txt", False),
    (["src/"], "src\\file.txt", False),
    (["src/"], "src/./file.txt", False),
    (["src/", "../"], "src/file.txt", False),
])
def test_execution_review_compliance_and_patch_enforce_same_scope(allowed, changed, expected):
    plan, task, _ = context()
    task = replace(task, allowed_paths=allowed)
    plan = replace(plan, tasks=[task])
    patch = "@@ -1 +1 @@\n-before\n+after\n"
    validations = ["validation: exit_code=0; stdout=; stderr="]
    review = DeterministicReviewer().review(plan, task, [changed], patch, validations)
    compliance = ComplianceChecker().check(
        plan, task, review, [changed], validations, ["gate evidence"], True, "abc",
    )
    assert _path_allowed(changed, allowed) is expected
    assert (review.status is ReviewStatus.APPROVE) is expected
    assert (compliance.status is ComplianceStatus.PASS) is expected
    assert (not _patch_policy(patch, [changed], allowed)) is expected


@pytest.mark.parametrize(("allowed", "expected"), [
    (["src/"], ExecutionStatus.COMPLETED),
    (["src"], ExecutionStatus.FAILED),
])
def test_real_isolated_execution_requires_explicit_directory_scope(tmp_path, allowed, expected):
    init_repo(tmp_path)
    base = make_plan(tmp_path)
    task = replace(base.tasks[0], allowed_paths=allowed,
                   validation_commands=["git diff --check"])
    plan = replace(base, tasks=[task])
    binary = fake_codex(tmp_path, body="mkdir -p src; printf 'after\\n' > src/file.txt")
    result = Executor(CodexAdapter(str(binary), profile=CodexInvocationProfile())).execute(
        plan, task.task_id, str(tmp_path), dry_run=False,
    )
    assert result.status is expected
    assert not (tmp_path / "src").exists()
