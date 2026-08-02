import subprocess

from agf_orchestrator.adapters.codex import CodexAdapter, CodexInvocationProfile
from agf_orchestrator.delivery import DeliveryPipeline, _patch_policy
from agf_orchestrator.git_delivery import DraftPRCreator, GitDeliveryError
from agf_orchestrator.models import ExecutionPlan, PlanStatus, RepositoryContext, Task
from agf_orchestrator.review_models import ReviewReport, ReviewStatus
from agf_orchestrator.reviewer import DeterministicReviewer


def git(path, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=check,
        capture_output=True, text=True,
    )


def setup_repo(tmp_path):
    bare = tmp_path / "origin.git"
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True, text=True)
    root.mkdir()
    git(root, "init", "-b", "feature")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "allowed.txt").write_text("before\n")
    git(root, "add", "allowed.txt")
    git(root, "commit", "-m", "initial")
    git(root, "remote", "add", "origin", str(bare))
    return root


def plan_for(root):
    task = Task(
        "task-001", "Update allowed", "Update allowed.txt to after", ["allowed.txt"], [],
        ["allowed.txt contains after"],
        [
            "python -B -c \"from pathlib import Path; assert "
            "Path('allowed.txt').read_text().strip() == 'after'\""
        ],
        "low", "Implementer", PlanStatus.READY,
    )
    plan = ExecutionPlan(
        "1.0", "plan-delivery", "1970-01-01T00:00:00Z",
        RepositoryContext(str(root), "feature", str(root.parent / "origin.git"), True,
                          git(root, "rev-parse", "HEAD").stdout.strip()),
        "Update allowed", {"in": ["allowed.txt"]}, [], [],
        {"status": "approved", "requires_architect": False}, [task], [],
        [[task.task_id]], ["Reviewer"], ["review", "compliance"], [], PlanStatus.READY,
    )
    plan.validate()
    return plan


def fake_adapter(tmp_path, body=None):
    body = body or "printf 'after\\n' > allowed.txt"
    fake = tmp_path / "fake-codex"
    fake.write_text(f"#!/bin/sh\n{body}\n")
    fake.chmod(0o755)
    return CodexAdapter(str(fake), timeout=2, profile=CodexInvocationProfile())


def test_dry_run_performs_no_model_or_git_mutation(tmp_path):
    root = setup_repo(tmp_path)
    plan = plan_for(root)
    report = DeliveryPipeline(adapter=CodexAdapter(str(tmp_path / "missing"))).deliver(
        plan, "task-001", str(root), execute=False
    )
    assert report.status == "DRY_RUN"
    assert git(root, "status", "--porcelain").stdout == ""
    assert git(root, "branch", "--list", "agf/*").stdout == ""


def test_successful_pipeline_reviews_complies_pushes_and_simulates_pr(tmp_path):
    root = setup_repo(tmp_path)
    plan = plan_for(root)
    report = DeliveryPipeline(
        adapter=fake_adapter(tmp_path),
        reviewer=DeterministicReviewer(),
        pr_creator=DraftPRCreator(simulate=True),
        artifact_dir=tmp_path / "artifacts",
    ).deliver(plan, "task-001", str(root), execute=True)
    assert report.status == "COMPLETED"
    assert report.review_status == ReviewStatus.APPROVE
    assert report.compliance_status == "PASS"
    assert report.changed_files == ["allowed.txt"]
    assert report.commit_sha
    assert report.pr_url.startswith("local://draft-pr/")
    assert git(root, "status", "--porcelain").stdout == ""
    assert git(root, "branch", "--show-current").stdout.strip() == "feature"


def test_patch_policy_rejects_binary_submodule_and_rename():
    assert _patch_policy("GIT binary patch", ["file.bin"], ["file.bin"])
    assert _patch_policy("Subproject commit abc", ["submodule"], ["submodule"])
    assert _patch_policy("rename from a\nrename to b", ["a"], ["a"])


class RejectingReviewer:
    name = "rejecting"

    def review(self, *args):
        return ReviewReport("rejecting", ReviewStatus.REJECT, [], [], ["reject"])


def test_reviewer_rejection_blocks_delivery(tmp_path):
    root = setup_repo(tmp_path)
    report = DeliveryPipeline(
        adapter=fake_adapter(tmp_path), reviewer=RejectingReviewer(),
        pr_creator=DraftPRCreator(simulate=True), artifact_dir=tmp_path / "artifacts",
    ).deliver(plan_for(root), "task-001", str(root), execute=True)
    assert report.status in {"BLOCKED", "HUMAN_REQUIRED"}
    assert git(root, "branch", "--list", "agf/*").stdout == ""


class CorrectOnRetryReviewer:
    name = "correct-on-retry"

    def __init__(self):
        self.calls = 0

    def review(self, *args):
        self.calls += 1
        if self.calls == 1:
            return ReviewReport(
                self.name, ReviewStatus.REQUEST_CHANGES, [], [], ["improve evidence"]
            )
        return DeterministicReviewer().review(*args)


def test_correction_succeeds_on_first_retry(tmp_path):
    root = setup_repo(tmp_path)
    reviewer = CorrectOnRetryReviewer()
    report = DeliveryPipeline(
        adapter=fake_adapter(tmp_path), reviewer=reviewer,
        pr_creator=DraftPRCreator(simulate=True), artifact_dir=tmp_path / "artifacts",
    ).deliver(plan_for(root), "task-001", str(root), execute=True)
    assert report.status == "COMPLETED"
    assert report.correction_rounds == 1
    assert reviewer.calls == 2


def test_correction_limit_requires_human(tmp_path):
    root = setup_repo(tmp_path)
    report = DeliveryPipeline(
        adapter=fake_adapter(tmp_path), reviewer=RejectingReviewer(),
        pr_creator=DraftPRCreator(simulate=True), artifact_dir=tmp_path / "artifacts",
    ).deliver(plan_for(root), "task-001", str(root), execute=True)
    assert report.status in {"BLOCKED", "HUMAN_REQUIRED"}
    assert git(root, "branch", "--list", "agf/*").stdout == ""


class FailedPRCreator:
    def create(self, *args, **kwargs):
        raise GitDeliveryError("draft PR creation failed; pushed branch retained")


def test_failed_pr_creation_retains_pushed_branch(tmp_path):
    root = setup_repo(tmp_path)
    report = DeliveryPipeline(
        adapter=fake_adapter(tmp_path), reviewer=DeterministicReviewer(),
        pr_creator=FailedPRCreator(), artifact_dir=tmp_path / "artifacts",
    ).deliver(plan_for(root), "task-001", str(root), execute=True)
    assert report.status == "HUMAN_REQUIRED"
    assert report.push_status == "PUSHED"
    assert git(root, "branch", "--list", "agf/*").stdout.strip()


def test_no_merge_action_exists_in_delivery_pipeline():
    assert not hasattr(DeliveryPipeline, "merge")
