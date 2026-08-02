# AGF-Orchestrator

AGF-Orchestrator is the reference operational model for coordinating autonomous software-development agents under the Agent Governance Framework (AGF).

AGF defines the governing rules. AGF-Orchestrator defines how work, decisions, evidence, reviews, compliance checks, and releases move through an accountable system. The model is vendor neutral: an agent provider is an interchangeable adapter, not a governing authority.

This repository is the documentation release for AGF-Orchestrator v0.1. It specifies the system boundary, roles, lifecycle, workflow, decision rights, failure handling, and maturity path. It does not contain an implementation.

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

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing changes. Governance concerns are recorded as architecture decision records where appropriate.

## Security

See [SECURITY.md](SECURITY.md) for reporting and design expectations.

## License

No license is asserted by this documentation baseline. A project license must be established before redistribution.
