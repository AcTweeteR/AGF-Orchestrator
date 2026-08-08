"""E6-T8 canaries for the existing controlled bootstrap delivery flow."""

from test_delivery import (
    DeliveryPipeline,
    DeterministicReviewer,
    DraftPRCreator,
    fake_adapter,
    git,
    plan_for,
    setup_repo,
)

from agf_orchestrator.policy_state_store import PolicyStateStore

PROJECT = "project-efc8e8ef7be7050b"


def test_bootstrap_delivery_keeps_caller_main_clean_and_never_merges(tmp_path, monkeypatch):
    root = setup_repo(tmp_path, branch="main")
    plan = plan_for(root)
    caller_head = git(root, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    PolicyStateStore(tmp_path / ".agf-orchestrator").bootstrap_authority(PROJECT, generation=1)

    report = DeliveryPipeline(
        adapter=fake_adapter(tmp_path),
        reviewer=DeterministicReviewer(),
        pr_creator=DraftPRCreator(simulate=True),
        artifact_dir=tmp_path / "artifacts",
    ).deliver(plan, "task-001", str(root), execute=True, project_id=PROJECT)

    assert report.status == "COMPLETED"
    assert report.review_status == "APPROVE"
    assert report.compliance_status == "PASS"
    assert report.validation_results
    assert all("validation" in item for item in report.validation_results)
    assert report.pr_url == "local://draft-pr/agf/plan-delivery/task-001"
    assert report.commit_sha
    assert git(root, "rev-parse", "HEAD").stdout.strip() == caller_head
    assert git(root, "branch", "--show-current").stdout.strip() == "main"
    assert git(root, "status", "--porcelain").stdout == ""
    assert not hasattr(DeliveryPipeline, "merge")
    assert "merge" not in report.to_dict()


def test_bootstrap_dry_run_is_side_effect_free(tmp_path):
    root = setup_repo(tmp_path, branch="main")
    plan = plan_for(root)
    caller_head = git(root, "rev-parse", "HEAD").stdout.strip()

    report = DeliveryPipeline(
        adapter=fake_adapter(tmp_path),
        reviewer=DeterministicReviewer(),
        pr_creator=DraftPRCreator(simulate=True),
    ).deliver(plan, "task-001", str(root), execute=False)

    assert report.status == "DRY_RUN"
    assert report.pr_url is None
    assert git(root, "rev-parse", "HEAD").stdout.strip() == caller_head
    assert git(root, "branch", "--list", "agf/*").stdout == ""
    assert git(root, "status", "--porcelain").stdout == ""
