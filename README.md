# AGF-Orchestrator

AGF-Orchestrator is the reference operational model for coordinating autonomous software-development agents under the Agent Governance Framework (AGF).

AGF defines the governing rules. AGF-Orchestrator defines how work, decisions, evidence, reviews, compliance checks, and releases move through an accountable system. The model is vendor neutral: an agent provider is an interchangeable adapter, not a governing authority.

This repository contains the AGF-Orchestrator v0.1 documentation baseline and its first controlled runtime layers. It specifies the system boundary, roles, lifecycle, workflow, decision rights, failure handling, and delivery safeguards.

## Read the model

- [Vision](docs/VISION.md) — purpose, scope, and principles.
- [Architecture](docs/ARCHITECTURE.md) — layers, boundaries, and control flow.
- [Execution model](docs/EXECUTION_MODEL.md) — controlled work execution and evidence.
- [Workflow engine](docs/WORKFLOW_ENGINE.md) — the end-to-end orchestration pipeline.
- [Task model](docs/TASK_MODEL.md) — task states, transitions, and invariants.
- [Decision model](docs/DECISION_MODEL.md) — decision ownership and escalation.
- [Review pipeline](docs/REVIEW_PIPELINE.md) — independent quality gates.
- [Failure model](docs/FAILURE_MODEL.md) — detection, escalation, and recovery.
- [Human intervention](docs/HUMAN_INTERVENTION.md) — mandatory human decision points.
- [Agent roles](docs/AGENT_ROLES.md) — defined responsibilities and authority.
- [Adapter model](docs/ADAPTER_MODEL.md) — provider-neutral agent integration boundary.
- [Roadmap](docs/ROADMAP.md) — capability maturity levels.
- [Architecture decision records](docs/adr/README.md) — durable architectural decisions.

## Status

Version 0.1 is a formal documentation baseline. It is intended to establish a shared vocabulary and reference architecture for future implementations.

## Director Runtime MVP

The first executable layer provides a read-only Director planning command. Install the package with Python 3.12+ and the development tools:

```text
python -m pip install -e .
python -m pip install pytest ruff
```

Generate a deterministic plan for a clean Git repository:

```text
agf-orchestrator plan \
  --repository /path/to/project \
  --goal "High-level objective" \
  --output /path/to/plan.json
```

The command performs repository preflight, never modifies the target repository, and returns `HUMAN_REQUIRED` for ambiguous goals. Use `--allow-dirty` only when the caller explicitly accepts planning against a dirty working tree. The runtime currently uses a deterministic local adapter; no remote model or provider API is called.

Preflight requires a resolvable named branch, an `origin` remote, and a resolvable `HEAD`. Missing repository context is an explicit non-zero error. When `--allow-dirty` is used, the plan preserves `clean: false` and records the dirty-state risk and required evidence.

Execute one approved task with a dry-run by default:

```text
agf-orchestrator execute \
  --plan /path/to/plan.json \
  --task task-001 \
  --repository /path/to/project \
  --adapter codex \
  --dry-run
```

Live execution requires both `--execute` and `--confirm-execution`, plus a clean named non-default branch, approved architecture, allowed paths, acceptance criteria, validations, and no unresolved intervention. The Codex adapter invokes the locally discovered `codex exec` command without shell interpretation, enforces a timeout, verifies changed-file scope, runs only task-declared validations, and never commits or pushes.

## Autonomous delivery pipeline

The delivery workflow defaults to dry-run and requires all three flags for live delivery: `--execute`, `--confirm-execution`, and `--confirm-delivery`. It executes one task in isolation, creates a patch outside the target repository, runs independent review and compliance gates, applies only the approved patch in a fresh delivery worktree, commits and pushes an `agf/<plan-id>/<task-id>` branch, and opens a draft PR. AGF-Orchestrator never merges PRs.

```text
agf-orchestrator deliver \
  --plan /path/to/plan.json \
  --task task-001 \
  --repository /path/to/project \
  --adapter codex \
  --output /path/to/delivery-report.json
```

Dry-run performs no model, Git, branch, commit, push, or PR mutation. For a controlled local test, `--simulate-pr` returns a local draft-PR reference instead of contacting GitHub. Live delivery requires `--execute --confirm-execution --confirm-delivery` together; it never bypasses sandboxing or approvals.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing changes. Governance concerns are recorded as architecture decision records where appropriate.

## Security

See [SECURITY.md](SECURITY.md) for reporting and design expectations.

## License

No license is asserted by this documentation baseline. A project license must be established before redistribution.
