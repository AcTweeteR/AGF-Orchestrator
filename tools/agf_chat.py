#!/usr/bin/env python3
"""Owner-operated, zero-paid-API AGF mission pilot. No AGF policy authority is granted."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DEFAULT_STATE = Path.home() / ".local/state/agf-free-first"
OLLAMA_URL = "http://127.0.0.1:11434"
OMNI_URL = "http://127.0.0.1:20128"
DEFAULT_MODELS = ("qwen3.5:9b-q4_K_M", "qwen3:14b")


def write_json(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".agf-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(record, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def request(
    url: str, payload: dict | None = None, token: str | None = None, timeout: int = 120
) -> dict:
    if not (url.startswith(OLLAMA_URL + "/") or url.startswith(OMNI_URL + "/")):
        raise ValueError("only loopback Ollama/OmniRoute endpoints are allowed")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        url, json.dumps(payload).encode() if payload is not None else None, headers=headers
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def provider_status() -> dict:
    result = {"policy": "zero-paid-api", "codex_subscription_calls": 0}
    try:
        result["ollama"] = [m["name"] for m in request(OLLAMA_URL + "/api/tags").get("models", [])]
    except (OSError, ValueError) as err:
        result["ollama_error"] = str(err)[:200]
    key = os.environ.get("AGF_OMNIROUTE_KEY")
    try:
        health = request(OMNI_URL + "/api/health", timeout=5)
        result["omniroute"] = {
            "reachable": health.get("status") == "ok",
            "endpoint": OMNI_URL,
            "free_local_route": "ollama/qwen3.5:9b-q4_K_M",
        }
        if key:
            models = request(OMNI_URL + "/v1/models", token=key)
            result["omniroute"]["model_count"] = len(models.get("data", []))
    except (OSError, ValueError) as err:
        result["omniroute"] = {"reachable": False, "reason": str(err)[:180]}
    return result


def ask_model(prompt: str, *, provider: str, model: str) -> tuple[dict, dict]:
    messages = [
        {
            "role": "system",
            "content": (
                "You are an autonomous software implementer. Respond with one JSON object "
                "with exactly the field content containing the FULL replacement file. "
                "Do not use markdown fences. Preserve unrelated code. Return valid source."
            ),
        },
        {"role": "user", "content": prompt},
    ]
    if provider == "ollama":
        reply = request(
            OLLAMA_URL + "/api/chat",
            {
                "model": model,
                "messages": messages,
                "stream": False,
                "think": False,
                "format": "json",
                "options": {"temperature": 0, "num_predict": 3500},
            },
            timeout=180,
        )
        raw = reply["message"]["content"]
        usage = {
            "provider": "ollama",
            "model": reply.get("model", model),
            "prompt_tokens": reply.get("prompt_eval_count", 0),
            "completion_tokens": reply.get("eval_count", 0),
            "api_eur": 0,
        }
    elif provider == "omniroute":
        key = os.environ.get("AGF_OMNIROUTE_KEY")
        # Loopback OmniRoute may permit local keyless requests; if API-key
        # authentication is enabled, set AGF_OMNIROUTE_KEY in the worker's env.
        # Never route through 'auto' without an explicit free-only allowlist.
        if not model or model == "auto" or not model.startswith("ollama/"):
            raise ValueError("OmniRoute pilot permits only explicit ollama/ models")
        reply = request(
            OMNI_URL + "/v1/chat/completions",
            {
                "model": model,
                "messages": messages,
                "stream": False,
                "temperature": 0,
                "max_tokens": 2400,
                "think": False,
                "response_format": {"type": "json_object"},
            },
            token=key,
            timeout=180,
        )
        used = reply.get("model")
        # OmniRoute reports the Ollama upstream's bare model ID even for an
        # explicitly addressed ollama/<id> request. Permit only that exact
        # canonical alias; never permit unrelated fallbacks or auto routing.
        if used not in {model, model.removeprefix("ollama/")}:
            raise ValueError(f"OmniRoute model substitution not approved: {used!r}")
        raw = reply["choices"][0]["message"]["content"]
        usage_data = reply.get("usage") or {}
        usage = {
            "provider": "omniroute/ollama",
            "model": model,
            "effective_model": used,
            "prompt_tokens": usage_data.get("prompt_tokens", 0),
            "completion_tokens": usage_data.get("completion_tokens", 0),
            "api_eur": 0,
        }
    else:
        raise ValueError(f"unknown provider {provider}")
    output = json.loads(raw)
    if not isinstance(output, dict) or not isinstance(output.get("content"), str):
        raise ValueError("model failed to return content as JSON string")
    return output, usage


def plan_objective(root: Path, objective: str, tests: list[str]) -> tuple[list[dict], dict]:
    """Build a bounded task plan using the local free model, no paid provider."""
    if not objective.strip() or len(objective) > 4000:
        raise ValueError("objective must contain 1-4000 characters")
    sources = sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*.py")
        if ".git" not in p.parts
        and ".venv" not in p.parts
        and "node_modules" not in p.parts
        and "__pycache__" not in p.parts
        and len(p.relative_to(root).parts) <= 6
        and not p.name.startswith("test_")
        and not p.name.endswith("_test.py")
    )[:80]
    if not sources:
        raise ValueError("natural language planner currently requires Python source files")
    prompt = (
        "Create a small, ordered software engineering plan for the objective below. "
        "Return JSON object with tasks array. Every item has file and instruction "
        "as strings. Choose ONLY existing filenames from list. One task per file "
        "unless a multi-step task is essential. At most three tasks. "
        "Do not touch tests or invent paths. If unclear, pick the most relevant file. "
        f"Objective: {objective}\nAllowed source files: {json.dumps(sources)}"
    )
    reply = request(
        OLLAMA_URL + "/api/chat",
        {
            "model": DEFAULT_MODELS[0],
            "messages": [
                {
                    "role": "system",
                    "content": "You are a software task planner. Respond with JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "think": False,
            "format": "json",
            "options": {"num_predict": 650, "temperature": 0},
        },
        timeout=150,
    )
    data = json.loads(reply["message"]["content"])
    raw_tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= 3:
        raise ValueError("planner did not return between one and three tasks")
    tasks = []
    for item in raw_tasks:
        if (
            not isinstance(item, dict)
            or item.get("file") not in sources
            or not isinstance(item.get("instruction"), str)
            or not 5 <= len(item["instruction"]) <= 1200
        ):
            raise ValueError("planner proposed an invalid or unapproved task")
        tasks.append(
            {"file": item["file"], "instruction": item["instruction"], "tests": list(tests)}
        )
    usage = {
        "provider": "ollama",
        "model": reply.get("model", DEFAULT_MODELS[0]),
        "prompt_tokens": reply.get("prompt_eval_count", 0),
        "completion_tokens": reply.get("eval_count", 0),
        "api_eur": 0,
        "purpose": "planning",
    }
    return tasks, usage


def safe_target(root: Path, name: str) -> Path:
    p = (root / name).resolve()
    if not p.is_relative_to(root) or p == root:
        raise ValueError("target file outside approved repository")
    if p.is_symlink():
        raise ValueError("symlink target not allowed")
    return p


def execute_tests(root: Path, argv: list[str], timeout: int = 90) -> tuple[bool, str]:
    if not argv or not all(isinstance(a, str) for a in argv):
        raise ValueError("test command must be a nonempty argument array")
    # This owner-supplied command is never generated or modified by an LLM.
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        argv, cwd=root, capture_output=True, text=True, timeout=timeout, env=env
    )
    report = (result.stdout + "\n" + result.stderr)[-7000:]
    zero_unittest = any("unittest" in arg for arg in argv) and "Ran 0 tests" in report
    return result.returncode == 0 and not zero_unittest, report


def run_mission(state_dir: Path, mission_id: str) -> dict:
    path = state_dir / f"{mission_id}.json"
    lock = state_dir / f"{mission_id}.lock"
    with lock.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"mission_id": mission_id, "status": "ALREADY_RUNNING"}
        state = json.loads(path.read_text())
        if state["status"] in ("COMPLETED", "BLOCKED", "STOPPED", "PAUSED"):
            return state
        root = Path(state["repository"]).resolve()
        state["status"] = "RUNNING"
        state["pid"] = os.getpid()
        write_json(path, state)
        while state["index"] < len(state["tasks"]):
            task = state["tasks"][state["index"]]
            target = safe_target(root, task["file"])
            tests = task["tests"]
            failure = ""
            completed = False
            for attempt in range(1, state["max_attempts"] + 1):
                latest = json.loads(path.read_text())
                if latest["status"] in ("PAUSED", "STOPPED"):
                    return latest
                prompt = (
                    f"Task: {task['instruction']}\nFile: {task['file']}\n"
                    f"Current file content:\n{target.read_text() if target.exists() else ''}\n"
                    f"Previous test output (if any):\n{failure[-3500:]}\n"
                    "Return the complete corrected content of this file as JSON. "
                    "Do not modify the tests."
                )
                try:
                    # Prefer the explicitly configured free route, then direct Ollama.
                    routes = state["routes"]
                    errors = []
                    for route in routes:
                        try:
                            output, usage = ask_model(prompt, **route)
                            new_content = output["content"]
                            if not new_content.strip() or len(new_content) > 120000:
                                raise ValueError("model returned empty or oversized source")
                            break
                        except (
                            ValueError,
                            KeyError,
                            IndexError,
                            OSError,
                            urllib.error.HTTPError,
                        ) as err:
                            errors.append(f"{route['provider']}/{route['model']}: {str(err)[:160]}")
                    else:
                        raise ValueError("; ".join(errors))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(new_content)
                    ok, report = execute_tests(root, tests)
                    latest = json.loads(path.read_text())
                    if latest["status"] in ("PAUSED", "STOPPED"):
                        return latest
                    state["usage"].append(usage)
                    state["events"].append(
                        {
                            "task": state["index"],
                            "attempt": attempt,
                            "model": usage["model"],
                            "passed": ok,
                            "test_tail": report[-600:],
                        }
                    )
                    state["updated_at"] = time.time()
                    write_json(path, state)
                    if ok:
                        completed = True
                        break
                    failure = report
                except (ValueError, OSError, subprocess.TimeoutExpired) as err:
                    failure = str(err)
                    state["events"].append(
                        {"task": state["index"], "attempt": attempt, "error": failure[:500]}
                    )
                    write_json(path, state)
            if not completed:
                # Preserve modifications and evidence for review rather than silently
                # pretending the original source is now verified.
                state["status"] = "BLOCKED"
                state["reason"] = (
                    f"Task {state['index']} failed after bounded retries: {failure[-800:]}"
                )
                write_json(path, state)
                return state
            state["index"] += 1
            state["updated_at"] = time.time()
            write_json(path, state)
        latest = json.loads(path.read_text())
        if latest["status"] in ("PAUSED", "STOPPED"):
            return latest
        state["status"] = "COMPLETED"
        state.pop("pid", None)
        write_json(path, state)
        return state


def parse_args():
    parser = argparse.ArgumentParser(description="AGF free-first owner-side pilot")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("providers")
    sub.add_parser("list")
    create = sub.add_parser("start")
    create.add_argument("--repository", type=Path, required=True)
    task_mode = create.add_mutually_exclusive_group(required=True)
    task_mode.add_argument("--tasks", type=Path, help="JSON array of approved file tasks")
    task_mode.add_argument("--objective", help="Natural-language objective, planned locally")
    create.add_argument(
        "--test-command",
        default="python3 -m unittest discover",
        help="Single quoted, fixed test command; parsed as argv without a shell",
    )
    create.add_argument("--model", default=DEFAULT_MODELS[0])
    create.add_argument("--fallback-model", default=DEFAULT_MODELS[1])
    create.add_argument("--omniroute-model", help="Explicit ollama/ model, optional")
    create.add_argument("--max-attempts", type=int, default=3)
    create.add_argument("--background", action="store_true")
    create.add_argument(
        "--project-wide", action="store_true", help="isolated multi-file mission with local commits"
    )
    for name in ("run", "status", "report", "pause", "resume", "stop"):
        c = sub.add_parser(name)
        c.add_argument("mission_id")
    sub.add_parser("worker")
    return parser.parse_args()


def main():
    args = parse_args()
    state_dir = args.state_dir.expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    if args.command == "providers":
        print(json.dumps(provider_status(), ensure_ascii=False))
        return
    if args.command == "list":
        missions = []
        for item in sorted(state_dir.glob("*.json")):
            try:
                record = json.loads(item.read_text())
                if "mission_id" in record:
                    missions.append(
                        {
                            key: record.get(key)
                            for key in ("mission_id", "status", "repository", "index")
                        }
                    )
            except (ValueError, OSError):
                continue
        print(json.dumps({"missions": missions}, ensure_ascii=False))
        return
    if args.command == "start":
        if args.project_wide:
            if __package__:
                from . import agf_full
            else:
                import agf_full
            result = agf_full.start(
                state_dir,
                args.repository.expanduser(),
                objective=args.objective,
                tasks_path=args.tasks,
                tests=args.test_command,
                model=args.omniroute_model or "ollama/qwen3.5:9b-q4_K_M",
                max_attempts=args.max_attempts,
                background=args.background,
            )
            print(json.dumps(result, ensure_ascii=False))
            return
        root = args.repository.expanduser().resolve()
        if not root.is_dir():
            raise SystemExit("repository does not exist")
        subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        if args.tasks:
            tasks = json.loads(args.tasks.read_text())
            planning_usage = None
        else:
            tasks, planning_usage = plan_objective(
                root, args.objective, shlex.split(args.test_command)
            )
        if not isinstance(tasks, list) or not tasks:
            raise SystemExit("tasks must be a nonempty JSON array")
        for item in tasks:
            if not isinstance(item, dict) or not isinstance(item.get("instruction"), str):
                raise SystemExit("invalid task")
            safe_target(root, item["file"])
            if not isinstance(item.get("tests"), list):
                raise SystemExit("tests must be an argument array")
        routes = []
        if args.omniroute_model:
            if not args.omniroute_model.startswith("ollama/"):
                raise SystemExit("OmniRoute model must be explicitly ollama/...")
            routes.append({"provider": "omniroute", "model": args.omniroute_model})
        routes += [{"provider": "ollama", "model": args.model}]
        if args.fallback_model and args.fallback_model != args.model:
            routes.append({"provider": "ollama", "model": args.fallback_model})
        mid = uuid.uuid4().hex[:16]
        record = {
            "mission_id": mid,
            "status": "PENDING",
            "repository": str(root),
            "tasks": tasks,
            "index": 0,
            "max_attempts": max(1, min(5, args.max_attempts)),
            "routes": routes,
            "usage": [planning_usage] if planning_usage else [],
            "events": [],
            "objective": args.objective if not args.tasks else None,
            "api_budget_eur": 0,
            "codex_calls": 0,
            "created_at": time.time(),
        }
        write_json(state_dir / f"{mid}.json", record)
        if args.background:
            with (state_dir / f"{mid}.log").open("ab") as log:
                subprocess.Popen(
                    [sys.executable, __file__, "--state-dir", str(state_dir), "run", mid],
                    stdout=log,
                    stderr=log,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
        print(
            json.dumps({"mission_id": mid, "status": "STARTED" if args.background else "PENDING"})
        )
        return
    if args.command == "worker":
        for record in state_dir.glob("*.json"):
            try:
                state = json.loads(record.read_text())
                if state.get("status") in ("PENDING", "RUNNING"):
                    if state.get("engine") == "full":
                        if __package__:
                            from . import agf_full
                        else:
                            import agf_full
                        agf_full.run_mission(state_dir, state["mission_id"])
                    else:
                        run_mission(state_dir, state["mission_id"])
            except (KeyError, OSError, ValueError, json.JSONDecodeError):
                pass
        print(json.dumps({"status": "WORKER_CHECKED"}))
        return
    path = state_dir / f"{args.mission_id}.json"
    if not path.is_file():
        raise SystemExit("unknown mission id")
    state = json.loads(path.read_text())
    if args.command in ("run", "resume"):
        if args.command == "resume" and state["status"] in ("PAUSED", "BLOCKED"):
            state["status"] = "PENDING"
            state.pop("reason", None)
            write_json(path, state)
        if state.get("engine") == "full":
            if __package__:
                from . import agf_full
            else:
                import agf_full
            state = agf_full.run_mission(state_dir, args.mission_id)
        else:
            state = run_mission(state_dir, args.mission_id)
    elif args.command in ("pause", "stop"):
        state["status"] = "PAUSED" if args.command == "pause" else "STOPPED"
        write_json(path, state)
    if args.command == "status":
        print(
            json.dumps(
                {
                    key: state.get(key)
                    for key in (
                        "mission_id",
                        "status",
                        "index",
                        "reason",
                        "pid",
                        "api_budget_eur",
                        "codex_calls",
                        "worktree",
                        "branch",
                        "engine",
                    )
                },
                ensure_ascii=False,
            )
        )
    elif args.command == "report":
        print(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        print(
            json.dumps(
                {
                    "mission_id": args.mission_id,
                    "status": state["status"],
                    "tasks_done": state["index"],
                    "tokens": sum(
                        (x.get("prompt_tokens") or 0) + (x.get("completion_tokens") or 0)
                        for x in state["usage"]
                    ),
                }
            )
        )


if __name__ == "__main__":
    main()
