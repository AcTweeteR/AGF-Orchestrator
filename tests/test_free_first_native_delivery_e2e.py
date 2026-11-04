"""Free-first native delivery through real Git, review and Compliance gates."""

import json
from dataclasses import replace
from unittest.mock import patch

from test_delivery import git, plan_for, setup_repo

from agf_orchestrator.adapters.free_first import FreeFirstGatewayAdapter
from agf_orchestrator.delivery import DeliveryPipeline
from agf_orchestrator.git_delivery import DraftPRCreator
from agf_orchestrator.model_gateway import GatewayResult
from agf_orchestrator.policy_state_store import PolicyStateStore
from agf_orchestrator.reviewer import DeterministicReviewer

PROJECT = "project-efc8e8ef7be7050b"


def test_local_model_native_pipeline_pushes_only_approved_branch(tmp_path):
    root = setup_repo(tmp_path, branch="main")
    (root / "compute.py").write_text("def compute(a, b):\n    return 0\n")
    git(root, "add", "compute.py")
    git(root, "commit", "-m", "Add disposable compute function")
    plan = plan_for(root)
    task = replace(
        plan.tasks[0],
        allowed_paths=["compute.py"],
        objective="Make compute return a + b instead of zero.",
        validation_commands=[
            'python -B -c "from compute import compute; assert compute(2, 3) == 5"'
        ],
    )
    plan = replace(plan, tasks=[task])
    plan.validate()
    PolicyStateStore(tmp_path / ".agf-orchestrator").bootstrap_authority(PROJECT, generation=1)
    original_head = git(root, "rev-parse", "HEAD").stdout.strip()

    result = GatewayResult(
        model="ollama/qwen3.5:9b-q4_K_M",
        provider="ollama",
        content=json.dumps({
            "changes": [{
                "file": "compute.py",
                "find": "return 0",
                "replace": "return a + b",
            }]
        }),
        prompt_tokens=30,
        completion_tokens=20,
    )
    with patch(
        "agf_orchestrator.adapters.free_first.ModelGatewayProvider.complete",
        return_value=result,
    ) as provider:
        report = DeliveryPipeline(
            adapter=FreeFirstGatewayAdapter(),
            reviewer=DeterministicReviewer(),
            pr_creator=DraftPRCreator(simulate=True),
            artifact_dir=tmp_path / "artifacts",
        ).deliver(plan, "task-001", str(root), execute=True, project_id=PROJECT)

    provider.assert_called_once()
    assert report.status == "COMPLETED", report.blocking_issues
    assert report.execution_status == "COMPLETED"
    assert report.review_status == "APPROVE"
    assert report.compliance_status == "PASS"
    assert report.changed_files == ["compute.py"]
    assert report.commit_sha is not None
    assert report.pr_url.startswith("local://draft-pr/")
    assert report.push_status == "PUSHED"
    assert git(root, "rev-parse", "HEAD").stdout.strip() == original_head
    assert git(root, "status", "--porcelain").stdout.strip() == ""
    assert (root / "compute.py").read_text().endswith("return 0\n")
    assert git(root, "show", f"{report.delivery_branch}:compute.py").stdout.strip().endswith(
        "return a + b"
    )
    assert git(
        root, "ls-remote", "--heads", "origin", report.delivery_branch
    ).stdout.strip(), "delivery was pushed to the disposable local bare remote"


def test_native_pipeline_never_publishes_without_project_identity(tmp_path):
    root = setup_repo(tmp_path, branch="main")
    (root / "compute.py").write_text("def compute(a, b):\n    return 0\n")
    git(root, "add", "compute.py")
    git(root, "commit", "-m", "Add test source")
    plan = plan_for(root)
    plan = replace(plan, tasks=[
        replace(plan.tasks[0], allowed_paths=["compute.py"],
                validation_commands=["git diff --check"])
    ])
    plan.validate()
    model = GatewayResult("ollama/qwen3.5:9b-q4_K_M", "ollama", json.dumps({
        "changes": [{"file": "compute.py", "find": "return 0", "replace": "return a+b"}]
    }), 10, 10)
    with patch(
        "agf_orchestrator.adapters.free_first.ModelGatewayProvider.complete",
        return_value=model,
    ):
        report = DeliveryPipeline(
            adapter=FreeFirstGatewayAdapter(),
            reviewer=DeterministicReviewer(),
            pr_creator=DraftPRCreator(simulate=True),
            artifact_dir=tmp_path / "artifacts",
        ).deliver(plan, "task-001", str(root), execute=True)
    assert report.status == "BLOCKED"
    assert report.commit_sha is None
    assert report.push_status == "NOT_REQUESTED"
    assert report.pr_url is None
    assert "free-first live delivery requires approved project identity" in report.blocking_issues
