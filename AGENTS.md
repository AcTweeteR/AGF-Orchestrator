# Guidance for agents working on AGF-Orchestrator

This repository is documentation-only. Do not add code, scripts, CI, provider SDKs, APIs, or automation as part of documentation changes.

## Governing references

AGF is the source of governance rules. This repository is the operational orchestration reference. When a proposal changes authority, policy interpretation, or a control boundary, consult [DECISION_MODEL.md](docs/DECISION_MODEL.md) and record the decision in [docs/adr/](docs/adr/README.md) when it is architectural.

## Editorial invariants

- Use the defined role names exactly: Director, Planner, Architect, Implementer, Reviewer, Compliance Officer, Release Manager, and Observer.
- Use the task lifecycle in [TASK_MODEL.md](docs/TASK_MODEL.md) exactly.
- Keep provider names and provider-specific mechanisms out of normative text.
- Describe evidence and outcomes, not hypothetical implementation details.
- Preserve the distinction between authority, responsibility, and observation.

Before handoff, validate links, terminology, cross-references, and `git diff --check`.
