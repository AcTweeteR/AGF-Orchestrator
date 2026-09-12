"""Built-in session campaign driver; generic command drivers remain unchanged."""

import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from .authority_context import resolve_authority
from .campaign_runner import (
    CampaignStatus,
    PersistentCampaignRunner,
    StepResult,
    WaitRequest,
    timestamp,
    utc_now,
)
from .delivery_reconciliation import DeliveryIntentStore
from .models import plan_from_dict
from .project_registry import ProjectRegistry, _git
from .remote_identity import canonical_remote_identity
from .session_continuation import SessionContinuation
from .session_manager import SessionManager
from .session_models import SessionStatus
from .session_store import SessionStore
from .task_dependencies import _hash, verify_plan_lineage


class GovernedCampaignError(ValueError):
    pass


@dataclass(frozen=True)
class GovernedSessionDriverSpec:
    project_id: str
    campaign_id: str
    state_dir: str
    session_id: str
    poll_seconds: int = 30
    adapter: str = "codex"
    timeout: float = 300.0
    architect_config: str | None = None
    codex_path: str | None = None
    openhands_path: str = "openhands"
    allow_openhands_llm_env: bool = False
    driver_kind: str = "governed-session/1"

    def validate(self):
        for value, prefix in ((self.project_id, "project-"), (self.campaign_id, "campaign-"),
                              (self.session_id, "session-")):
            if (not isinstance(value, str) or not value.startswith(prefix)
                    or "/" in value or "\\" in value):
                raise GovernedCampaignError("governed campaign identity is invalid")
        if self.driver_kind != "governed-session/1":
            raise GovernedCampaignError("unsupported governed driver")
        if type(self.poll_seconds) is not int or not 1 <= self.poll_seconds <= 3600:
            raise GovernedCampaignError("poll interval is outside limits")
        if (isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float))
                or not math.isfinite(self.timeout) or self.timeout <= 0):
            raise GovernedCampaignError("timeout must be finite and positive")
        if self.adapter not in {"codex", "openhands", "ollama"}:
            raise GovernedCampaignError("unknown execution adapter")
        if type(self.allow_openhands_llm_env) is not bool:
            raise GovernedCampaignError("environment forwarding setting is invalid")
        if self.allow_openhands_llm_env and self.adapter != "openhands":
            raise GovernedCampaignError("environment forwarding requires OpenHands")
        if not isinstance(self.state_dir, str) or not Path(self.state_dir).is_absolute():
            raise GovernedCampaignError("campaign state root must be absolute")
        for value in (self.architect_config, self.codex_path, self.openhands_path):
            if value is not None and (not isinstance(value, str) or not value or len(value) > 4096):
                raise GovernedCampaignError("driver configuration path is invalid")
        if self.architect_config is not None and not Path(self.architect_config).is_absolute():
            raise GovernedCampaignError("architect configuration path must be absolute")

    def to_dict(self):
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, payload):
        try:
            spec = cls(**payload)
        except (TypeError, ValueError) as exc:
            raise GovernedCampaignError("invalid governed driver specification") from exc
        spec.validate()
        return spec


class GovernedSessionDriver:
    def __init__(self, spec, *, manager_factory=SessionManager, pipeline_factory=None,
                 architect_factory=None, now=utc_now):
        spec.validate()
        self.spec = spec
        self.manager_factory = manager_factory
        self.pipeline_factory = pipeline_factory
        self.architect_factory = architect_factory
        self.now = now

    def snapshot(self, state):
        """Read current authority and prove the campaign's retained canonical origin."""
        spec = self.spec
        store = SessionStore(spec.state_dir)
        if (state.project_id != spec.project_id or state.session_id != spec.session_id
                or state.campaign_id != spec.campaign_id):
            raise GovernedCampaignError("campaign specification binding differs")
        project = ProjectRegistry(store.state_dir).get(state.project_id)
        session = store.load(state.session_id)
        if project.status.value != "ACTIVE" or session.project_id != project.project_id:
            raise GovernedCampaignError("campaign project is not active")
        runtime = resolve_authority(project.project_id)
        authority = runtime.context
        if (authority is None or authority.policy_hash != state.policy_binding
                or authority.generation_number != state.authority_generation
                or runtime.snapshot is None or runtime.snapshot["kill_switch_active"]):
            raise GovernedCampaignError("campaign authority or stop condition changed")
        root = Path(project.repository_root)
        head = _git(root, "rev-parse", "HEAD")
        if (_git(root, "status", "--porcelain")
                or _git(root, "branch", "--show-current") != project.default_branch
                or canonical_remote_identity(_git(root, "config", "--get", "remote.origin.url"))
                != canonical_remote_identity(project.origin_url)):
            raise GovernedCampaignError("campaign repository identity or cleanliness changed")
        path = store.ensure_safe_path(session.plan_path)
        expected_hash = session.artifact_hashes["plan"]
        plan = None
        seen = set()
        for _ in range(200):
            if (path.parent != store.artifacts_dir / session.session_id or path in seen
                    or store.artifact_hash(str(path)) != expected_hash):
                raise GovernedCampaignError("campaign plan lineage is inconsistent")
            seen.add(path)
            payload = json.loads(path.read_text())
            parsed = plan_from_dict(payload)
            parsed.validate()
            if plan is None:
                plan = parsed
            if expected_hash == state.lineage_binding:
                if parsed.repository.head_sha != state.target_sha:
                    raise GovernedCampaignError("campaign origin target differs")
                break
            previous = payload.get("scope", {}).get("lineage")
            if not previous:
                raise GovernedCampaignError("campaign origin is absent from canonical lineage")
            expected_hash = payload["scope"]["predecessor_plan_sha256"]
            path = store.ensure_safe_path(previous)
        else:
            raise GovernedCampaignError("campaign lineage exceeds limit")
        if plan.repository.head_sha != session.base_sha:
            raise GovernedCampaignError("session target differs from its plan")
        if session.base_sha != state.target_sha:
            # Includes restart after session reconciliation but before campaign save.
            verify_plan_lineage(session.session_id, plan, str(root), state_dir=store.state_dir,
                                allow_completed=True, policy_hash=authority.policy_hash,
                                constitution_id=authority.artifacts["constitution"]["constitution_id"])
        pending = None
        if head != session.base_sha:
            intents = DeliveryIntentStore(store.state_dir)
            matches = [item for item in intents.for_session(project.project_id, session.session_id)
                       if item.base_sha == session.base_sha
                       and item.plan_hash == _hash(plan.to_dict())]
            if len(matches) != 1:
                raise GovernedCampaignError("target advancement has no unique canonical intent")
            pending = matches[0]
            task = next((item for item in plan.tasks if item.task_id == pending.task_id), None)
            if task is None or pending.task_hash != _hash(asdict(task)):
                raise GovernedCampaignError("target advancement task binding differs")
            intents.verify_observation(project.project_id, pending.delivery_id, root)
        return project, session, plan, pending

    def _verify_budget_binding(self, state, session):
        from .objective_acceptance import content_hash

        store = SessionStore(self.spec.state_dir)
        path = store.ensure_safe_path(
            store.artifacts_dir / session.session_id / "campaign-binding.json",
        )
        if store.artifact_hash(str(path)) != session.artifact_hashes.get("campaign_binding"):
            raise GovernedCampaignError("campaign budget binding changed")
        expected = {"campaign_id": state.campaign_id, "session_id": session.session_id,
                    "project_id": state.project_id, "operation_id": state.operation_id,
                    "retry_budget": state.retry_budget,
                    "driver_sha256": content_hash(self.spec.to_dict())}
        if json.loads(path.read_text()) != expected:
            raise GovernedCampaignError("campaign driver or budget binding differs")

    def probe(self, state):
        _, session, _, pending = self.snapshot(state)
        self._verify_budget_binding(state, session)
        # Polling performs only repository/evidence reads, never assessment.
        return (pending is not None or session.base_sha != state.target_sha
                or (state.status is CampaignStatus.RETRY_BACKOFF
                    and state.resource == "campaign-external-boundary"))

    def work(self, state):
        project, session, _, _ = self.snapshot(state)
        self._verify_budget_binding(state, session)
        if SessionStore().state_dir != Path(self.spec.state_dir).resolve():
            raise GovernedCampaignError("daemon state differs from configured session state")
        args = SimpleNamespace(**asdict(self.spec), session=session.session_id,
                               session_command="continue", reviewer="codex", simulate_pr=False)
        if self.pipeline_factory is None:
            from .cli import _delivery_pipeline
            pipeline = _delivery_pipeline(args, project)
        else:
            pipeline = self.pipeline_factory(project)
        if self.architect_factory is None:
            from .cli import _architect_from_config

            def architect():
                return _architect_from_config(args)
        else:
            architect = self.architect_factory
        manager = self.manager_factory()
        result = SessionContinuation(manager, pipeline, architect_factory=architect).tick(
            session.session_id, execute=True, confirm_execution=True, confirm_delivery=True,
        )
        status = result["status"]
        if status == "WAIT":
            return StepResult("WAIT", WaitRequest(
                CampaignStatus.WAITING_GITHUB, result["reason"], result["delivery_id"],
                "exact canonical delivery integrated",
                timestamp(self.now() + timedelta(seconds=self.spec.poll_seconds)),
            ))
        if status == "SUCCESS" and result.get("objective_completed") is True:
            return StepResult("COMPLETE", reason="canonical Objective acceptance verified")
        if status == "NO_JUSTIFIED_WORK":
            return StepResult("NO_JUSTIFIED_WORK",
                              reason="governed assessment; Objective unsatisfied")
        outcome = {"CONTINUE": "CONTINUE", "HUMAN_REQUIRED": "HUMAN_REQUIRED"}.get(
            status, "BLOCKED_NON_RETRYABLE",
        )
        return StepResult(outcome, reason=result.get("reason", status))


class GovernedCampaignRunner(PersistentCampaignRunner):
    def __init__(self, store, driver, **kwargs):
        super().__init__(store, **kwargs)
        self.driver = driver

    def _apply_result(self, state, result):
        _, session, _, pending = self.driver.snapshot(state)
        if pending is not None:
            if result.outcome in {"WAIT", "CONTINUE"}:
                # A concurrent exact integration is work for the next tick.
                # Retain the old binding until SessionManager reconciles it.
                return super()._apply_result(state, result)
            raise GovernedCampaignError("session has not reconciled observed delivery")
        state = replace(state, target_sha=session.base_sha,
                        lineage_binding=session.artifact_hashes["plan"])
        return super()._apply_result(state, result)


def register_governed_campaign(spec, retry_budget):
    """Bind one campaign budget to a canonical session without activating authority."""
    from .campaign_runner import CampaignStore, make_initial_state
    from .locking import project_lock, session_lock
    from .objective_acceptance import content_hash

    spec.validate()
    if type(retry_budget) is not int or not 0 <= retry_budget <= 100:
        raise GovernedCampaignError("retry budget must be an integer between 0 and 100")
    store = SessionStore(spec.state_dir)
    manager = SessionManager(state_dir=store.state_dir)
    with session_lock(store.state_dir, spec.session_id, "campaign-session-binding"):
        with project_lock(store.state_dir, spec.project_id, "campaign-session-binding"):
            session = store.load(spec.session_id)
            project = manager.registry.get(spec.project_id)
            if (session.project_id != spec.project_id or project.status.value != "ACTIVE"
                    or session.status not in {SessionStatus.READY, SessionStatus.RETRY_REQUIRED}):
                raise GovernedCampaignError("campaign requires an active ready session")
            runtime = resolve_authority(project.project_id)
            if runtime.context is None:
                raise GovernedCampaignError("campaign requires installed generation authority")
            operation = "operation-" + content_hash({"session": session.session_id,
                                                    "campaign": spec.campaign_id})[:24]
            campaign_store = CampaignStore(store.state_dir, spec.project_id, spec.campaign_id)
            store.ensure_safe_path(campaign_store.path)
            existing = campaign_store._load_unlocked()
            if existing is None:
                state = make_initial_state(
                    project_id=spec.project_id, campaign_id=spec.campaign_id,
                    session_id=spec.session_id, phase="governed-session",
                    operation_id=operation, target_sha=session.base_sha,
                    lineage_binding=session.artifact_hashes["plan"], retry_budget=retry_budget,
                    policy_binding=runtime.context.policy_hash,
                    authority_generation=runtime.context.generation_number,
                )
            else:
                state = existing
                if (state.session_id != session.session_id or state.operation_id != operation
                        or state.retry_budget != retry_budget):
                    raise GovernedCampaignError("campaign registration cannot reset its budget")
            binding = {"campaign_id": state.campaign_id, "session_id": session.session_id,
                       "project_id": project.project_id, "operation_id": state.operation_id,
                       "retry_budget": state.retry_budget,
                       "driver_sha256": content_hash(spec.to_dict())}
            path = store.ensure_safe_path(store.artifacts_dir / session.session_id
                                           / "campaign-binding.json")
            rollover = False
            if path.exists() and json.loads(path.read_text()) != binding:
                historical_hash = session.artifact_hashes.get("historical:campaign_binding")
                if (
                    session.artifact_hashes.get("campaign_binding") is not None
                    or session.artifact_hashes.get("external_advancement") is None
                    or historical_hash != store.artifact_hash(str(path))
                ):
                    raise GovernedCampaignError(
                        "session already belongs to another campaign budget"
                    )
                rollover = True
            GovernedSessionDriver(spec).snapshot(state)
            if existing is None:
                campaign_store._save_unlocked(state)
            if rollover:
                _, _, archived_hash = store.replace_artifact_for_recovery(
                    session.session_id,
                    "campaign-binding.json",
                    json.dumps(binding, sort_keys=True),
                    f"campaign-binding-before-{state.campaign_id}.json",
                )
                if archived_hash != historical_hash:
                    raise GovernedCampaignError("historical campaign budget binding changed")
            if path.exists() and json.loads(path.read_text()) == binding:
                digest = store.artifact_hash(str(path))
            else:
                _, digest = store.write_artifact(session.session_id, path.name,
                                                  json.dumps(binding, sort_keys=True))
            session.artifact_hashes["campaign_binding"] = digest
            manager._save(session)
            return state
