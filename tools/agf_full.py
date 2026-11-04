"""Multi-file, isolated, persistent Free-First missions under owner control.

No paid inference or authorization to push/merge/publish. The task planner and
implementer are untrusted. A fresh Git worktree isolates every project mission.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

try:
    from . import agf_chat as chat
except ImportError:
    import agf_chat as chat

SOURCE_SUFFIXES = {".py", ".xml", ".js", ".ts", ".json", ".yaml", ".yml", ".swift", ".toml", ".md"}
SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "build",
    ".build",
    "__pycache__",
    "venv",
    ".tox",
    ".pytest_cache",
    "RecoveryBackups",
}
MAX_FILES = 4
WORKTREE_ROOT = Path.home() / ".local/share/agf-free-first/worktrees"


def inventory(root: Path, query: str = "", scope: str = "") -> list[str]:
    """Rank tracked, bounded sources using words in the requested objective."""
    raw = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"], text=True)
    eligible = []
    terms = [w.strip(".,:;!?()").lower() for w in query.split() if len(w) >= 4]
    test_goal = any(term in query.lower() for term in ("test", "prueba", "coverage", "cobertura"))
    fixture_goal = any(
        term in query.lower() for term in ("fixture", "test data", "datos de prueba")
    )
    documentation_goal = any(
        term in query.lower() for term in ("documentation", "documentación", "readme", "docs")
    )
    for name in raw.split("\0"):
        if not name:
            continue
        if scope and not (name == scope or name.startswith(scope.rstrip("/") + "/")):
            continue
        rel = Path(name)
        if (
            name == "AGENTS.md"
            or name.startswith(".github/")
            or (not documentation_goal and name.startswith("docs/"))
        ):
            continue
        if (
            len(rel.parts) > 7
            or any(part in SKIP_DIRS for part in rel.parts)
            or rel.suffix.lower() not in SOURCE_SUFFIXES
            or (
                not test_goal
                and (
                    rel.name.startswith("test_")
                    or rel.name.endswith("_test.py")
                    or "tests" in rel.parts
                )
            )
            or (not fixture_goal and "fixtures" in rel.parts)
        ):
            continue
        path = root / rel
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 1000000:
            continue
        score = sum(4 if t in rel.name.lower() else 1 for t in terms if t in name.lower())
        eligible.append((-score, name))
    eligible.sort()
    return [name for _, name in eligible[:220]]


def route_json(prompt: str, *, purpose: str, model="ollama/qwen3.5:9b-q4_K_M",
               usage_observer=None):
    if not isinstance(model, str) or not model.startswith("ollama/") or not model[7:]:
        raise ValueError("only an explicit local Ollama model is permitted")
    messages = [
        {
            "role": "system",
            "content": "Return a single valid JSON object. No markdown. Follow allowed paths.",
        },
        {"role": "user", "content": prompt},
    ]
    try:
        reply = chat.request(
            chat.OMNI_URL + "/v1/chat/completions",
            {
                "model": model,
                "messages": messages,
                "stream": False,
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 2600,
                "think": False,
            },
            token=os.environ.get("AGF_OMNIROUTE_KEY"),
            timeout=150,
        )
        actual = reply.get("model")
        usage = reply.get("usage", {})
        evidence = {
            "purpose": purpose,
            "provider": "omniroute/ollama",
            "model": model,
            "effective_model": actual,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "api_eur": 0,
        }
        if usage_observer is not None:
            usage_observer(evidence)
        if actual not in {model, model.removeprefix("ollama/")}:
            raise ValueError("unexpected gateway model")
        content = reply["choices"][0]["message"]["content"]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        # Local, no-cost fallback; never upgrade to API-based providers.
        reply = chat.request(
            chat.OLLAMA_URL + "/api/chat",
            {
                "model": model.removeprefix("ollama/"),
                "messages": messages,
                "stream": False,
                "think": False,
                "format": "json",
                "options": {
                    "temperature": 0,
                    "num_predict": 700 if purpose == "planning" else 2600,
                },
            },
            timeout=150,
        )
        actual = reply.get("model")
        evidence = {
            "purpose": purpose,
            "provider": "ollama",
            "model": model,
            "effective_model": actual,
            "prompt_tokens": reply.get("prompt_eval_count", 0),
            "completion_tokens": reply.get("eval_count", 0),
            "api_eur": 0,
        }
        if usage_observer is not None:
            usage_observer(evidence)
        if actual != model.removeprefix("ollama/"):
            raise ValueError("unexpected local model")
        content = reply["message"]["content"]
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError("model response is not a JSON object")
    return result, evidence


def plan(
    root: Path,
    objective: str,
    fixed_tests: list[str],
    *,
    model="ollama/qwen3.5:9b-q4_K_M",
    scope: str = "",
    usage_observer=None,
) -> tuple[list[dict], dict]:
    allowed = inventory(root, query=objective, scope=scope)
    if not allowed:
        raise ValueError("repository has no supported editable source files")
    if not 5 <= len(objective.strip()) <= 6000:
        raise ValueError("objective must be 5-6000 characters")
    prompt = (
        "Plan a small executable software change. Respond as JSON with tasks, "
        "an array of 1 to 4 objects {files:[existing paths],instruction:string}. "
        "Each task may edit at most 4 related files, and tasks should be ordered. "
        "Choose exact filenames ONLY from the inventory. Do not include test files "
        "unless the task explicitly asks for tests. Avoid documentation-only plans. "
        "Do not invent files. Aim to complete the full objective with the smallest "
        "changes. If goal is complex, focus on an independently verifiable first slice."
        f"\nOBJECTIVE:\n{objective}\nINVENTORY:\n" + json.dumps(allowed)
    )
    data, evidence = route_json(prompt, purpose="planning", model=model,
                                usage_observer=usage_observer)
    raw = data.get("tasks")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 4:
        raise ValueError("planner returned no bounded task list")
    tasks = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("invalid task")
        files, instruction = item.get("files"), item.get("instruction")
        if (
            not isinstance(files, list)
            or not 1 <= len(files) <= MAX_FILES
            or len(set(files)) != len(files)
            or any(f not in allowed for f in files)
            or not isinstance(instruction, str)
            or not 8 <= len(instruction) <= 2000
        ):
            raise ValueError("planner produced unapproved paths or instructions")
        tasks.append({"files": files, "instruction": instruction, "tests": fixed_tests})
    return tasks, evidence


def check_task(root: Path, task: dict, *, approved_files: list[str] | None = None) -> None:
    names = task.get("files")
    if (
        not isinstance(names, list)
        or not 1 <= len(names) <= MAX_FILES
        or len(set(names)) != len(names)
    ):
        raise ValueError("task must select 1-4 distinct files")
    allowed = approved_files if approved_files is not None else inventory(root)
    for name in names:
        if not isinstance(name, str) or name not in allowed:
            raise ValueError("task includes an unknown, unavailable, or unsafe source")
        target = chat.safe_target(root, name)
        if not target.is_file() or target.is_symlink():
            raise ValueError("task must use an existing non-symlink source")
    if not isinstance(task.get("instruction"), str) or not task["instruction"].strip():
        raise ValueError("empty task instruction")
    if not isinstance(task.get("tests"), list) or not task["tests"]:
        raise ValueError("approved test argv required")


def create_worktree(repository: Path, mission_id: str) -> tuple[Path, str]:
    check = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
        text=True,
    )
    if check.returncode:
        raise ValueError("repository needs an initial Git commit")
    root = WORKTREE_ROOT / mission_id
    if root.exists():
        raise ValueError("mission worktree already exists")
    root.parent.mkdir(parents=True, exist_ok=True)
    branch = "agf/free-first-" + mission_id
    subprocess.run(
        ["git", "-C", str(repository), "worktree", "add", "-q", "-b", branch, str(root), "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return root, branch


def start(
    state_dir: Path,
    repository: Path,
    *,
    objective: str | None,
    tasks_path: Path | None,
    tests: list[str],
    model: str,
    max_attempts=3,
    background=False,
    scope: str = "",
) -> dict:
    repository = repository.resolve()
    if not isinstance(model, str) or not model.startswith("ollama/") or not model[7:]:
        raise ValueError("only an explicit local Ollama model is permitted")
    if scope and (
        scope.startswith("/") or ".." in Path(scope).parts or ".git" in Path(scope).parts
    ):
        raise ValueError("scope must be a safe repository-relative prefix")
    if not repository.is_dir():
        raise ValueError("repository is missing")
    # Never start from an arbitrary subfolder or mutate the caller's worktree.
    top = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if Path(top).resolve() != repository:
        raise ValueError("repository must be its Git toplevel")
    mission_id = uuid.uuid4().hex[:16]
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / (mission_id + ".json")
    record = {
        "engine": "full",
        "mission_id": mission_id,
        "status": "PLANNING",
        "repository": str(repository),
        "worktree": None,
        "branch": None,
        "objective": objective,
        "scope": scope,
        "tasks": None,
        "planning_tests": shlex.split(tests) if isinstance(tests, str) else tests,
        "planning_model": model,
        "planning_attempts": 0,
        "index": 0,
        "attempt": 0,
        "max_attempts": max(1, min(5, max_attempts)),
        "usage": [],
        "events": [],
        "api_budget_eur": 0,
        "codex_calls": 0,
        "created_at": time.time(),
        "planning_pid": os.getpid(),
    }
    # Persist before creating a worktree or invoking a potentially slow model.
    worktree = None
    branch = None
    with chat.control_lock(path):
        chat._write_json_unlocked(path, record)
        try:
            worktree, branch = create_worktree(repository, mission_id)
            head = subprocess.check_output(
                ["git", "-C", str(worktree), "rev-parse", "HEAD"], text=True
            ).strip()
            record.update(worktree=str(worktree), branch=branch, expected_head=head)
            chat._write_json_unlocked(path, record)
            tasks = json.loads(tasks_path.read_text()) if tasks_path else None
            if tasks is not None and (not isinstance(tasks, list) or not 1 <= len(tasks) <= 30):
                raise ValueError("must have between 1 and 30 tasks")
            approved = inventory(worktree, query=objective or "", scope=scope)
            for task in tasks or []:
                check_task(worktree, task, approved_files=approved)
                if scope and any(
                    not (name == scope or name.startswith(scope.rstrip("/") + "/"))
                    for name in task["files"]
                ):
                    raise ValueError("task file outside owner-selected scope")
            record.update(status="PENDING", tasks=tasks)
            record.pop("planning_pid", None)
            chat._write_json_unlocked(path, record)
        except Exception as exc:
            record.update(
                status="BLOCKED",
                reason=f"Mission preparation failed: {type(exc).__name__}: {str(exc)[:600]}",
            )
            record.pop("planning_pid", None)
            chat._write_json_unlocked(path, record)
            if worktree is not None:
                subprocess.run(
                    ["git", "-C", str(repository), "worktree", "remove", "--force", str(worktree)],
                    capture_output=True,
                )
            if branch is not None:
                subprocess.run(
                    ["git", "-C", str(repository), "branch", "-D", branch], capture_output=True
                )
            return {"mission_id": mission_id, "status": "BLOCKED", "reason": record["reason"]}
    with chat.control_lock(path):
        record = json.loads(path.read_text())
        if record["status"] == "PENDING" and background:
            with (state_dir / (mission_id + ".log")).open("ab") as logfile:
                subprocess.Popen(
                    [sys.executable, str(Path(__file__).with_name("agf_chat.py")),
                     "--state-dir", str(state_dir), "run", mission_id],
                    stdin=subprocess.DEVNULL, stdout=logfile, stderr=logfile,
                    start_new_session=True,
                )
        return {
            "mission_id": mission_id,
            "status": (
                "STARTED" if background and record["status"] == "PENDING" else record["status"]
            ),
            "worktree": str(worktree),
            "branch": branch,
        }


def relevant_excerpt(content: str, objective: str, limit: int = 2800) -> str:
    """Prefer intact lines around objective keywords in large source files."""
    if len(content) <= limit:
        return content
    lines = content.splitlines(keepends=True)
    keywords = [w.strip(".,:;!?()").lower() for w in objective.split() if len(w) > 4]
    selected = (
        max(range(len(lines)), key=lambda i: sum(word in lines[i].lower() for word in keywords))
        if lines
        else 0
    )
    start = max(0, selected - 8)
    return (
        f"[source excerpt near line {selected + 1}]\n"
        + "".join(lines[start : selected + 10])[:limit]
    )


def propose_edits(
    root: Path, task: dict, previous_error: str, *, model="ollama/qwen3.5:9b-q4_K_M",
    usage_observer=None,
) -> tuple[dict[str, str], dict]:
    current = {
        name: chat.safe_target(root, name).read_text(encoding="utf-8") for name in task["files"]
    }
    for name, content in current.items():
        if len(content) > 1000000:
            raise ValueError(f"source exceeds supported file size: {name}")
    context = "\n\n".join(
        f"FILE {name}:\n{relevant_excerpt(source, task['instruction'])}"
        for name, source in current.items()
    )
    prompt = (
        "Modify only the explicitly allowed files. Return one JSON object "
        "with a changes array. Each item must contain file, find, replace. "
        "The find field must be an exact, nonempty, unique existing substring "
        "from that file; replace is its replacement. Prefer compact targeted "
        "edits over full rewrites. You may return multiple changes per file, "
        "but each find must match once at the point it is applied. "
        "Never include test changes, shell commands, or extra paths. "
        "Respect the original language and preserve unrelated behavior."
        f"\nGOAL: {task['instruction']}"
        f"\nPREVIOUS FAILURE:\n{previous_error[-2600:]}"
        f"\nALLOWED FILES:\n{context}"
    )
    observed = []

    def observe(usage):
        if usage_observer is not None:
            usage_observer(usage)
        observed.append(usage)

    result, usage = route_json(prompt, purpose="implementation", model=model,
                               usage_observer=observe)
    if not observed and usage_observer is not None:
        usage_observer(usage)
    operations = result.get("changes")
    if not isinstance(operations, list) or not 1 <= len(operations) <= 12:
        raise ValueError("model returned no bounded change list")
    updated = current.copy()
    for op in operations:
        if not isinstance(op, dict):
            raise ValueError("malformed edit")
        name, find, replacement = op.get("file"), op.get("find"), op.get("replace")
        if (
            name not in current
            or not isinstance(find, str)
            or not find
            or not isinstance(replacement, str)
            or len(replacement) > 30000
        ):
            raise ValueError("unapproved or malformed file replacement")
        if updated[name].count(find) != 1:
            raise ValueError(f"nonunique or missing edit anchor in {name}")
        updated[name] = updated[name].replace(find, replacement, 1)
    if all(updated[n] == current[n] for n in updated):
        raise ValueError("model proposed no effective code changes")
    return {name: new for name, new in updated.items() if new != current[name]}, usage


def check_edits(
    worktree: Path, names: list[str], *, committed_files: list[str] | None = None
) -> tuple[bool, str]:
    # Interrupted commits no longer appear in git diff, but must still pass
    # the same syntax and scope checks as an in-flight uncommitted patch.
    command = (
        ["git", "-C", str(worktree), "diff", "HEAD", "--check"]
        if committed_files is None
        else ["git", "-C", str(worktree), "show", "--check", "--format=", "HEAD"]
    )
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        return False, result.stderr[-1800:] + result.stdout[-1800:]
    if committed_files is None:
        changed = subprocess.run(
            ["git", "-C", str(worktree), "diff", "HEAD", "--name-only", "-z"],
            capture_output=True, text=True, check=True,
        ).stdout
        actual = [name for name in changed.split("\0") if name]
        # Git diff ignores untracked files and, without HEAD, staged changes.
        # Acceptance tests are untrusted code: neither may expand the task.
        untracked = subprocess.run(
            ["git", "-C", str(worktree), "ls-files", "--others",
             "--exclude-standard", "-z"],
            capture_output=True, text=True, check=True,
        ).stdout
        if untracked:
            return False, "untracked files created outside the approved task scope"
    else:
        actual = committed_files
    if not actual or any(name not in names for name in actual):
        return False, "change scope does not match the approved task files"
    for name in actual:
        path = chat.safe_target(worktree, name)
        try:
            if path.suffix.lower() == ".py":
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
            elif path.suffix.lower() == ".xml":
                import xml.etree.ElementTree as ET

                ET.parse(path)
            elif path.suffix.lower() == ".json":
                json.loads(path.read_text(encoding="utf-8"))
            elif path.suffix.lower() == ".toml":
                import tomllib

                tomllib.loads(path.read_text(encoding="utf-8"))
        except (SyntaxError, ValueError, OSError, ImportError) as exc:
            return False, f"{name}: {exc}"
    return True, "static checks passed"


def _task_digest(task: dict) -> str:
    """Bind a commit to the exact owner-approved task, not just a task number."""
    return hashlib.sha256(
        json.dumps(task, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _reconcile_committed_task(root: Path, state: dict, path: Path, head: str) -> dict:
    """Recover only the one commit proven to follow the last checkpoint.

    The executor never guesses whether unrelated repository activity was authorized.
    Modeled after the durable assignment/recovery approach in dsh-superagent.
    """
    index = state["index"]
    tasks = state.get("tasks")
    if not isinstance(tasks, list) or not 0 <= index < len(tasks):
        raise ValueError("no pending task matches an unrecorded commit")
    task = tasks[index]
    parent = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD^"], text=True
    ).strip()
    if parent != state["expected_head"]:
        raise ValueError("unrecorded commit is not the next child of the checkpoint")
    message = subprocess.check_output(
        ["git", "-C", str(root), "show", "-s", "--format=%B", "HEAD"], text=True
    )
    markers = (
        f"AGF-Mission: {state['mission_id']}\n",
        f"AGF-Task: {index + 1}\n",
        f"AGF-Task-Digest: {_task_digest(task)}\n",
    )
    if not all(marker in message for marker in markers):
        raise ValueError("unrecorded commit has no matching AGF task provenance")
    modified = subprocess.check_output(
        ["git", "-C", str(root), "diff-tree", "--no-commit-id",
         "--name-only", "-r", "-z", "HEAD"]
    )
    changed = set(modified.decode("utf-8").strip("\0").split("\0"))
    if not changed or not changed.issubset(set(task["files"])):
        raise ValueError("unrecorded commit changed files outside approved task scope")
    safe, details = check_edits(root, task["files"], committed_files=sorted(changed))
    if safe and index == len(tasks) - 1:
        safe, validation = chat.execute_tests(root, task["tests"], timeout=100)
        details += "\n" + validation[-600:]
    if not safe:
        raise ValueError("recovered commit did not pass verification: " + details[-500:])
    state["expected_head"] = head
    state["index"] = index + 1
    state["attempt"] = 0
    state["status"] = "PENDING"
    state.pop("reason", None)
    state.pop("pid", None)
    state["updated_at"] = time.time()
    state["events"].append({
        "task": index, "committed": True, "recovered": True,
        "commit": head[:12], "validation_tail": details[-500:],
    })
    chat.write_json(path, state)
    return state


def run_mission(state_dir: Path, mission_id: str) -> dict:
    path = state_dir / (mission_id + ".json")
    lock = state_dir / (mission_id + ".lock")
    with lock.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"mission_id": mission_id, "status": "ALREADY_RUNNING"}
        state = json.loads(path.read_text())
        if state["status"] in {"COMPLETED", "BLOCKED", "PAUSED", "STOPPED", "HUMAN_REQUIRED"}:
            return state
        if state["status"] == "PLANNING" and state.get("planning_pid"):
            return state
        interrupted = state["status"] in {"RUNNING", "PAUSING"}
        if state["status"] == "PAUSING":
            # Generic write_json deliberately preserves owner PAUSING against
            # stale executor writes. Interrupted-pause recovery is a distinct,
            # lock-guarded transition and must use the canonical latest state.
            with chat.control_lock(path):
                current = json.loads(path.read_text())
                if current["status"] != "PAUSING":
                    return current
                current["status"] = "HUMAN_REQUIRED"
                current["reason"] = "interrupted pause requires owner reconciliation"
                current.pop("pid", None)
                chat._write_json_unlocked(path, current)
                return current
        if not state.get("worktree"):
            state["status"] = "BLOCKED"
            state["reason"] = "mission has no validated worktree or tasks"
            state.pop("pid", None)
            chat.write_json(path, state)
            return state
        root = Path(state["worktree"]).resolve()
        if not root.is_dir():
            state["status"] = "BLOCKED"
            state["reason"] = "isolated mission worktree is missing"
            chat.write_json(path, state)
            return state
        try:
            actual_head = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip()
            actual_branch = subprocess.check_output(
                ["git", "-C", str(root), "branch", "--show-current"], text=True
            ).strip()
            actual_root = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "--show-toplevel"], text=True
            ).strip()
            if (actual_branch != state.get("branch")
                    or Path(actual_root).resolve() != root):
                raise ValueError("worktree identity changed")
            if subprocess.check_output(
                ["git", "-C", str(root), "status", "--porcelain"], text=True
            ).strip():
                raise ValueError("worktree has uncommitted changes")
            if actual_head != state.get("expected_head"):
                state = _reconcile_committed_task(root, state, path, actual_head)
            elif interrupted:
                # No authenticated commit means the interrupted model attempt is
                # ambiguous; retain the original HUMAN_REQUIRED safety boundary.
                state["status"] = "HUMAN_REQUIRED"
                state["reason"] = "execution interrupted without verified commit"
                state.pop("pid", None)
                chat.write_json(path, state)
                return state
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            state["status"] = "HUMAN_REQUIRED"
            state["reason"] = f"worktree identity cannot be verified: {exc}"
            chat.write_json(path, state)
            return state
        if state.get("tasks") is None:
            if state.get("planning_attempts", 0) >= 2:
                state["status"] = "BLOCKED"
                state["reason"] = "planning retry limit reached after interruption"
                state.pop("pid", None)
                chat.write_json(path, state)
                return state
            state["status"] = "PLANNING"
            state["planning_attempts"] = state.get("planning_attempts", 0) + 1
            state["pid"] = os.getpid()
            chat.write_json(path, state)
            if state["status"] in {"PAUSED", "STOPPED", "HUMAN_REQUIRED"}:
                state.pop("pid", None)
                chat.write_json(path, state)
                return state
            recorded_planning_usage = []

            def record_planning_usage(usage):
                current = json.loads(path.read_text())
                current["usage"].append(usage)
                chat.write_json(path, current)
                state["usage"] = current["usage"]
                recorded_planning_usage.append(usage)

            try:
                tasks, usage = plan(root, state.get("objective") or "", state["planning_tests"],
                                    model=state["planning_model"], scope=state.get("scope", ""),
                                    usage_observer=record_planning_usage)
                approved = inventory(
                    root, query=state.get("objective") or "", scope=state.get("scope") or ""
                )
                for task in tasks:
                    check_task(root, task, approved_files=approved)
                latest = json.loads(path.read_text())
                if not recorded_planning_usage:
                    latest["usage"].append(usage)
                if latest["status"] in {"PAUSED", "STOPPED"}:
                    latest.pop("pid", None)
                    chat.write_json(path, latest)
                    return latest
                state = latest
                state["tasks"] = tasks
                chat.write_json(path, state)
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
                latest = json.loads(path.read_text())
                latest["status"] = (
                    latest["status"] if latest["status"] in {"PAUSED", "STOPPED"} else "BLOCKED"
                )
                latest["reason"] = "planning failed: " + str(exc)[:500]
                latest.pop("pid", None)
                chat.write_json(path, latest)
                return latest
        state["status"] = "RUNNING"
        state.pop("reason", None)
        state["pid"] = os.getpid()
        chat.write_json(path, state)
        if state["status"] in {"PAUSED", "STOPPED", "HUMAN_REQUIRED"}:
            state.pop("pid", None)
            chat.write_json(path, state)
            return state
        approved = inventory(
            root, query=state.get("objective") or "", scope=state.get("scope") or ""
        )
        for index in range(state["index"], len(state["tasks"])):
            task = state["tasks"][index]
            historical_attempts = sum(
                1 for event in state["events"]
                if event.get("task") == index and type(event.get("attempt")) is int
            )
            state["attempt"] = max(state.get("attempt", 0), historical_attempts)
            failure = ""
            passed = False
            for attempt in range(state.get("attempt", 0) + 1, state["max_attempts"] + 1):
                if json.loads(path.read_text()).get("status") in {"STOPPED", "PAUSED"}:
                    return json.loads(path.read_text())
                state["attempt"] = attempt
                chat.write_json(path, state)
                if state["status"] in {"PAUSED", "STOPPED", "HUMAN_REQUIRED"}:
                    state.pop("pid", None)
                    chat.write_json(path, state)
                    return state
                backups = {}
                try:
                    check_task(root, task, approved_files=approved)
                    recorded_usage = []

                    def record_usage(usage):
                        current = json.loads(path.read_text())
                        current["usage"].append(usage)
                        chat.write_json(path, current)
                        state["usage"] = current["usage"]
                        recorded_usage.append(usage)

                    changes, usage = propose_edits(
                        root, task, failure,
                        model=state.get("planning_model", "ollama/qwen3.5:9b-q4_K_M"),
                        usage_observer=record_usage,
                    )
                    with chat.control_lock(path):
                        current = json.loads(path.read_text())
                        if current["status"] in {"STOPPED", "PAUSED", "HUMAN_REQUIRED"}:
                            if not recorded_usage:
                                current["usage"].append(usage)
                            current.pop("pid", None)
                            chat._write_json_unlocked(path, current)
                            return current
                        backups = {
                            name: chat.safe_target(root, name).read_text(encoding="utf-8")
                            for name in changes
                        }
                        for name, new in changes.items():
                            chat.safe_target(root, name).write_text(new, encoding="utf-8")
                    safe, info = check_edits(root, task["files"])
                    if safe and index == len(state["tasks"]) - 1:
                        # Full acceptance tests are run on the integrated final state.
                        # Earlier tasks may intentionally leave tests for later tasks failing.
                        with chat.control_lock(path):
                            current = json.loads(path.read_text())
                            if current["status"] in {"STOPPED", "PAUSED", "HUMAN_REQUIRED"}:
                                if not recorded_usage:
                                    current["usage"].append(usage)
                                current.pop("pid", None)
                                chat._write_json_unlocked(path, current)
                                return current
                            safe, validation = chat.execute_tests(root, task["tests"], timeout=100)
                            # A passing test can still modify tracked, staged or
                            # untracked files. Reinspect the actual post-test tree.
                            post_safe, post_info = check_edits(root, task["files"])
                            if not post_safe:
                                current["status"] = "HUMAN_REQUIRED"
                                current["reason"] = (
                                    "acceptance changed the approved worktree: "
                                    + post_info[-400:]
                                )
                                current["events"].append({
                                    "task": index, "attempt": attempt,
                                    "error": current["reason"][:500],
                                })
                                current.pop("pid", None)
                                chat._write_json_unlocked(path, current)
                                return current
                        info += "\n" + validation[-3500:]
                    elif safe:
                        info += (
                            "\nProject-wide acceptance tests deferred until "
                            "all dependent tasks finish"
                        )
                    if not safe:
                        failure = info[-3200:]
                        current = json.loads(path.read_text())
                        if current["status"] in {"STOPPED", "PAUSING"}:
                            with chat.control_lock(path):
                                current = json.loads(path.read_text())
                                if current["status"] == "PAUSING":
                                    current["status"] = "HUMAN_REQUIRED"
                                    current["reason"] = (
                                        "failed in-flight edits require reconciliation after pause"
                                    )
                                current["usage"] = state["usage"]
                                current["events"].append({"task": index, "attempt": attempt,
                                                          "error": failure[:500]})
                                current.pop("pid", None)
                                chat._write_json_unlocked(path, current)
                                return current
                    # Failed edits remain isolated and are included in the next correction.
                    current = json.loads(path.read_text())
                    if not recorded_usage:
                        state["usage"].append(usage)
                    state["events"].append(
                        {
                            "task": index,
                            "attempt": attempt,
                            "files": sorted(changes),
                            "model": usage["model"],
                            "provider": usage["provider"],
                            "passed": safe,
                            "validation_tail": info[-600:],
                        }
                    )
                    state["updated_at"] = time.time()
                    if current["status"] in {"STOPPED", "PAUSED"}:
                        current["usage"] = state["usage"]
                        current["events"] = state["events"]
                        current["updated_at"] = state["updated_at"]
                        current.pop("pid", None)
                        chat.write_json(path, current)
                        return current
                    chat.write_json(path, state)
                    if safe:
                        passed = True
                        break
                except (
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                    subprocess.CalledProcessError,
                    subprocess.TimeoutExpired,
                ) as exc:
                    current = json.loads(path.read_text())
                    if current["status"] in {"STOPPED", "PAUSING"}:
                        with chat.control_lock(path):
                            current = json.loads(path.read_text())
                            if current["status"] == "PAUSING":
                                current["status"] = "HUMAN_REQUIRED" if backups else "PAUSED"
                                current["reason"] = (
                                    "in-flight attempt ended during pause: " + str(exc)[:400]
                                )
                            current["events"].append({"task": index, "attempt": attempt,
                                                      "error": str(exc)[:500]})
                            current.pop("pid", None)
                            chat._write_json_unlocked(path, current)
                            return current
                    failure = str(exc)[-2000:]
                    state["events"].append(
                        {"task": index, "attempt": attempt, "error": failure[:500]}
                    )
                    current = json.loads(path.read_text())
                    if current["status"] in {"STOPPED", "PAUSED"}:
                        current["usage"] = state["usage"]
                        current["events"] = state["events"]
                        current.pop("pid", None)
                        chat.write_json(path, current)
                        return current
                    chat.write_json(path, state)
            if not passed:
                state["status"] = "BLOCKED"
                state["reason"] = f"Task {index} failed after bounded attempts: {failure[-700:]}"
                state.pop("pid", None)
                chat.write_json(path, state)
                return state
            with chat.control_lock(path):
                current = json.loads(path.read_text())
                if current.get("status") in {"STOPPED", "PAUSED", "HUMAN_REQUIRED"}:
                    current.pop("pid", None)
                    chat._write_json_unlocked(path, current)
                    return current
                state = current
                # Recheck immediately before staging: a concurrent test or
                # external writer must not smuggle extra files into the commit.
                safe_to_commit, commit_scope = check_edits(root, task["files"])
                if not safe_to_commit:
                    state["status"] = "HUMAN_REQUIRED"
                    state["reason"] = "worktree changed before commit: " + commit_scope[-400:]
                    state.pop("pid", None)
                    chat._write_json_unlocked(path, state)
                    return state
                # Commits stay on the isolated local branch, never pushed automatically.
                subprocess.run(
                    ["git", "-C", str(root), "add", "--", *task["files"]],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                committed = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(root),
                        "-c",
                        "user.name=AGF Free-first",
                        "-c",
                        "user.email=agf-free-first@localhost",
                        "commit",
                        "-q",
                        "-m",
                        f"AGF free-first task {index + 1}: {task['instruction'][:65]}",
                        "-m",
                        (f"AGF-Mission: {mission_id}\n"
                         f"AGF-Task: {index + 1}\n"
                         f"AGF-Task-Digest: {_task_digest(task)}"),
                    ],
                    capture_output=True,
                    text=True,
                )
                if committed.returncode:
                    state["status"] = "BLOCKED"
                    state["reason"] = "local Git commit failed: " + committed.stderr[-450:]
                    chat._write_json_unlocked(path, state)
                    return state
                state["index"] = index + 1
                state["expected_head"] = subprocess.check_output(
                    ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
                ).strip()
                state["attempt"] = 0
                state["updated_at"] = time.time()
                state["events"].append(
                    {
                        "task": index,
                        "committed": True,
                        "commit": subprocess.check_output(
                            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"], text=True
                        ).strip(),
                    }
                )
                if state["status"] == "PAUSING":
                    state["status"] = "PAUSED"
                    state.pop("pid", None)
                    chat._write_json_unlocked(path, state)
                    return state
                chat._write_json_unlocked(path, state)
        with chat.control_lock(path):
            state = json.loads(path.read_text())
            if state.get("status") not in {"STOPPED", "PAUSED", "HUMAN_REQUIRED"}:
                state["status"] = "COMPLETED"
            state.pop("pid", None)
            state["updated_at"] = time.time()
            chat._write_json_unlocked(path, state)
            return state
