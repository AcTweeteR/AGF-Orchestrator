"""Command-line interface for the Director Runtime MVP."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from .adapters.codex import CodexAdapter, resolve_codex_executable
from .adapters.ollama import OllamaOpenHandsAdapter
from .adapters.openhands import OpenHandsSDKAdapter
from .architect_planning import (
    ArchitectPlanningError,
    ProviderArchitect,
    ProviderInvocationError,
    architect_response_schema,
)
from .authority_context import resolve_authority
from .capability_profiles import CapabilityProfileError, CapabilityStatus
from .capability_selection import CapabilityCandidate, SelectionGates
from .compliance import ComplianceChecker
from .constitution import ConstitutionAuthority, ConstitutionVerificationError
from .delivery import DeliveryPipeline, write_delivery_report
from .director import Director
from .execution_models import ExecutionStatus
from .executor import ExecutionValidationError, Executor, load_plan, write_execution_result
from .git_delivery import DraftPRCreator
from .inbox import build_inbox
from .locking import LockError
from .models import PlanStatus
from .policy_authority import EffectiveRisk, PolicyActivationError
from .preflight import PreflightError, collect_repository
from .project_models import ProjectPolicy
from .project_registry import ProjectRegistry, ProjectRegistryError, parse_remote_url
from .provider_intelligence import (
    ARCHITECT_REQUIREMENTS,
    ProviderIntelligenceError,
    ProviderIntelligenceStore,
    build_state,
    make_profile,
)
from .reviewer import CodexReviewerAdapter, DeterministicReviewer
from .session_manager import SessionManager, SessionManagerError, _now
from .session_store import SessionStore, SessionStoreError

AGF_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def load_cli_environment() -> None:
    """Load only an approved AGF .env without overriding process environment values."""
    configured = os.environ.get("AGF_ENV_FILE")
    selected = Path(configured).expanduser() if configured else AGF_PACKAGE_ROOT / ".env"
    if not _approved_dotenv_path(selected, allow_registry_failure=not configured):
        return
    load_dotenv(dotenv_path=selected.resolve(), override=False)


def _approved_dotenv_path(selected: Path, *, allow_registry_failure: bool = False) -> bool:
    """Validate a dotenv path without exposing its contents or following escapes."""
    candidate = selected if selected.is_absolute() else Path.cwd() / selected
    if _contains_symlink(candidate):
        return False
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return False
    if not resolved.is_file() or not candidate.exists():
        return False
    try:
        managed_roots = [
            Path(project.repository_root).resolve() for project in ProjectRegistry().list()
        ]
    except (OSError, ProjectRegistryError, ValueError):
        return allow_registry_failure
    return not any(resolved == root or root in resolved.parents for root in managed_roots)


def _contains_symlink(path: Path) -> bool:
    """Reject symlink components so dotenv cannot escape an approved location."""
    current = Path(path.anchor) if path.is_absolute() else Path.cwd()
    parts = path.parts[1:] if path.is_absolute() else path.parts
    for part in parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agf-orchestrator")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="create a validated execution plan")
    plan.add_argument("--repository", help="path to the target Git repository")
    plan.add_argument("--project", help="registered project name or ID")
    plan.add_argument("--goal", required=True, help="high-level project goal")
    plan.add_argument("--output", required=True, help="JSON output path")
    plan.add_argument("--allow-dirty", action="store_true", help="allow a dirty target repository")
    execute = commands.add_parser("execute", help="execute one approved task under safety gates")
    execute.add_argument("--plan", required=True, help="validated execution plan JSON")
    execute.add_argument("--task", required=True, help="selected task ID")
    execute.add_argument("--repository", help="target Git repository")
    execute.add_argument("--project", help="registered project name or ID")
    execute.add_argument("--adapter", choices=["codex", "openhands", "ollama"], default="codex")
    execute.add_argument("--allow-openhands-llm-env", action="store_true")
    execute.add_argument("--dry-run", action="store_true", help="explicitly request dry-run")
    execute.add_argument("--execute", action="store_true", help="allow live execution")
    execute.add_argument(
        "--confirm-execution", action="store_true", help="confirm live execution explicitly"
    )
    execute.add_argument("--codex-path", default=None, help="Codex executable path")
    execute.add_argument("--openhands-path", default="openhands", help="OpenHands executable path")
    execute.add_argument("--timeout", type=float, default=300.0, help="Codex timeout in seconds")
    execute.add_argument("--output", help="optional report path outside the target repository")
    deliver = commands.add_parser("deliver", help="run the autonomous delivery pipeline")
    deliver.add_argument("--plan", required=True)
    deliver.add_argument("--task", required=True)
    deliver.add_argument("--repository")
    deliver.add_argument("--project", help="registered project name or ID")
    deliver.add_argument("--adapter", choices=["codex", "openhands", "ollama"], default="codex")
    deliver.add_argument("--allow-openhands-llm-env", action="store_true")
    deliver.add_argument("--output", required=True)
    deliver.add_argument("--execute", action="store_true")
    deliver.add_argument("--confirm-execution", action="store_true")
    deliver.add_argument("--confirm-delivery", action="store_true")
    deliver.add_argument("--reviewer", choices=["deterministic", "codex"], default="deterministic")
    deliver.add_argument("--simulate-pr", action="store_true")
    deliver.add_argument("--codex-path", default=None)
    deliver.add_argument("--openhands-path", default="openhands")
    deliver.add_argument("--timeout", type=float, default=300.0)
    project = commands.add_parser("project", help="manage explicitly registered projects")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    project_add = project_commands.add_parser("add")
    project_add.add_argument("--name", required=True)
    project_add.add_argument("--repository", required=True)
    project_add.add_argument("--allow-dirty-planning", action="store_true")
    project_add.add_argument("--allow-live-execution", action="store_true")
    project_add.add_argument("--allow-delivery", action="store_true")
    project_add.add_argument("--no-human-merge", action="store_true")
    project_add.add_argument("--maximum-correction-rounds", type=int, default=2)
    project_add.add_argument("--allowed-remote-host", action="append", default=[])
    project_add.add_argument("--json", action="store_true")
    for command in ("list", "show", "verify", "remove"):
        item = project_commands.add_parser(command)
        if command != "list":
            item.add_argument("--project", required=True)
        item.add_argument("--json", action="store_true")
    session = commands.add_parser("session", help="manage persistent workflow sessions")
    session_commands = session.add_subparsers(dest="session_command", required=True)
    session_start = session_commands.add_parser("start")
    session_start.add_argument("--project", required=True)
    session_start.add_argument("--goal", required=True)
    session_start.add_argument("--json", action="store_true")
    for command in ("list",):
        item = session_commands.add_parser(command)
        item.add_argument("--json", action="store_true")
    for command in ("show", "resume", "assess", "repair-lineage", "cancel"):
        item = session_commands.add_parser(command)
        item.add_argument("--session", required=True)
        item.add_argument("--json", action="store_true")
        if command == "assess":
            item.add_argument(
                "--architect-config",
                help="approved state-root JSON with capability profiles, gates, and providers",
            )
    resume = session_commands.choices["resume"]
    resume.add_argument("--project", help="registered project name or ID")
    resume.add_argument("--execute", action="store_true")
    resume.add_argument("--confirm-execution", action="store_true")
    resume.add_argument("--confirm-delivery", action="store_true")
    lock = session_commands.add_parser("lock-status")
    lock.add_argument("--session", required=True)
    lock.add_argument("--json", action="store_true")
    inbox = commands.add_parser("inbox", help="show only items requiring attention")
    inbox.add_argument("--json", action="store_true")
    policy = commands.add_parser("policy", help="verify owner-signed policy state")
    policy_commands = policy.add_subparsers(dest="policy_command", required=True)
    policy_verify = policy_commands.add_parser("verify")
    policy_verify.add_argument("--project", required=True)
    policy_verify.add_argument("--json", action="store_true")
    intelligence = commands.add_parser(
        "provider-intelligence", help="inspect or bootstrap durable capability evidence"
    )
    intelligence_commands = intelligence.add_subparsers(dest="intelligence_command", required=True)
    intelligence_inspect = intelligence_commands.add_parser("inspect")
    intelligence_inspect.add_argument("--project", required=True)
    intelligence_inspect.add_argument("--json", action="store_true")
    intelligence_bootstrap = intelligence_commands.add_parser("bootstrap-architect")
    intelligence_bootstrap.add_argument("--project", required=True)
    intelligence_bootstrap.add_argument(
        "--candidate-output",
        help="write unsigned evidence for the external owner controller to sign",
    )
    intelligence_bootstrap.add_argument("--json", action="store_true")
    return parser


def _output(value, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True))
    elif isinstance(value, list):
        for item in value:
            print(item)
    else:
        print(value)


def _resolve_project(args: argparse.Namespace, *, verify: bool = True):
    registry = ProjectRegistry()
    selected = registry.get(args.project) if getattr(args, "project", None) else None
    requested = (
        Path(args.repository).expanduser().resolve() if getattr(args, "repository", None) else None
    )
    if requested is not None:
        try:
            context = collect_repository(requested, allow_dirty=True)
            canonical = Path(context.root).resolve()
        except PreflightError as exc:
            raise ProjectRegistryError(f"repository selection failed: {exc}") from exc
        if requested != canonical:
            raise ProjectRegistryError("repository path must be the canonical repository root")
        projects = registry.list()
        nested = [p for p in projects if canonical in Path(p.repository_root).resolve().parents]
        if nested:
            raise ProjectRegistryError("repository path is nested inside a registered project")
        matches = [p for p in projects if Path(p.repository_root).resolve() == canonical]
        if len(matches) != 1:
            raise ProjectRegistryError("repository path must match exactly one registered project")
        if selected and selected.project_id != matches[0].project_id:
            raise ProjectRegistryError("repository does not match the selected project")
        selected = matches[0]
    if selected is None:
        raise ProjectRegistryError("an exactly one registered project selection is required")
    if verify:
        selected = registry.verify(selected.project_id)
    if selected.status.value != "ACTIVE":
        raise ProjectRegistryError(f"project status is {selected.status.value}")
    return selected, Path(selected.repository_root).resolve()


def _validate_plan_project(plan, project, repository: Path) -> None:
    context = plan.repository
    if Path(context.root).resolve() != repository:
        raise ProjectRegistryError("plan repository root does not match the registered project")
    if parse_remote_url(context.origin).identity != parse_remote_url(project.origin_url).identity:
        raise ProjectRegistryError("plan origin does not match the registered project")
    if context.branch != project.default_branch:
        raise ProjectRegistryError("plan branch does not match the registered project")
    if context.head_sha != project.current_head_sha:
        raise ProjectRegistryError("plan HEAD does not match the verified project HEAD")


def _dirty_policy(project, repository: Path, allow_dirty: bool) -> None:
    dirty = bool(collect_repository(repository, allow_dirty=True).clean is False)
    if dirty and (not allow_dirty or not project.policy.allow_dirty_planning):
        raise ProjectRegistryError(
            "dirty planning requires --allow-dirty and project policy allow_dirty_planning"
        )


def _verify_constitution(project) -> None:
    try:
        ConstitutionAuthority().resolve(project.project_id)
    except ConstitutionVerificationError as exc:
        raise ProjectRegistryError(str(exc)) from exc


def run_project(args: argparse.Namespace) -> int:
    registry = ProjectRegistry()
    try:
        if args.project_command == "add":
            project = registry.add(
                args.name,
                args.repository,
                policy=ProjectPolicy(
                    allowed_remote_hosts=args.allowed_remote_host,
                    allow_dirty_planning=args.allow_dirty_planning,
                    allow_live_execution=args.allow_live_execution,
                    allow_delivery=args.allow_delivery,
                    require_human_merge=not args.no_human_merge,
                    maximum_correction_rounds=args.maximum_correction_rounds,
                ),
            )
            _output(project.to_dict(), args.json)
        elif args.project_command == "list":
            _output([p.to_dict() for p in registry.list()], args.json)
        elif args.project_command == "show":
            _output(registry.get(args.project).to_dict(), args.json)
        elif args.project_command == "verify":
            _output(registry.verify(args.project).to_dict(), args.json)
        elif args.project_command == "remove":
            registry.remove(args.project)
            _output({"removed": args.project}, args.json)
        return 0
    except (ProjectRegistryError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def run_session(args: argparse.Namespace) -> int:
    try:
        manager = SessionManager(architect=_architect_from_config(args))
        if args.session_command == "start":
            session = manager.start(args.project, args.goal)
            _output(session.to_dict(), args.json)
        elif args.session_command == "list":
            _output([s.to_dict() for s in manager.list()], args.json)
        elif args.session_command == "show":
            _output(manager.get(args.session).to_dict(), args.json)
        elif args.session_command == "resume":
            session = manager.resume(
                args.session,
                project_name=args.project,
                execute=args.execute,
                confirm_execution=args.confirm_execution,
                confirm_delivery=args.confirm_delivery,
            )
            _output(session.to_dict(), args.json)
        elif args.session_command == "assess":
            _output(manager.assess(args.session).to_dict(), args.json)
        elif args.session_command == "repair-lineage":
            _output(manager.repair_lineage(args.session).to_dict(), args.json)
        elif args.session_command == "cancel":
            _output(manager.cancel(args.session).to_dict(), args.json)
        elif args.session_command == "lock-status":
            _output(manager.lock_status(args.session), args.json)
        return 0
    except (
        SessionManagerError,
        ProjectRegistryError,
        SessionStoreError,
        LockError,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


class _AdapterArchitectProvider:
    def __init__(self, provider_id: str, adapter: object) -> None:
        self.provider_id = provider_id
        self.adapter = adapter

    @staticmethod
    def _instruction(request) -> str:
        assessment = request.assessment
        evidence_inventory = sorted({
            *assessment.repository_structure,
            assessment.evidence_hash,
        })
        return (
            "Return exactly one JSON object matching this schema, with no wrapper key: "
            + json.dumps(architect_response_schema(), sort_keys=True)
            + ". Do not wrap it "
            "in an architecture key. No Markdown, code fences, prose, invented evidence, "
            "paths, or dependencies. Use proposed_outcome=NO_JUSTIFIED_WORK only when formally "
            "justified; malformed output is a provider failure, never no-work. Every value in "
            "evidence_references MUST be exactly one item from this closed evidence inventory: "
            + json.dumps(evidence_inventory, ensure_ascii=False)
            + ". Do not cite any other string.\n"
            + json.dumps(request.to_dict(), ensure_ascii=False, sort_keys=True)
        )

    def propose(self, request) -> str:
        instruction = self._instruction(request)
        kwargs = {"sandbox": "read-only"}
        if isinstance(self.adapter, CodexAdapter):
            kwargs["output_schema"] = architect_response_schema()
        result = self.adapter.execute(instruction, request.repository.root, **kwargs)
        if result.transport_error or not result.invocation_verified:
            raise ProviderInvocationError(
                f"{self.provider_id} architect invocation failed: "
                f"{result.transport_error or 'missing verified response'}"
            )
        if not result.final_message:
            raise ProviderInvocationError(f"{self.provider_id} architect response is missing")
        return result.final_message


def _architect_from_config(args: argparse.Namespace):
    if getattr(args, "session_command", None) != "assess":
        return None
    config_path = getattr(args, "architect_config", None)
    store = SessionStore()
    session = SessionStore().load(args.session)
    intelligence_store = ProviderIntelligenceStore().for_project(session.project_id)
    if not config_path:
        default_path = intelligence_store.path
        if not default_path.is_file():
            return None
        config_path = str(default_path)
    candidate_path = Path(config_path).expanduser()
    try:
        resolved = candidate_path.resolve(strict=True)
    except OSError as exc:
        raise ArchitectPlanningError("architect config is unreadable") from exc
    state_root = store.state_dir.resolve()
    if state_root not in resolved.parents or _contains_symlink(candidate_path):
        raise ArchitectPlanningError("architect config must be under approved AGF state root")
    try:
        if resolved != intelligence_store.path.resolve():
            raise ProviderIntelligenceError("architect config is not the project-bound state")
        state = intelligence_store.load()
        if state.project_id != session.project_id:
            raise ProviderIntelligenceError("provider intelligence session binding differs")
        project = ProjectRegistry().get(session.project_id)
        target_sha = _git_output(Path(project.repository_root), "rev-parse", "HEAD")
        snapshot = resolve_authority(project.project_id).policy_snapshot
        _validate_provider_intelligence_runtime(state, project, target_sha, snapshot, _now())
        project_id = state.project_id
        candidates = state.candidates
        gates = state.gates
        providers = {}
        for provider_id, interface in state.provider_interfaces:
            if interface == "codex":
                providers[provider_id] = _AdapterArchitectProvider(provider_id, CodexAdapter())
            elif interface == "openhands":
                providers[provider_id] = _AdapterArchitectProvider(
                    provider_id, OpenHandsSDKAdapter(allow_llm_env=False)
                )
            else:
                raise ProviderIntelligenceError("provider interface is not approved")
    except (KeyError, TypeError, ValueError, OSError, ProviderIntelligenceError) as exc:
        raise ArchitectPlanningError("architect config is invalid") from exc
    return ProviderArchitect(candidates, providers, now=_now(), project_id=project_id, gates=gates)


def run_inbox(args: argparse.Namespace) -> int:
    try:
        items = build_inbox(SessionStore(), ProjectRegistry())
        if args.json:
            _output([item.to_dict() for item in items], True)
        else:
            for item in items:
                print(f"{item.priority} {item.project}/{item.session_id}: {item.required_action}")
        return 0
    except (ProjectRegistryError, SessionStoreError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def run_provider_intelligence(args: argparse.Namespace) -> int:
    """Owner-invoked evidence bootstrap; no runtime mutation authority is exposed."""
    try:
        registry = ProjectRegistry()
        project = registry.verify(args.project)
        store = ProviderIntelligenceStore().for_project(project.project_id)
        if args.intelligence_command == "inspect":
            state = store.load()
            if state.project_id != project.project_id:
                raise ProviderIntelligenceError("provider intelligence project binding differs")
            target_sha = _git_output(Path(project.repository_root), "rev-parse", "HEAD")
            snapshot = resolve_authority(project.project_id).policy_snapshot
            _validate_provider_intelligence_runtime(state, project, target_sha, snapshot, _now())
            _output(
                {
                    "project_id": state.project_id,
                    "target_sha": state.target_sha,
                    "requirements_hash": state.requirements_hash,
                    "profiles": [candidate.profile.profile_id for candidate in state.candidates],
                    "gates": state.to_dict()["gates"],
                    "policy_generation": state.policy_generation,
                    "state_sha256": state.state_sha256,
                },
                args.json,
            )
            return 0

        root = Path(project.repository_root)
        target_sha = _git_output(root, "rev-parse", "HEAD")
        resolved_authority = resolve_authority(project.project_id)
        policy = resolved_authority.policy
        constitution = resolved_authority.constitution
        if policy is None:
            raise ProviderIntelligenceError("active policy is unavailable")
        snapshot = resolved_authority.policy_snapshot
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("generation"), int):
            raise ProviderIntelligenceError("active policy generation is unavailable")
        authority_generation = _authority_generation(resolved_authority, snapshot)
        now = _now()
        existing = None
        profile_version = 1
        if store.path.exists():
            try:
                existing = store.load()
            except ProviderIntelligenceError as exc:
                if str(exc) not in {
                    "provider evidence is bound to stale authority",
                    "provider intelligence evidence is stale",
                }:
                    raise
                existing = store._load_for_owner_recovery()
            if existing.project_id != project.project_id:
                raise ProviderIntelligenceError(
                    "existing provider intelligence is bound to a different project"
                )
            try:
                _validate_provider_intelligence_runtime(
                    existing, project, target_sha, snapshot, now
                )
                _output(
                    {
                        "project_id": existing.project_id,
                        "target_sha": existing.target_sha,
                        "profile_id": existing.candidates[0].profile.profile_id,
                        "profile_sha256": existing.candidates[0].profile.profile_sha256,
                        "probe_pass": True,
                        "idempotent": True,
                        "gates": existing.to_dict()["gates"],
                        "requirements_hash": existing.requirements_hash,
                        "state_sha256": existing.state_sha256,
                    },
                    args.json,
                )
                return 0
            except ProviderIntelligenceError:
                # A verified, project-bound state may be renewed when any
                # consequential input (policy, Constitution, provider, or
                # freshness) changes.  Tampered state fails in store.load()
                # before reaching this recovery path.
                profile_version = (
                    max(candidate.profile.profile_version for candidate in existing.candidates) + 1
                )
        expires = (
            (datetime.now(UTC).replace(microsecond=0) + timedelta(hours=24))
            .isoformat()
            .replace("+00:00", "Z")
        )
        executable = resolve_codex_executable()
        if executable.path is None:
            raise ProviderIntelligenceError("no approved Architect provider interface is available")
        adapter = CodexAdapter(executable=executable.path, timeout=90.0)
        probe = adapter.execute(
            "Read-only AGF Architect capability canary. Do not edit files. Return only JSON "
            "with exactly these keys: head_sha, repository_name, reasoning_answer, "
            "context_marker, summary. Inspect the repository and report its current HEAD SHA "
            f"and name ({root.name}). Solve 2+4 and return reasoning_answer as the string '6'. "
            "Return context_marker exactly as AGF-CONTEXT-CANARY-7f3d. Do not report capability "
            f"claims. Inspect only {root} and do not modify it.",
            str(root),
            sandbox="read-only",
        )
        capabilities = {name: CapabilityStatus.UNKNOWN for name in ARCHITECT_REQUIREMENTS}
        if probe.invocation_verified and probe.final_message:
            try:
                result = json.loads(probe.final_message)
                exact_schema = set(result) == {
                    "head_sha",
                    "repository_name",
                    "reasoning_answer",
                    "context_marker",
                    "summary",
                }
                structured = (
                    exact_schema
                    and isinstance(result["head_sha"], str)
                    and isinstance(result["repository_name"], str)
                    and isinstance(result["reasoning_answer"], str)
                    and isinstance(result["context_marker"], str)
                    and isinstance(result["summary"], str)
                )
                independent_head_sha = _git_output(root, "rev-parse", "HEAD")
                independent_repository_name = root.name
                repository_understanding = (
                    structured
                    and result["head_sha"] == independent_head_sha == target_sha
                    and result["repository_name"] == independent_repository_name
                )
                reasoning = structured and result["reasoning_answer"] == "6"
                context_capacity = (
                    structured and result["context_marker"] == "AGF-CONTEXT-CANARY-7f3d"
                )
                capabilities = {
                    "repository-understanding": (
                        CapabilityStatus.SUPPORTED
                        if repository_understanding
                        else CapabilityStatus.UNKNOWN
                    ),
                    "structured-output": (
                        CapabilityStatus.SUPPORTED if structured else CapabilityStatus.UNKNOWN
                    ),
                    "reasoning": (
                        CapabilityStatus.SUPPORTED if reasoning else CapabilityStatus.UNKNOWN
                    ),
                    "context-capacity": (
                        CapabilityStatus.SUPPORTED if context_capacity else CapabilityStatus.UNKNOWN
                    ),
                }
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        probe_ok = _capability_probe_passed(capabilities)
        provenance = (
            f"runtime-canary:codex:{executable.path}:"
            f"{hashlib.sha256(Path(executable.path).read_bytes()).hexdigest()}"
        )
        profile = make_profile(
            project_id=project.project_id,
            provider_id="provider-codex",
            provenance_source=provenance,
            observed_at=now,
            expires_at=expires,
            capability_results=capabilities,
            profile_version=profile_version,
        )
        candidate = CapabilityCandidate(profile, priority=0)
        probe_hash = hashlib.sha256((probe.final_message or "").encode()).hexdigest()
        gates = SelectionGates(
            policy_eligible=policy.policy_id == "merge-policy-adr-0003"
            and policy.policy_hash == snapshot.get("active_policy_hash"),
            privacy_eligible=executable.path.startswith("/Applications/"),
            independence_eligible=isinstance(adapter, CodexAdapter),
            budget_eligible=adapter.timeout <= 90.0,
            health_eligible=probe.invocation_verified,
            empirical_evidence_eligible=probe_ok,
        )
        gate_evidence = (
            ("policy_eligible", f"active-policy:{policy.policy_id}:{policy.policy_hash}"),
            (
                "privacy_eligible",
                f"codex-safe-environment-v1;read-only-canary;{gates.privacy_eligible}",
            ),
            (
                "independence_eligible",
                f"architect-advisory;reviewer-separate-stage;{gates.independence_eligible}",
            ),
            (
                "budget_eligible",
                f"bounded-timeout-seconds:{adapter.timeout:g};{gates.budget_eligible}",
            ),
            ("health_eligible", f"invocation-verified:{probe.invocation_verified}"),
            ("empirical_evidence_eligible", f"deterministic-canary-sha256:{probe_hash}"),
        )
        state = build_state(
            project_id=project.project_id,
            target_sha=target_sha,
            constitution_id=constitution.constitution_id,
            constitution_record_hash=constitution.record_hash,
            observed_at=now,
            expires_at=expires,
            candidates=(candidate,),
            provider_interfaces=(("provider-codex", "codex"),),
            gates=gates,
            gate_evidence=gate_evidence,
            policy_generation=authority_generation,
        )
        if not args.candidate_output:
            raise ProviderIntelligenceError(
                "provider intelligence bootstrap requires --candidate-output "
                "and the external owner controller"
            )
        candidate_output = Path(args.candidate_output).expanduser().resolve()
        if store.root.resolve() not in candidate_output.parents:
            raise ProviderIntelligenceError(
                "provider intelligence candidate must stay under AGF state root"
            )
        candidate_output.parent.mkdir(parents=True, exist_ok=True)
        candidate_output.write_text(
            json.dumps(state.to_dict(), sort_keys=True) + "\n", encoding="utf-8"
        )
        _output(
            {
                "project_id": state.project_id,
                "target_sha": state.target_sha,
                "profile_id": profile.profile_id,
                "profile_sha256": profile.profile_sha256,
                "probe_pass": probe_ok,
                "gates": state.to_dict()["gates"],
                "requirements_hash": state.requirements_hash,
                "state_sha256": state.state_sha256,
                "candidate_output": str(candidate_output),
            },
            args.json,
        )
        return 0
    except (
        ProviderIntelligenceError,
        ProjectRegistryError,
        PolicyActivationError,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def _capability_probe_passed(capabilities: dict[str, CapabilityStatus]) -> bool:
    """Return whether every observed Architect capability is supported."""
    return all(status is CapabilityStatus.SUPPORTED for status in capabilities.values())


def _authority_generation(resolved_authority, snapshot: dict | None) -> int | None:
    if resolved_authority.context is not None:
        return resolved_authority.context.generation_number
    return snapshot.get("generation") if isinstance(snapshot, dict) else None


def _git_output(root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            check=True,
            shell=False,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProviderIntelligenceError(
            "provider intelligence repository inspection failed"
        ) from exc
    return result.stdout.strip()


def _validate_provider_intelligence_runtime(state, project, target_sha, snapshot, now):
    state.validate(now=now, target_sha=target_sha)
    from .authority_context import resolve_authority

    resolved_authority = resolve_authority(project.project_id)
    expected_generation = _authority_generation(resolved_authority, snapshot)
    if state.policy_generation != expected_generation:
        raise ProviderIntelligenceError("provider intelligence policy generation is stale")
    active_policy = resolved_authority.policy
    active_constitution = resolved_authority.constitution
    if active_policy is None:
        raise ProviderIntelligenceError("active policy is unavailable")
    if (
        state.constitution_id != active_constitution.constitution_id
        or state.constitution_record_hash != active_constitution.record_hash
    ):
        raise ProviderIntelligenceError("provider intelligence Constitution binding is stale")
    if (
        snapshot.get("active_policy_id") != active_policy.policy_id
        or snapshot.get("active_policy_hash") != active_policy.policy_hash
        or active_policy.project_id != project.project_id
    ):
        raise ProviderIntelligenceError("provider intelligence policy binding is stale")
    evidence = dict(state.gate_evidence)
    if evidence.get("policy_eligible") != (
        f"active-policy:{active_policy.policy_id}:{active_policy.policy_hash}"
    ):
        raise ProviderIntelligenceError("provider intelligence policy evidence is stale")
    if evidence.get("privacy_eligible") != "codex-safe-environment-v1;read-only-canary;True":
        raise ProviderIntelligenceError("provider intelligence privacy evidence is invalid")
    if evidence.get("independence_eligible") != "architect-advisory;reviewer-separate-stage;True":
        raise ProviderIntelligenceError("provider intelligence independence evidence is invalid")
    if evidence.get("budget_eligible") != "bounded-timeout-seconds:90;True":
        raise ProviderIntelligenceError("provider intelligence budget evidence is invalid")
    health = evidence.get("health_eligible")
    if health != f"invocation-verified:{state.gates.health_eligible}":
        raise ProviderIntelligenceError("provider intelligence health evidence is invalid")
    empirical = evidence.get("empirical_evidence_eligible", "")
    if not empirical.startswith("deterministic-canary-sha256:"):
        raise ProviderIntelligenceError("provider intelligence empirical evidence is invalid")
    canary_hash = empirical.removeprefix("deterministic-canary-sha256:")
    if len(canary_hash) != 64 or any(char not in "0123456789abcdef" for char in canary_hash):
        raise ProviderIntelligenceError("provider intelligence canary evidence is invalid")
    try:
        expected_empirical = all(
            candidate.profile.require_supported(capability)
            for candidate in state.candidates
            for capability in ARCHITECT_REQUIREMENTS
        )
    except (CapabilityProfileError, ValueError):
        expected_empirical = False
    if state.gates.empirical_evidence_eligible != expected_empirical:
        raise ProviderIntelligenceError("provider intelligence empirical gate is inconsistent")
    for provider_id, interface in state.provider_interfaces:
        if interface != "codex":
            continue
        profile = next(
            item.profile for item in state.candidates if item.profile.provider_id == provider_id
        )
        current = resolve_codex_executable()
        if current.path is None:
            raise ProviderIntelligenceError("Architect provider interface is unavailable")
        current_provenance = (
            f"runtime-canary:codex:{current.path}:"
            f"{hashlib.sha256(Path(current.path).read_bytes()).hexdigest()}"
        )
        if profile.provenance_source != current_provenance:
            raise ProviderIntelligenceError("provider profile is stale after provider change")


def run_policy(args: argparse.Namespace) -> int:
    try:
        project, _ = _resolve_project(argparse.Namespace(project=args.project))
        active = resolve_authority(project.project_id).policy
        if active is None:
            raise PolicyActivationError("active policy is unavailable")
        _output(
            {
                "project_id": active.project_id,
                "policy_id": active.policy_id,
                "version": active.version,
                "policy_hash": active.policy_hash,
                "activation_hash": active.activation_hash,
                "rollback_target": active.rollback_target,
                "key_id": active.key_id,
                "human_merge": {
                    risk.value: active.requires_human_merge(risk) for risk in EffectiveRisk
                },
            },
            args.json,
        )
        return 0
    except (PolicyActivationError, ProjectRegistryError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def _write_plan(plan, output: str, repository_root: str) -> None:
    target = Path(output).expanduser().resolve()
    root = Path(repository_root).resolve()
    if target == root or root in target.parents:
        raise ValueError("output must not be written inside the target repository")
    serialized = json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, target)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def run_plan(args: argparse.Namespace) -> int:
    try:
        project, target = _resolve_project(args)
        _dirty_policy(project, target, args.allow_dirty)
        repository = collect_repository(target, allow_dirty=True)
        repository = replace(
            repository,
            branch=project.default_branch,
            origin=project.origin_url,
            head_sha=project.current_head_sha,
        )
        plan = Director().create_plan(args.goal, repository)
        _write_plan(plan, args.output, repository.root)
    except (ProjectRegistryError, PreflightError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if plan.status is PlanStatus.HUMAN_REQUIRED:
        print("HUMAN_REQUIRED: plan written; clarify the goal before execution", file=sys.stderr)
        return 3
    print(f"plan written: {args.output}")
    return 0


def run_execute(args: argparse.Namespace) -> int:
    if args.allow_openhands_llm_env and args.adapter != "openhands":
        print("ERROR: --allow-openhands-llm-env requires --adapter openhands", file=sys.stderr)
        return 2
    if args.execute != args.confirm_execution:
        print("ERROR: --execute and --confirm-execution must be supplied together", file=sys.stderr)
        return 2
    if args.execute and args.dry_run:
        print("ERROR: --dry-run cannot be combined with live execution", file=sys.stderr)
        return 2
    try:
        plan = load_plan(args.plan)
        project, target_root = _resolve_project(args)
        _validate_plan_project(plan, project, target_root)
        if args.execute and not project.policy.allow_live_execution:
            raise ProjectRegistryError("project policy denies live execution")
        if args.execute:
            _verify_constitution(project)
        if args.output:
            output = Path(args.output).expanduser().resolve()
            if output == target_root or target_root in output.parents:
                raise ExecutionValidationError(
                    "execution report must not be written inside the target repository"
                )
        adapter = (
            OllamaOpenHandsAdapter(
                executable=args.openhands_path,
                timeout=args.timeout,
                allow_llm_env=True,
            )
            if args.adapter == "ollama"
            else OpenHandsSDKAdapter(
                executable=args.openhands_path,
                timeout=args.timeout,
                allow_llm_env=args.allow_openhands_llm_env,
            )
            if args.adapter == "openhands"
            else CodexAdapter(executable=args.codex_path, timeout=args.timeout)
        )
        result = Executor(adapter=adapter).execute(
            plan, args.task, str(target_root), dry_run=not args.execute
        )
        if args.output:
            write_execution_result(result, args.output)
        else:
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    except (
        ProjectRegistryError,
        ExecutionValidationError,
        PolicyActivationError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if result.status in {
        ExecutionStatus.BLOCKED,
        ExecutionStatus.FAILED,
        ExecutionStatus.HUMAN_REQUIRED,
    }:
        return 2
    return 0


def run_deliver(args: argparse.Namespace) -> int:
    if args.allow_openhands_llm_env and args.adapter != "openhands":
        print("ERROR: --allow-openhands-llm-env requires --adapter openhands", file=sys.stderr)
        return 2
    live_flags = (args.execute, args.confirm_execution, args.confirm_delivery)
    if any(live_flags) and not all(live_flags):
        print(
            "ERROR: --execute, --confirm-execution, and --confirm-delivery "
            "must be supplied together",
            file=sys.stderr,
        )
        return 2
    try:
        plan = load_plan(args.plan)
        project, target_root = _resolve_project(args)
        _validate_plan_project(plan, project, target_root)
        if args.execute:
            if not project.policy.allow_live_execution or not project.policy.allow_delivery:
                raise ProjectRegistryError("project policy denies live delivery")
            _verify_constitution(project)
            if (Path.home() / ".agf-orchestrator" / "policy-state.sqlite3").exists():
                active_policy = resolve_authority(project.project_id).policy
            else:
                active_policy = None
            if active_policy is None and not project.policy.require_human_merge:
                raise ProjectRegistryError("delivery requires human merge approval")
            task = next((item for item in plan.tasks if item.task_id == args.task), None)
            if task is None:
                raise ExecutionValidationError(f"task does not exist: {args.task}")
            if active_policy is not None and active_policy.requires_human_merge(task.risk_level):
                raise ProjectRegistryError(
                    f"active policy requires human merge for risk {task.risk_level}"
                )
        output = Path(args.output).expanduser().resolve()
        if output == target_root or target_root in output.parents:
            raise ExecutionValidationError(
                "delivery report must not be written inside the target repository"
            )
        adapter = (
            OllamaOpenHandsAdapter(
                executable=args.openhands_path,
                timeout=args.timeout,
                allow_llm_env=True,
            )
            if args.adapter == "ollama"
            else OpenHandsSDKAdapter(
                executable=args.openhands_path,
                timeout=args.timeout,
                allow_llm_env=args.allow_openhands_llm_env,
            )
            if args.adapter == "openhands"
            else CodexAdapter(executable=args.codex_path, timeout=args.timeout)
        )
        reviewer = (
            CodexReviewerAdapter(CodexAdapter(executable=args.codex_path, timeout=args.timeout))
            if args.reviewer == "codex"
            else DeterministicReviewer()
        )
        pipeline = DeliveryPipeline(
            adapter=adapter,
            reviewer=reviewer,
            compliance=ComplianceChecker(),
            pr_creator=DraftPRCreator(simulate=args.simulate_pr),
            max_correction_rounds=project.policy.maximum_correction_rounds,
        )
        report = pipeline.deliver(
            plan,
            args.task,
            str(target_root),
            execute=args.execute,
            project_id=project.project_id,
        )
        write_delivery_report(report, output)
    except (
        ProjectRegistryError,
        ExecutionValidationError,
        PolicyActivationError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if report.status in {"BLOCKED", "FAILED", "HUMAN_REQUIRED"}:
        return 2
    print(f"delivery report written: {args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_cli_environment()
    args = build_parser().parse_args(argv)
    if args.command == "plan":
        return run_plan(args)
    if args.command == "execute":
        return run_execute(args)
    if args.command == "deliver":
        return run_deliver(args)
    if args.command == "project":
        return run_project(args)
    if args.command == "session":
        return run_session(args)
    if args.command == "inbox":
        return run_inbox(args)
    if args.command == "provider-intelligence":
        return run_provider_intelligence(args)
    if args.command == "policy":
        return run_policy(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
