"""Multi-file, isolated, persistent Free-First missions under owner control.

No paid inference or authorization to push/merge/publish. The task planner and
implementer are untrusted. A fresh Git worktree isolates every project mission.
"""

from __future__ import annotations

import fcntl
import json
import os
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
            or rel.name.startswith("test_")
            or rel.name.endswith("_test.py")
        ):
            continue
        path = root / rel
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 1000000:
            continue
        score = sum(4 if t in rel.name.lower() else 1 for t in terms if t in name.lower())
        eligible.append((-score, name))
    eligible.sort()
    return [name for _, name in eligible[:220]]


def route_json(prompt: str, *, purpose: str, model="ollama/qwen3.5:9b-q4_K_M"):
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
        if actual not in {model, model.removeprefix("ollama/")}:
            raise ValueError("unexpected gateway model")
        result = json.loads(reply["choices"][0]["message"]["content"])
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
        if actual != model.removeprefix("ollama/"):
            raise ValueError("unexpected local model")
        result = json.loads(reply["message"]["content"])
        evidence = {
            "purpose": purpose,
            "provider": "ollama",
            "model": model,
            "effective_model": actual,
            "prompt_tokens": reply.get("prompt_eval_count", 0),
            "completion_tokens": reply.get("eval_count", 0),
            "api_eur": 0,
        }
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
    data, evidence = route_json(prompt, purpose="planning", model=model)
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


def check_task(root: Path, task: dict) -> None:
    names = task.get("files")
    if (
        not isinstance(names, list)
        or not 1 <= len(names) <= MAX_FILES
        or len(set(names)) != len(names)
    ):
        raise ValueError("task must select 1-4 distinct files")
    for name in names:
        if not isinstance(name, str) or name not in inventory(root):
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
    worktree, branch = create_worktree(repository, mission_id)
    try:
        if tasks_path:
            tasks = json.loads(tasks_path.read_text())
            planning = None
        else:
            tasks, planning = plan(worktree, objective or "", tests, model=model, scope=scope)
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 30:
            raise ValueError("must have between 1 and 30 tasks")
        for task in tasks:
            check_task(worktree, task)
            if scope and any(
                not (name == scope or name.startswith(scope.rstrip("/") + "/"))
                for name in task["files"]
            ):
                raise ValueError("task file outside owner-selected scope")
        record = {
            "engine": "full",
            "mission_id": mission_id,
            "status": "PENDING",
            "repository": str(repository),
            "worktree": str(worktree),
            "branch": branch,
            "objective": objective,
            "scope": scope,
            "tasks": tasks,
            "index": 0,
            "attempt": 0,
            "max_attempts": max(1, min(5, max_attempts)),
            "usage": [planning] if planning else [],
            "events": [],
            "api_budget_eur": 0,
            "codex_calls": 0,
            "created_at": time.time(),
        }
        state_dir.mkdir(parents=True, exist_ok=True)
        chat.write_json(state_dir / (mission_id + ".json"), record)
    except Exception:
        subprocess.run(
            ["git", "-C", str(repository), "worktree", "remove", "--force", str(worktree)],
            capture_output=True,
        )
        subprocess.run(["git", "-C", str(repository), "branch", "-D", branch], capture_output=True)
        raise
    if background:
        with (state_dir / (mission_id + ".log")).open("ab") as logfile:
            subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).with_name("agf_chat.py")),
                    "--state-dir",
                    str(state_dir),
                    "run",
                    mission_id,
                ],
                stdin=subprocess.DEVNULL,
                stdout=logfile,
                stderr=logfile,
                start_new_session=True,
            )
    return {
        "mission_id": mission_id,
        "status": "STARTED" if background else "PENDING",
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
    root: Path, task: dict, previous_error: str, *, model="ollama/qwen3.5:9b-q4_K_M"
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
    result, usage = route_json(prompt, purpose="implementation", model=model)
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


def check_edits(worktree: Path, names: list[str]) -> tuple[bool, str]:
    result = subprocess.run(
        ["git", "-C", str(worktree), "diff", "--check"], capture_output=True, text=True
    )
    if result.returncode != 0:
        return False, result.stderr[-1800:] + result.stdout[-1800:]
    changed = subprocess.run(
        ["git", "-C", str(worktree), "diff", "--name-only", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    actual = [name for name in changed.split("\0") if name]
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


def run_mission(state_dir: Path, mission_id: str) -> dict:
    path = state_dir / (mission_id + ".json")
    lock = state_dir / (mission_id + ".lock")
    with lock.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"mission_id": mission_id, "status": "ALREADY_RUNNING"}
        state = json.loads(path.read_text())
        if state["status"] in {"COMPLETED", "BLOCKED", "PAUSED", "STOPPED"}:
            return state
        root = Path(state["worktree"]).resolve()
        if not root.is_dir():
            state["status"] = "BLOCKED"
            state["reason"] = "isolated mission worktree is missing"
            chat.write_json(path, state)
            return state
        state["status"] = "RUNNING"
        state.pop("reason", None)
        state["pid"] = os.getpid()
        chat.write_json(path, state)
        for index in range(state["index"], len(state["tasks"])):
            task = state["tasks"][index]
            failure = ""
            passed = False
            for attempt in range(1, state["max_attempts"] + 1):
                if json.loads(path.read_text()).get("status") in {"STOPPED", "PAUSED"}:
                    return json.loads(path.read_text())
                backups = {}
                try:
                    check_task(root, task)
                    changes, usage = propose_edits(root, task, failure)
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
                        safe, validation = chat.execute_tests(root, task["tests"], timeout=100)
                        info += "\n" + validation[-3500:]
                    elif safe:
                        info += (
                            "\nProject-wide acceptance tests deferred until "
                            "all dependent tasks finish"
                        )
                    if not safe:
                        failure = info[-3200:]
                    # Restore failed attempt so the next model repairs known baseline
                    # rather than being conditioned on broken intermediate source.
                    if not safe:
                        for name, original in backups.items():
                            chat.safe_target(root, name).write_text(original, encoding="utf-8")
                    current = json.loads(path.read_text())
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
                        state["status"] = current["status"]
                        chat.write_json(path, state)
                        return state
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
                    for name, original in backups.items():
                        chat.safe_target(root, name).write_text(original, encoding="utf-8")
                    failure = str(exc)[-2000:]
                    state["events"].append(
                        {"task": index, "attempt": attempt, "error": failure[:500]}
                    )
                    chat.write_json(path, state)
            if not passed:
                state["status"] = "BLOCKED"
                state["reason"] = f"Task {index} failed after bounded attempts: {failure[-700:]}"
                state.pop("pid", None)
                chat.write_json(path, state)
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
                    "commit",
                    "-q",
                    "-m",
                    f"AGF free-first task {index + 1}: {task['instruction'][:65]}",
                ],
                capture_output=True,
                text=True,
            )
            if committed.returncode:
                state["status"] = "BLOCKED"
                state["reason"] = "local Git commit failed: " + committed.stderr[-450:]
                chat.write_json(path, state)
                return state
            state["index"] = index + 1
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
            chat.write_json(path, state)
        latest = json.loads(path.read_text())
        if latest.get("status") in {"STOPPED", "PAUSED"}:
            return latest
        state["status"] = "COMPLETED"
        state.pop("pid", None)
        state["updated_at"] = time.time()
        chat.write_json(path, state)
        return state
