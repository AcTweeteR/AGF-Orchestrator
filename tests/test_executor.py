import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from agf_orchestrator.adapters.codex import CodexAdapter, CodexInvocationProfile
from agf_orchestrator.execution_models import ExecutionStatus
from agf_orchestrator.executor import Executor, _changed_paths, write_execution_result
from agf_orchestrator.models import ExecutionPlan, PlanStatus, RepositoryContext, Task


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    )


def test_changed_paths_ignores_generated_python_cache_but_keeps_real_changes():
    assert _changed_paths(
        [],
        [
            " M calculator.py",
            "?? __pycache__/calculator.cpython-314.pyc",
            "?? tests/.pytest_cache/v/cache/lastfailed",
            " M tracked.pyc",
        ],
    ) == ["calculator.py", "tracked.pyc"]


def init_repo(tmp_path, branch="feature"):
    git(tmp_path, "init", "-b", branch)
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "allowed.txt").write_text("before\n")
    git(tmp_path, "add", "allowed.txt")
    git(tmp_path, "commit", "-m", "initial")
    git(tmp_path, "remote", "add", "origin", "https://example.invalid/repo.git")


def make_plan(repo, *, branch="feature", task=None, architecture=None, status=PlanStatus.READY):
    task = task or Task(
        "task-001", "Update allowed file", "Update allowed.txt", ["allowed.txt"], [],
        ["allowed file contains the new value"], ["git diff --check -- allowed.txt"],
        "low", "Implementer", PlanStatus.READY,
    )
    repository = RepositoryContext(
        str(repo), branch, "https://example.invalid/repo.git", True,
        git(repo, "rev-parse", "HEAD").stdout.strip(),
    )
    plan = ExecutionPlan(
        "1.0", "plan-test", "1970-01-01T00:00:00Z", repository, "Update the file",
        {"in": ["allowed.txt"], "out": []}, [], [],
        architecture or {"status": "approved", "requires_architect": False}, [task], [],
        [[task.task_id]], ["Reviewer"], ["task outcome"], [], status,
    )
    plan.validate()
    return plan


def fake_codex(
    tmp_path,
    *,
    body=None,
    exit_code=0,
):
    if body is None:
        body = (
            "printf 'done\\n'\nprintf 'API_KEY=sk-test-secret-value\\n' >&2\n"
            "printf 'updated\\n' > allowed.txt"
        )
    fake = tmp_path.parent / f"fake-codex-{tmp_path.name}"
    fake.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then printf 'codex-test 1.0\\n'; exit 0; fi\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--output-last-message\" ]; then "
        "sleep 0.1; printf 'completed\\n' > \"$2\"; shift 2; else shift; fi\n"
        "done\n"
        f"{body}\nexit {exit_code}\n"
    )
    fake.chmod(0o755)
    return fake


def test_dry_run_does_not_invoke_fake_executable(tmp_path):
    init_repo(tmp_path)
    plan = make_plan(tmp_path)
    result = Executor(CodexAdapter(executable=str(tmp_path / "does-not-exist"))).execute(
        plan, "task-001", str(tmp_path)
    )
    assert result.status is ExecutionStatus.DRY_RUN
    assert result.exit_code is None
    assert any(item.startswith("gate checked:") for item in result.evidence)


def test_execution_evidence_contains_objective_traceability(tmp_path):
    init_repo(tmp_path)
    task = Task(
        "task-001", "Update allowed file", "Update allowed.txt", ["allowed.txt"], [],
        ["allowed file contains the new value"], ["git diff --check -- allowed.txt"],
        "low", "Implementer", PlanStatus.READY, ["requirement-file"],
    )
    plan = replace(
        make_plan(tmp_path),
        tasks=[task],
        objective_id="objective-file",
        requirement_refs=["requirement-file"],
    )

    result = Executor().execute(plan, "task-001", str(tmp_path))

    assert any("objective_id=objective-file" in item for item in result.evidence)
    assert any("requirement-file" in item for item in result.evidence)


def test_resolved_validation_commands_are_sent_to_provider(monkeypatch, tmp_path):
    init_repo(tmp_path)
    task = Task(
        "task-001", "Update allowed file", "Update allowed.txt", ["allowed.txt"], [],
        ["allowed file contains the new value"], ["python -c \"assert True\""],
        "low", "Implementer", PlanStatus.READY,
    )
    plan = make_plan(tmp_path, task=task)
    captured = {}
    original = CodexAdapter.build_instruction

    def capture(self, **kwargs):
        captured["validation_commands"] = kwargs["validation_commands"]
        return original(self, **kwargs)

    monkeypatch.setattr(CodexAdapter, "build_instruction", capture)
    monkeypatch.setattr(
        "agf_orchestrator.validation_commands.shutil.which",
        lambda name: sys.executable if name == "python3" else None,
    )
    result = Executor(
        CodexAdapter(
            executable=str(fake_codex(tmp_path)), profile=CodexInvocationProfile()
        )
    ).execute(
        plan, "task-001", str(tmp_path), dry_run=False
    )
    assert result.status is ExecutionStatus.COMPLETED
    assert captured["validation_commands"] == ["python3 -c 'assert True'"]


def test_unverified_invocation_syntax_requires_human(monkeypatch, tmp_path):
    init_repo(tmp_path)
    from agf_orchestrator.adapters import codex as codex_module

    monkeypatch.setattr(codex_module, "discover_invocation_profile", lambda executable: None)
    result = Executor(CodexAdapter(executable=str(fake_codex(tmp_path)))).execute(
        make_plan(tmp_path), "task-001", str(tmp_path), dry_run=False
    )
    assert result.status is ExecutionStatus.HUMAN_REQUIRED
    assert result.blocking_issues[0] == "CODEX_PROCESS_NOT_STARTED"
    assert git(tmp_path, "status", "--porcelain").stdout == ""


def test_dry_run_allows_isolated_execution_from_main_and_checks_allowed_paths(tmp_path):
    init_repo(tmp_path, branch="main")
    plan = make_plan(tmp_path, branch="main")
    result = Executor().execute(plan, "task-001", str(tmp_path))
    assert result.status is ExecutionStatus.DRY_RUN
    assert any("isolated temporary worktree" in item for item in result.evidence)

    git(tmp_path, "checkout", "-b", "feature")
    invalid_task = replace(plan.tasks[0], allowed_paths=[])
    invalid_plan = make_plan(tmp_path, branch="feature", task=invalid_task)
    result = Executor().execute(invalid_plan, "task-001", str(tmp_path))
    assert result.status is ExecutionStatus.BLOCKED
    assert "allowed_paths" in result.blocking_issues[0]


def test_live_execution_from_main_mutates_only_isolated_worktree(tmp_path):
    init_repo(tmp_path, branch="main")
    plan = make_plan(tmp_path, branch="main")
    head_before = git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    worktrees_before = git(tmp_path, "worktree", "list", "--porcelain").stdout
    result = Executor(
        CodexAdapter(
            str(fake_codex(tmp_path)), timeout=2, profile=CodexInvocationProfile()
        )
    ).execute(plan, "task-001", str(tmp_path), dry_run=False)
    assert result.status is ExecutionStatus.COMPLETED
    assert git(tmp_path, "rev-parse", "HEAD").stdout.strip() == head_before
    assert git(tmp_path, "status", "--porcelain").stdout == ""
    assert git(tmp_path, "worktree", "list", "--porcelain").stdout == worktrees_before


def test_head_drift_before_isolated_worktree_is_fail_closed(monkeypatch, tmp_path):
    init_repo(tmp_path)
    plan = make_plan(tmp_path)
    from agf_orchestrator import executor as executor_module

    original = executor_module._create_worktree

    def drift_then_create(repository, head_sha):
        (Path(repository) / "allowed.txt").write_text("drift\n")
        git(repository, "add", "allowed.txt")
        git(repository, "commit", "-m", "unexpected drift")
        return original(repository, head_sha)

    monkeypatch.setattr(executor_module, "_create_worktree", drift_then_create)
    result = Executor(
        CodexAdapter(
            str(fake_codex(tmp_path)), timeout=2, profile=CodexInvocationProfile()
        )
    ).execute(plan, "task-001", str(tmp_path), dry_run=False)
    assert result.status is ExecutionStatus.FAILED
    assert "repository HEAD changed" in result.blocking_issues[0]


def test_head_drift_during_worktree_creation_is_fail_closed(monkeypatch, tmp_path):
    init_repo(tmp_path)
    plan = make_plan(tmp_path)
    from agf_orchestrator import executor as executor_module

    original_run = executor_module.subprocess.run
    drifted = False

    def drift_during_add(command, *args, **kwargs):
        nonlocal drifted
        if not drifted and command[:5] == ["git", "-C", str(tmp_path), "worktree", "add"]:
            drifted = True
            (Path(tmp_path) / "allowed.txt").write_text("drift\n")
            git(tmp_path, "add", "allowed.txt")
            git(tmp_path, "commit", "-m", "unexpected concurrent drift")
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(executor_module.subprocess, "run", drift_during_add)
    result = Executor(
        CodexAdapter(
            str(fake_codex(tmp_path)), timeout=2, profile=CodexInvocationProfile()
        )
    ).execute(plan, "task-001", str(tmp_path), dry_run=False)
    assert result.status is ExecutionStatus.FAILED
    assert "during isolated worktree creation" in result.blocking_issues[0]


def test_head_drift_before_provider_invocation_is_fail_closed(monkeypatch, tmp_path):
    init_repo(tmp_path)
    plan = make_plan(tmp_path)
    from agf_orchestrator import executor as executor_module

    original = executor_module._create_worktree

    def create_then_drift(repository, head_sha):
        worktree = original(repository, head_sha)
        (Path(repository) / "allowed.txt").write_text("drift\n")
        git(repository, "add", "allowed.txt")
        git(repository, "commit", "-m", "unexpected pre-invocation drift")
        return worktree

    monkeypatch.setattr(executor_module, "_create_worktree", create_then_drift)
    result = Executor(
        CodexAdapter(
            str(fake_codex(tmp_path)), timeout=2, profile=CodexInvocationProfile()
        )
    ).execute(plan, "task-001", str(tmp_path), dry_run=False)
    assert result.status is ExecutionStatus.FAILED
    assert "before provider invocation" in result.blocking_issues[0]
    assert git(tmp_path, "worktree", "list", "--porcelain").stdout.count("worktree ") == 1


def test_plan_branch_and_base_sha_mismatch_is_fail_closed(tmp_path):
    init_repo(tmp_path, branch="main")
    plan = make_plan(tmp_path, branch="feature")
    result = Executor().execute(plan, "task-001", str(tmp_path))
    assert result.status is ExecutionStatus.BLOCKED
    assert "branch does not match" in result.blocking_issues[0]


def test_fake_codex_success_is_completed_with_scoped_change(tmp_path):
    init_repo(tmp_path)
    result = Executor(
        CodexAdapter(
            str(fake_codex(tmp_path)), timeout=2, profile=CodexInvocationProfile()
        )
    ).execute(
        make_plan(tmp_path), "task-001", str(tmp_path), dry_run=False
    )
    assert result.status is ExecutionStatus.COMPLETED
    assert result.files_changed == ["allowed.txt"]
    assert result.exit_code == 0
    assert "sk-test-secret-value" not in result.stderr_summary
    assert git(tmp_path, "status", "--porcelain").stdout == ""


def test_https_plan_origin_executes_against_equivalent_ssh_live_origin(tmp_path):
    init_repo(tmp_path)
    git(tmp_path, "remote", "set-url", "origin", "git@github.com:example/repo.git")
    plan = make_plan(tmp_path)
    plan = replace(
        plan,
        repository=replace(plan.repository, origin="https://github.com/example/repo.git"),
    )
    result = Executor(
        CodexAdapter(str(fake_codex(tmp_path)), timeout=2, profile=CodexInvocationProfile())
    ).execute(plan, "task-001", str(tmp_path), dry_run=False)
    assert result.status is ExecutionStatus.COMPLETED
    assert result.files_changed == ["allowed.txt"]
    assert git(tmp_path, "status", "--porcelain").stdout == ""


def test_unauthorized_change_is_rejected(tmp_path):
    init_repo(tmp_path)
    fake = fake_codex(
        tmp_path,
        body="printf 'updated\\n' > allowed.txt\nprintf 'bad\\n' > unauthorized.txt",
    )
    result = Executor(CodexAdapter(str(fake), timeout=2, profile=CodexInvocationProfile())).execute(
        make_plan(tmp_path), "task-001", str(tmp_path), dry_run=False
    )
    assert result.status is ExecutionStatus.FAILED
    assert any("unauthorized" in issue for issue in result.blocking_issues)
    assert git(tmp_path, "status", "--porcelain").stdout == ""


def test_fake_codex_failure_is_reported(tmp_path):
    init_repo(tmp_path)
    result = Executor(
        CodexAdapter(
            str(fake_codex(tmp_path, exit_code=7)),
            timeout=2,
            profile=CodexInvocationProfile(),
        )
    ).execute(
        make_plan(tmp_path), "task-001", str(tmp_path), dry_run=False
    )
    assert result.status is ExecutionStatus.FAILED
    assert result.exit_code == 7


def test_timeout_is_reported(tmp_path):
    init_repo(tmp_path)
    fake = fake_codex(tmp_path, body="sleep 1")
    result = Executor(
        CodexAdapter(str(fake), timeout=0.01, profile=CodexInvocationProfile())
    ).execute(
        make_plan(tmp_path), "task-001", str(tmp_path), dry_run=False
    )
    assert result.status is ExecutionStatus.FAILED
    assert result.exit_code is None
    assert any("timeout" in item.lower() for item in result.evidence)


def test_failed_validation_prevents_completed(tmp_path):
    init_repo(tmp_path)
    task = replace(make_plan(tmp_path).tasks[0], validation_commands=["false"])
    result = Executor(
        CodexAdapter(
            str(fake_codex(tmp_path)), timeout=2, profile=CodexInvocationProfile()
        )
    ).execute(
        make_plan(tmp_path, task=task), "task-001", str(tmp_path), dry_run=False
    )
    assert result.status is ExecutionStatus.FAILED
    assert any("validations failed" in issue for issue in result.blocking_issues)


def test_validation_command_policy_rejects_shell_syntax(tmp_path):
    init_repo(tmp_path)
    task = replace(make_plan(tmp_path).tasks[0], validation_commands=["echo ok; echo bad"])
    result = Executor().execute(make_plan(tmp_path, task=task), "task-001", str(tmp_path))
    assert result.status is ExecutionStatus.BLOCKED
    assert "shell control syntax" in result.blocking_issues[0]


def test_quoted_python_validation_command_is_allowed(tmp_path):
    init_repo(tmp_path)
    command = (
        'python3 -B -c "from pathlib import Path; '
        "assert Path('allowed.txt').read_text().strip() == 'before'\""
    )
    task = replace(make_plan(tmp_path).tasks[0], validation_commands=[command])
    result = Executor().execute(make_plan(tmp_path, task=task), "task-001", str(tmp_path))
    assert result.status is ExecutionStatus.DRY_RUN


@pytest.mark.parametrize("command", ["./check.sh", "../check.sh", "pytest & false", "pytest\nid"])
def test_runtime_validation_uses_target_scoped_command_policy(tmp_path, command):
    init_repo(tmp_path)
    outside = tmp_path.parent / "check.sh"
    outside.write_text("#!/bin/sh\nexit 0\n")
    outside.chmod(0o700)
    inside = tmp_path / "check.sh"
    inside.write_text("#!/bin/sh\nexit 0\n")
    inside.chmod(0o700)
    git(tmp_path, "add", "check.sh")
    git(tmp_path, "commit", "-m", "add validation helper")
    if command == "./check.sh":
        expected = ExecutionStatus.DRY_RUN
    else:
        expected = ExecutionStatus.BLOCKED
    task = replace(make_plan(tmp_path).tasks[0], validation_commands=[command])
    result = Executor().execute(make_plan(tmp_path, task=task), "task-001", str(tmp_path))
    assert result.status is expected


def test_runtime_validation_rejects_symlinked_target_executable(tmp_path):
    init_repo(tmp_path)
    outside = tmp_path.parent / "outside-check.sh"
    outside.write_text("#!/bin/sh\nexit 0\n")
    outside.chmod(0o700)
    (tmp_path / "check.sh").symlink_to(outside)
    task = replace(make_plan(tmp_path).tasks[0], validation_commands=["./check.sh"])
    result = Executor().execute(make_plan(tmp_path, task=task), "task-001", str(tmp_path))
    assert result.status is ExecutionStatus.BLOCKED


def test_validation_command_timeout_is_reported(tmp_path):
    init_repo(tmp_path)
    task = replace(make_plan(tmp_path).tasks[0], validation_commands=["sleep 1"])
    result = Executor(
        CodexAdapter(
            str(fake_codex(tmp_path)), timeout=2, profile=CodexInvocationProfile()
        ),
        validation_timeout=0.01,
    ).execute(make_plan(tmp_path, task=task), "task-001", str(tmp_path), dry_run=False)
    assert result.status is ExecutionStatus.FAILED
    assert any("timed out" in issue for issue in result.blocking_issues)


def test_nonexistent_validation_executable_is_blocked(tmp_path):
    init_repo(tmp_path)
    task = replace(make_plan(tmp_path).tasks[0], validation_commands=["does-not-exist-agf"])
    result = Executor().execute(make_plan(tmp_path, task=task), "task-001", str(tmp_path))
    assert result.status is ExecutionStatus.BLOCKED
    assert "cannot be resolved" in result.blocking_issues[0]


@pytest.mark.parametrize("branch", ["main", "master"])
def test_default_branches_are_safe_as_isolated_execution_sources(tmp_path, branch):
    init_repo(tmp_path, branch=branch)
    result = Executor().execute(
        make_plan(tmp_path, branch=branch), "task-001", str(tmp_path), dry_run=False
    )
    assert result.status is not ExecutionStatus.BLOCKED
    assert any("isolated temporary worktree" in item for item in result.evidence)


def test_detached_head_is_blocked(tmp_path):
    init_repo(tmp_path)
    git(tmp_path, "checkout", "--detach")
    result = Executor().execute(
        make_plan(tmp_path, branch="feature"), "task-001", str(tmp_path), dry_run=False
    )
    assert result.status is ExecutionStatus.BLOCKED
    assert "detached HEAD" in result.blocking_issues[0]


def test_dirty_repository_is_blocked(tmp_path):
    init_repo(tmp_path)
    (tmp_path / "allowed.txt").write_text("dirty\n")
    result = Executor().execute(make_plan(tmp_path), "task-001", str(tmp_path), dry_run=False)
    assert result.status is ExecutionStatus.BLOCKED
    assert "dirty" in result.blocking_issues[0]


def test_missing_origin_is_blocked(tmp_path):
    init_repo(tmp_path)
    git(tmp_path, "remote", "remove", "origin")
    result = Executor().execute(make_plan(tmp_path), "task-001", str(tmp_path), dry_run=False)
    assert result.status is ExecutionStatus.BLOCKED
    assert "origin" in result.blocking_issues[0]


def test_human_required_plan_is_blocked(tmp_path):
    init_repo(tmp_path)
    plan = make_plan(tmp_path, status=PlanStatus.HUMAN_REQUIRED)
    result = Executor().execute(plan, "task-001", str(tmp_path))
    assert result.status in {ExecutionStatus.BLOCKED, ExecutionStatus.HUMAN_REQUIRED}


def test_ready_dependency_is_blocked_until_execution_state_exists(tmp_path):
    init_repo(tmp_path)
    base = make_plan(tmp_path)
    dependency = replace(base.tasks[0], task_id="task-000", title="Dependency")
    selected = replace(base.tasks[0], dependencies=["task-000"])
    plan = replace(base, tasks=[dependency, selected], parallel_groups=[["task-000"], ["task-001"]])
    plan.validate()
    result = Executor().execute(plan, "task-001", str(tmp_path))
    assert result.status is ExecutionStatus.BLOCKED
    assert "completion cannot yet be verified" in result.blocking_issues[0]


def test_nonexistent_task_is_blocked(tmp_path):
    init_repo(tmp_path)
    result = Executor().execute(make_plan(tmp_path), "missing", str(tmp_path))
    assert result.status is ExecutionStatus.BLOCKED
    assert "does not exist" in result.blocking_issues[0]


@pytest.mark.parametrize(
    "task_change, plan_change, expected",
    [
        (lambda task: replace(task, allowed_paths=[]), lambda plan: plan, "allowed_paths"),
        (
            lambda task: task,
            lambda plan: replace(plan, human_intervention=["clarify"]),
            "human intervention",
        ),
        (
            lambda task: task,
            lambda plan: replace(plan, architecture_impact={"status": "pending"}),
            "architecture",
        ),
    ],
)
def test_live_safety_gates_block(task_change, plan_change, expected, tmp_path):
    init_repo(tmp_path)
    base = make_plan(tmp_path)
    plan = plan_change(replace(base, tasks=[task_change(base.tasks[0])]))
    result = Executor().execute(plan, "task-001", str(tmp_path), dry_run=False)
    assert result.status is ExecutionStatus.BLOCKED
    assert expected in result.blocking_issues[0]


@pytest.mark.parametrize("allowed_path", ["/absolute", "../outside", ".git/config", "."])
def test_invalid_allowed_paths_are_blocked(tmp_path, allowed_path):
    init_repo(tmp_path)
    task = replace(make_plan(tmp_path).tasks[0], allowed_paths=[allowed_path])
    result = Executor().execute(make_plan(tmp_path, task=task), "task-001", str(tmp_path))
    assert result.status is ExecutionStatus.BLOCKED
    assert "allowed path" in result.blocking_issues[0]


def test_temporary_worktree_removed_after_success(tmp_path, monkeypatch):
    init_repo(tmp_path)
    from agf_orchestrator import executor as executor_module

    created = []
    original = executor_module._create_worktree

    def capture(repository, head_sha):
        path = original(repository, head_sha)
        created.append(path)
        return path

    monkeypatch.setattr(executor_module, "_create_worktree", capture)
    result = Executor(
        CodexAdapter(
            str(fake_codex(tmp_path)), timeout=2, profile=CodexInvocationProfile()
        )
    ).execute(
        make_plan(tmp_path), "task-001", str(tmp_path), dry_run=False
    )
    assert result.status is ExecutionStatus.COMPLETED
    assert created and not Path(created[0]).exists()


def test_temporary_worktree_removed_after_failure(tmp_path, monkeypatch):
    init_repo(tmp_path)
    from agf_orchestrator import executor as executor_module

    created = []
    original = executor_module._create_worktree

    def capture(repository, head_sha):
        path = original(repository, head_sha)
        created.append(path)
        return path

    monkeypatch.setattr(executor_module, "_create_worktree", capture)
    result = Executor(
        CodexAdapter(
            str(fake_codex(tmp_path, exit_code=9)),
            timeout=2,
            profile=CodexInvocationProfile(),
        )
    ).execute(
        make_plan(tmp_path), "task-001", str(tmp_path), dry_run=False
    )
    assert result.status is ExecutionStatus.FAILED
    assert created and not Path(created[0]).exists()


def test_cleanup_failure_is_reported(tmp_path, monkeypatch):
    init_repo(tmp_path)
    from agf_orchestrator import executor as executor_module

    original = executor_module._remove_worktree

    def report_failure(repository, worktree):
        original(repository, worktree)
        return False

    monkeypatch.setattr(executor_module, "_remove_worktree", report_failure)
    result = Executor(
        CodexAdapter(
            str(fake_codex(tmp_path)), timeout=2, profile=CodexInvocationProfile()
        )
    ).execute(
        make_plan(tmp_path), "task-001", str(tmp_path), dry_run=False
    )
    assert result.status is ExecutionStatus.FAILED
    assert any("cleanup failed" in issue for issue in result.blocking_issues)


def test_report_serialization_failure_leaves_no_partial_file(tmp_path, monkeypatch):
    from agf_orchestrator import executor as executor_module

    output = tmp_path / "execution.json"
    result = ExecutionResultForTest()

    def fail_replace(source, target):
        raise OSError("serialization replacement failed")

    monkeypatch.setattr(executor_module.os, "replace", fail_replace)
    with pytest.raises(OSError):
        write_execution_result(result, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".execution.json.*.tmp"))


class ExecutionResultForTest:
    def to_dict(self):
        return {"status": "DRY_RUN"}
