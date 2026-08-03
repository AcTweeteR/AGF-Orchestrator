import subprocess
from dataclasses import replace

from agf_orchestrator.adapters.codex import CodexAdapter, CodexInvocationProfile
from agf_orchestrator.delivery import DeliveryPipeline, _patch_policy
from agf_orchestrator.git_delivery import DraftPRCreator, GitDeliveryError
from agf_orchestrator.models import ExecutionPlan, PlanStatus, RepositoryContext, Task
from agf_orchestrator.review_models import ReviewFinding, ReviewReport, ReviewStatus
from agf_orchestrator.reviewer import DeterministicReviewer


def git(path, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def setup_repo(tmp_path, branch="feature"):
    bare = tmp_path / "origin.git"
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True, text=True)
    root.mkdir()
    git(root, "init", "-b", branch)
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "allowed.txt").write_text("before\n")
    git(root, "add", "allowed.txt")
    git(root, "commit", "-m", "initial")
    git(root, "remote", "add", "origin", bare.as_uri())
    return root


def plan_for(root):
    branch = git(root, "branch", "--show-current").stdout.strip()
    task = Task(
        "task-001",
        "Update allowed",
        "Update allowed.txt to after",
        ["allowed.txt"],
        [],
        ["allowed.txt contains after"],
        [
            'python -B -c "from pathlib import Path; assert '
            "Path('allowed.txt').read_text().strip() == 'after'\""
        ],
        "low",
        "Implementer",
        PlanStatus.READY,
    )
    plan = ExecutionPlan(
        "1.0",
        "plan-delivery",
        "1970-01-01T00:00:00Z",
        RepositoryContext(
            str(root),
            branch,
            (root.parent / "origin.git").as_uri(),
            True,
            git(root, "rev-parse", "HEAD").stdout.strip(),
        ),
        "Update allowed",
        {"in": ["allowed.txt"]},
        [],
        [],
        {"status": "approved", "requires_architect": False},
        [task],
        [],
        [[task.task_id]],
        ["Reviewer"],
        ["review", "compliance"],
        [],
        PlanStatus.READY,
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


def test_delivery_from_main_uses_isolated_non_default_branch_and_is_idempotent(tmp_path):
    root = setup_repo(tmp_path, branch="main")
    plan = plan_for(root)
    adapter = fake_adapter(tmp_path)
    report = DeliveryPipeline(
        adapter=adapter,
        reviewer=DeterministicReviewer(),
        pr_creator=DraftPRCreator(simulate=True),
        artifact_dir=tmp_path / "artifacts",
    ).deliver(plan, "task-001", str(root), execute=True)
    assert report.status == "COMPLETED"
    assert report.delivery_branch.startswith("agf/")
    assert report.delivery_branch not in {"main", "master"}
    assert git(root, "branch", "--show-current").stdout.strip() == "main"
    assert git(root, "status", "--porcelain").stdout == ""
    assert git(root, "show-ref", "--verify", f"refs/heads/{report.delivery_branch}").returncode == 0
    assert git(
        root, "show-ref", "--verify", f"refs/remotes/origin/{report.delivery_branch}"
    ).returncode == 0

    second = DeliveryPipeline(
        adapter=adapter,
        reviewer=DeterministicReviewer(),
        pr_creator=DraftPRCreator(simulate=True),
        artifact_dir=tmp_path / "artifacts-second",
    ).deliver(plan, "task-001", str(root), execute=True)
    assert second.status in {"BLOCKED", "HUMAN_REQUIRED"}
    assert any("already exists" in issue for issue in second.blocking_issues)
    assert git(root, "branch", "--show-current").stdout.strip() == "main"
    assert git(root, "status", "--porcelain").stdout == ""


def test_delivery_from_dirty_main_is_blocked_before_adapter(tmp_path):
    root = setup_repo(tmp_path, branch="main")
    (root / "allowed.txt").write_text("dirty\n")
    report = DeliveryPipeline(
        adapter=fake_adapter(tmp_path),
        reviewer=DeterministicReviewer(),
        pr_creator=DraftPRCreator(simulate=True),
    ).deliver(plan_for(root), "task-001", str(root), execute=True)
    assert report.status == "BLOCKED"
    assert "clean" in report.blocking_issues[0]
    assert git(root, "branch", "--show-current").stdout.strip() == "main"


def test_delivery_base_and_origin_drift_block_before_adapter(tmp_path):
    root = setup_repo(tmp_path, branch="main")
    plan = plan_for(root)
    (root / "allowed.txt").write_text("drift\n")
    git(root, "add", "allowed.txt")
    git(root, "commit", "-m", "drift")
    drift_report = DeliveryPipeline(adapter=fake_adapter(tmp_path)).deliver(
        plan, "task-001", str(root), execute=True
    )
    assert drift_report.status == "BLOCKED"
    assert "base SHA" in drift_report.blocking_issues[0]

    root2 = setup_repo(tmp_path / "origin-drift", branch="main")
    plan2 = plan_for(root2)
    mismatched = replace(
        plan2,
        repository=replace(plan2.repository, origin="file:///unexpected/origin.git"),
    )
    origin_report = DeliveryPipeline(adapter=fake_adapter(tmp_path)).deliver(
        mismatched, "task-001", str(root2), execute=True
    )
    assert origin_report.status == "BLOCKED"
    assert "origin" in origin_report.blocking_issues[0]


def test_existing_delivery_branch_blocks_before_model_or_pr(tmp_path):
    root = setup_repo(tmp_path, branch="main")
    plan = plan_for(root)
    branch = "agf/plan-delivery/task-001"
    git(root, "branch", branch)
    report = DeliveryPipeline(
        adapter=fake_adapter(tmp_path),
        reviewer=DeterministicReviewer(),
        pr_creator=DraftPRCreator(simulate=True),
    ).deliver(plan, "task-001", str(root), execute=True)
    assert report.status == "BLOCKED"
    assert "already exists" in report.blocking_issues[0]
    assert git(root, "branch", "--show-current").stdout.strip() == "main"


def test_patch_policy_rejects_binary_submodule_and_rename():
    assert _patch_policy("GIT binary patch", ["file.bin"], ["file.bin"])
    assert _patch_policy("Subproject commit abc", ["submodule"], ["submodule"])
    assert _patch_policy("rename from a\nrename to b", ["a"], ["a"])


class RejectingReviewer:
    name = "rejecting"

    def review(self, *args):
        return ReviewReport("rejecting", ReviewStatus.REJECT, [], [], ["reject"])


def blocker_report(name="blocker"):
    return ReviewReport(
        name,
        ReviewStatus.REQUEST_CHANGES,
        [
            ReviewFinding(
                "REV-001",
                "CORRECTNESS",
                "blocker",
                "wrong value",
                ["allowed.txt"],
                "patch evidence",
                "write after",
            )
        ],
        [],
        ["wrong value"],
    )


def test_reviewer_rejection_blocks_delivery(tmp_path):
    root = setup_repo(tmp_path)
    report = DeliveryPipeline(
        adapter=fake_adapter(tmp_path),
        reviewer=RejectingReviewer(),
        pr_creator=DraftPRCreator(simulate=True),
        artifact_dir=tmp_path / "artifacts",
    ).deliver(plan_for(root), "task-001", str(root), execute=True)
    assert report.status in {"BLOCKED", "HUMAN_REQUIRED"}
    assert git(root, "branch", "--list", "agf/*").stdout == ""


def test_openhands_failure_is_primary_and_reviewer_is_not_run(tmp_path):
    class FailedOpenHands:
        name = "openhands"

        def build_instruction(self, **kwargs):
            return "instruction"

        def execute(self, instruction, repository, *, sandbox="workspace-write"):
            from agf_orchestrator.adapters.codex import CodexProcessResult

            return CodexProcessResult(
                "openhands-sdk", 0, "bounded evidence", "AgentErrorEvent: provider failed",
                human_required=True, transport_error="OPENHANDS_PROVIDER_ERROR",
            )

    root = setup_repo(tmp_path)
    report = DeliveryPipeline(
        adapter=FailedOpenHands(), reviewer=DeterministicReviewer(),
        pr_creator=DraftPRCreator(simulate=True),
    ).deliver(plan_for(root), "task-001", str(root), execute=True)
    assert report.status in {"BLOCKED", "HUMAN_REQUIRED"}
    assert report.execution_status == "HUMAN_REQUIRED"
    assert report.review_status == "NOT_RUN"
    assert report.compliance_status == "NOT_RUN"
    assert report.blocking_issues == ["OPENHANDS_PROVIDER_ERROR"]
    assert "reviewer invoked: no" in report.evidence
    assert git(root, "status", "--porcelain").stdout == ""


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
        report = DeterministicReviewer().review(*args)
        return ReviewReport(
            report.reviewer,
            report.status,
            report.findings,
            report.evidence,
            report.blocking_issues,
            "REV-001 resolved",
            report.checks_performed,
            report.residual_risks,
        )


def test_correction_succeeds_on_first_retry(tmp_path):
    root = setup_repo(tmp_path)
    reviewer = CorrectOnRetryReviewer()
    report = DeliveryPipeline(
        adapter=fake_adapter(tmp_path),
        reviewer=reviewer,
        pr_creator=DraftPRCreator(simulate=True),
        artifact_dir=tmp_path / "artifacts",
    ).deliver(plan_for(root), "task-001", str(root), execute=True)
    assert report.status == "COMPLETED"
    assert report.correction_rounds == 1
    assert reviewer.calls == 2


class TwoRoundReviewer:
    name = "two-round-reviewer"

    def __init__(self):
        self.calls = 0

    def review(self, *args):
        self.calls += 1
        if self.calls == 1:
            return blocker_report("first blocker")
        if self.calls == 2:
            report = blocker_report("second blocker")
            return ReviewReport(
                report.reviewer, report.status, report.findings, report.evidence,
                report.blocking_issues, "REV-001 resolved", report.checks_performed,
                report.residual_risks,
            )
        return ReviewReport(
            self.name, ReviewStatus.APPROVE, [], [], [], "REV-001 resolved", [], []
        )


def round_adapter(tmp_path):
    counter = tmp_path / "round-counter"
    fake = tmp_path / "fake-round-codex"
    fake.write_text(
        f"#!/bin/sh\n"
        f"n=$(cat '{counter}' 2>/dev/null || printf 0)\n"
        f"n=$((n + 1))\nprintf 'after\\n' > allowed.txt\n"
        f"i=0\nwhile [ $i -lt $n ]; do printf '\\n' >> allowed.txt; i=$((i + 1)); done\n"
        f"printf '%s' \"$n\" > '{counter}'\n"
    )
    fake.chmod(0o755)
    return CodexAdapter(str(fake), timeout=2, profile=CodexInvocationProfile())


def test_two_correction_rounds_are_allowed_and_approval_proceeds(tmp_path):
    root = setup_repo(tmp_path)
    reviewer = TwoRoundReviewer()
    report = DeliveryPipeline(
        adapter=round_adapter(tmp_path), reviewer=reviewer,
        pr_creator=DraftPRCreator(simulate=True), artifact_dir=tmp_path / "artifacts",
        max_correction_rounds=2,
    ).deliver(plan_for(root), "task-001", str(root), execute=True)
    assert report.status == "COMPLETED"
    assert report.correction_rounds == 2
    assert reviewer.calls == 3
    assert report.compliance_status == "PASS"
    assert git(root, "status", "--porcelain").stdout == ""


class RepeatingRequestReviewer:
    name = "repeating-request-reviewer"

    def __init__(self):
        self.calls = 0

    def review(self, *args):
        self.calls += 1
        report = blocker_report(f"round {self.calls}")
        summary = " ".join("REV-001 resolved" for _ in range(max(0, self.calls - 1)))
        return ReviewReport(
            self.name, report.status, report.findings, report.evidence,
            report.blocking_issues, summary, [], [],
        )


def test_correction_limit_exhausts_after_two_completed_rounds(tmp_path):
    root = setup_repo(tmp_path)
    reviewer = RepeatingRequestReviewer()
    report = DeliveryPipeline(
        adapter=round_adapter(tmp_path), reviewer=reviewer,
        pr_creator=DraftPRCreator(simulate=True), max_correction_rounds=2,
    ).deliver(plan_for(root), "task-001", str(root), execute=True)
    assert report.status == "BLOCKED"
    assert report.correction_rounds == 2
    assert reviewer.calls == 3
    assert "correction limit exhausted" in report.blocking_issues[0]


class HumanRequiredReviewer:
    name = "human-required-reviewer"

    def __init__(self, issue):
        self.issue = issue
        self.calls = 0

    def review(self, *args):
        self.calls += 1
        return ReviewReport(self.name, ReviewStatus.HUMAN_REQUIRED, [], [], [self.issue])


def test_human_required_judgment_stops_without_consuming_correction_round(tmp_path):
    root = setup_repo(tmp_path)
    reviewer = HumanRequiredReviewer("human judgment required")
    report = DeliveryPipeline(
        adapter=fake_adapter(tmp_path), reviewer=reviewer,
        pr_creator=DraftPRCreator(simulate=True), max_correction_rounds=2,
    ).deliver(plan_for(root), "task-001", str(root), execute=True)
    assert report.status == "HUMAN_REQUIRED"
    assert report.correction_rounds == 0
    assert reviewer.calls == 1
    assert "human judgment" in report.blocking_issues[0]


def test_transport_human_required_is_not_treated_as_judgment_or_correction(tmp_path):
    root = setup_repo(tmp_path)
    reviewer = HumanRequiredReviewer("CODEX_REVIEW_TRANSPORT_UNVERIFIED: transport uncertain")
    report = DeliveryPipeline(
        adapter=fake_adapter(tmp_path), reviewer=reviewer,
        pr_creator=DraftPRCreator(simulate=True), max_correction_rounds=2,
    ).deliver(plan_for(root), "task-001", str(root), execute=True)
    assert report.status == "HUMAN_REQUIRED"
    assert report.correction_rounds == 0
    assert "CODEX_REVIEW_TRANSPORT_UNVERIFIED" in report.blocking_issues[0]


def test_correction_limit_requires_human(tmp_path):
    root = setup_repo(tmp_path)
    report = DeliveryPipeline(
        adapter=fake_adapter(tmp_path),
        reviewer=RejectingReviewer(),
        pr_creator=DraftPRCreator(simulate=True),
        artifact_dir=tmp_path / "artifacts",
    ).deliver(plan_for(root), "task-001", str(root), execute=True)
    assert report.status in {"BLOCKED", "HUMAN_REQUIRED"}
    assert git(root, "branch", "--list", "agf/*").stdout == ""


class FailedPRCreator:
    def create(self, *args, **kwargs):
        raise GitDeliveryError("draft PR creation failed; pushed branch retained")


def test_failed_pr_creation_retains_pushed_branch(tmp_path):
    root = setup_repo(tmp_path)
    report = DeliveryPipeline(
        adapter=fake_adapter(tmp_path),
        reviewer=DeterministicReviewer(),
        pr_creator=FailedPRCreator(),
        artifact_dir=tmp_path / "artifacts",
    ).deliver(plan_for(root), "task-001", str(root), execute=True)
    assert report.status == "HUMAN_REQUIRED"
    assert report.push_status == "PUSHED"
    assert git(root, "branch", "--list", "agf/*").stdout.strip()


def test_no_merge_action_exists_in_delivery_pipeline():
    assert not hasattr(DeliveryPipeline, "merge")


class NeverCalledReviewer:
    name = "semantic-never-called"

    def __init__(self):
        self.calls = 0

    def review(self, *args):
        self.calls += 1
        return ReviewReport(self.name, ReviewStatus.APPROVE, [], [], [])


class DeterministicBlocker:
    name = "deterministic-blocker"

    def review(self, *args):
        return blocker_report(self.name)


def test_deterministic_blocker_prevents_codex_review(tmp_path):
    root = setup_repo(tmp_path)
    semantic = NeverCalledReviewer()
    report = DeliveryPipeline(
        adapter=fake_adapter(tmp_path),
        reviewer=semantic,
        deterministic_reviewer=DeterministicBlocker(),
        pr_creator=DraftPRCreator(simulate=True),
        artifact_dir=tmp_path / "artifacts",
    ).deliver(plan_for(root), "task-001", str(root), execute=True)
    assert semantic.calls == 0
    assert report.review_status == ReviewStatus.REQUEST_CHANGES


class RepeatingFindingReviewer:
    name = "repeating"

    def review(self, *args):
        report = blocker_report(self.name)
        return ReviewReport(
            report.reviewer,
            report.status,
            report.findings,
            report.evidence,
            report.blocking_issues,
            "REV-001 still open",
            report.checks_performed,
            report.residual_risks,
        )


def test_repeated_unchanged_finding_stops_with_human_required(tmp_path):
    root = setup_repo(tmp_path)
    report = DeliveryPipeline(
        adapter=fake_adapter(tmp_path),
        reviewer=RepeatingFindingReviewer(),
        pr_creator=DraftPRCreator(simulate=True),
        artifact_dir=tmp_path / "artifacts",
    ).deliver(plan_for(root), "task-001", str(root), execute=True)
    assert report.status == "HUMAN_REQUIRED"
    assert "non-convergence" in report.blocking_issues[0]
    assert report.correction_rounds == 1
