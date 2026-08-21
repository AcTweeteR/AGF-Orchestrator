# AGF-Orchestrator

**Governance for autonomous software-development agents.**

AGF-Orchestrator is a provider-neutral runtime and reference architecture for coordinating autonomous software-development agents under explicit authority, policy, evidence, review, compliance, recovery, and human-control boundaries.

Coding agents can plan and change software. AGF-Orchestrator focuses on the harder operational question: **how should those agents be governed when they are allowed to work autonomously on real repositories?**

The project treats model/provider integrations as interchangeable execution adapters rather than governing authorities. Authority remains explicit, bounded, auditable, and fail-closed.

> **Project status:** active, experimental, pre-1.0 software. The runtime is real and tested, but interfaces and persistence formats may still evolve. Do not grant it production authority that you have not explicitly reviewed and bounded.

## Why AGF-Orchestrator exists

Long-running agent workflows introduce risks that ordinary coding assistants do not have to solve: stale state, replay, provider drift, incomplete evidence, unauthorized external actions, ambiguous ownership, unsafe retries, and results that exist externally without having been authorized by the orchestrator.

AGF-Orchestrator is designed around those failure modes. Its core principles are:

- **Authority is separate from execution.** A provider can perform work but cannot grant itself permission.
- **Evidence is first-class.** Plans, reviews, compliance results, delivery intents, receipts, lineage, and state transitions are recorded and verified.
- **Fail closed.** Missing, stale, ambiguous, replayed, or mismatched authority/evidence blocks advancement.
- **Independent gates.** Review and Compliance are separate from implementation.
- **External mutations are governed.** Pushes, PR creation, merges, and other privileged actions cross explicit policy boundaries.
- **Results are not retroactive authorization.** AGF can reconcile an observed external result without pretending the action was previously authorized.
- **Recovery preserves provenance.** Restart, retry, reconciliation, and target advancement must not fabricate history.
- **Provider neutrality.** Codex, local models, or future providers are adapters; AGF remains the governor.

## What is implemented today

The current runtime includes executable layers for:

- deterministic planning and repository preflight;
- bounded task execution with allowed-path and validation controls;
- provider adapter execution without shell interpretation;
- independent review and Compliance gates;
- controlled delivery through isolated worktrees, commits, pushes, and draft PRs;
- persistent project/session state and immutable transition evidence;
- signed owner-authorized scope and external-advance records;
- delivery intent/receipt verification and lineage reconciliation;
- stale-target, replay, tamper, ambiguity, and drift detection;
- durable campaign waits, retries, wake conditions, and terminal states;
- an independent persistent campaign daemon with single-instance/heartbeat behavior;
- policy enforcement before external campaign actions;
- reconciliation of already-observed external results without false provenance.

The project has an extensive automated regression suite covering governance, recovery, delivery, policy, and adversarial edge cases.

## Quick start

### Requirements

- Python 3.12+
- Git
- Optional provider tooling such as the Codex CLI when using that adapter

Install from a checkout:

```bash
python -m pip install -e .
python -m pip install pytest ruff
```

Run the test suite:

```bash
pytest
ruff check .
```

Generate a deterministic read-only plan for a clean Git repository:

```bash
agf-orchestrator plan \
  --repository /path/to/project \
  --goal "High-level objective" \
  --output /path/to/plan.json
```

Execute one approved task in dry-run mode:

```bash
agf-orchestrator execute \
  --plan /path/to/plan.json \
  --task task-001 \
  --repository /path/to/project \
  --adapter codex \
  --dry-run
```

Dry-run is the default posture. Live execution requires explicit execution confirmation plus the applicable project policy and evidence gates.

## Controlled delivery

The delivery workflow executes one bounded task, creates a patch outside the target repository, runs independent review and Compliance, applies only the approved patch in a fresh delivery worktree, and can create a governed branch and draft PR.

```bash
agf-orchestrator deliver \
  --plan /path/to/plan.json \
  --task task-001 \
  --repository /path/to/project \
  --adapter codex \
  --output /path/to/delivery-report.json
```

Live delivery requires explicit execution and delivery confirmation. Merge authority is a separate governed concern and is never implied by successful implementation or review.

## Persistent projects and sessions

Persistent workflows use explicit project registration and state stored outside managed repositories (by default under `~/.agf-orchestrator`, configurable with `AGF_STATE_DIR`).

```bash
agf-orchestrator project add --name my-project --repository /path/to/project
agf-orchestrator project list --json
agf-orchestrator project verify --project my-project --json
agf-orchestrator session start --project my-project --goal "Bounded objective"
agf-orchestrator session resume --project my-project --session SESSION_ID
agf-orchestrator inbox --json
```

Sessions preserve transition history and evidence bindings, detect target or artifact drift, and surface human-attention states instead of silently advancing through ambiguity.

## Architecture and governance

Start with these documents:

- [Vision](docs/VISION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Execution model](docs/EXECUTION_MODEL.md)
- [Workflow engine](docs/WORKFLOW_ENGINE.md)
- [Task model](docs/TASK_MODEL.md)
- [Decision model](docs/DECISION_MODEL.md)
- [Review pipeline](docs/REVIEW_PIPELINE.md)
- [Failure model](docs/FAILURE_MODEL.md)
- [Human intervention](docs/HUMAN_INTERVENTION.md)
- [Agent roles](docs/AGENT_ROLES.md)
- [Adapter model](docs/ADAPTER_MODEL.md)
- [AGF Constitution](docs/CONSTITUTION.md)
- [Autonomous Director foundation](docs/AUTONOMOUS_DIRECTOR.md)
- [Persistent Campaign Runner](docs/PERSISTENT_CAMPAIGN_RUNNER.md)
- [Roadmap](docs/ROADMAP.md)
- [Autonomous roadmap](docs/AUTONOMOUS_ROADMAP.md)
- [Threat model](docs/THREAT_MODEL.md)
- [Architecture decision records](docs/adr/README.md)

## Maturity model

The canonical roadmap uses levels 0 through 5:

- **Level 0 — Documentation:** shared model, roles, lifecycle, and decisions are defined.
- **Level 1 — Manual orchestration:** humans coordinate documented stages and record evidence.
- **Level 2 — Semi-autonomous orchestration:** bounded roles perform repeatable work with human gate control.
- **Level 3 — Fully orchestrated workflows:** transitions and evidence are coordinated end to end.
- **Level 4 — Policy-driven execution:** applicable policy determines routing, controls, and gates consistently.
- **Level 5 — Self-improving orchestration:** measured outcomes improve planning and controls without weakening governance.

Higher autonomy never grants an agent authority to redefine AGF or expand its own privileges.

## Contributing

External contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md), [GOVERNANCE.md](GOVERNANCE.md), and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before opening a pull request. Changes that alter authority, policy interpretation, trust boundaries, or lifecycle invariants require explicit architectural treatment and may require an ADR.

## Security

Read [SECURITY.md](SECURITY.md) before reporting a vulnerability. Please do not publish exploit details for governance-boundary, signature, provenance, or external-action bypasses before maintainers have had an opportunity to assess them.

## License

Licensed under the [Apache License 2.0](LICENSE).
