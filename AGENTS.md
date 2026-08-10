# Guidance for agents working on AGF-Orchestrator

This repository contains both normative governance documentation and the
implementation of the AGF orchestrator/runtime and approved external,
owner-controlled tooling. Code changes are allowed only when they implement
an approved roadmap item, ADR, or architecture decision; remain within the
authorized task scope; preserve constitutional and policy boundaries; and
pass the required tests, independent review, and Compliance gates.

Agents must not modify the Constitution or root of trust without explicit
authorization, create new authority sources, self-activate protected policy,
bypass owner-controlled external actions, weaken fail-closed behavior, expose
secrets, or add unrelated production functionality. External owner-controlled
controllers may be implemented here when privileged mutation operations are
unavailable to the AGF runtime, the owner/runtime separation is explicit and
tested, and no secret material is committed. CRITICAL changes remain
HUMAN_REQUIRED under the active policy.

## Governing references

AGF is the source of governance rules. This repository is the operational orchestration reference. When a proposal changes authority, policy interpretation, or a control boundary, consult [DECISION_MODEL.md](docs/DECISION_MODEL.md) and record the decision in [docs/adr/](docs/adr/README.md) when it is architectural.

## Editorial invariants

- Use the defined role names exactly: Director, Planner, Architect, Implementer, Reviewer, Compliance Officer, Release Manager, and Observer.
- Use the task lifecycle in [TASK_MODEL.md](docs/TASK_MODEL.md) exactly.
- Keep provider names and provider-specific mechanisms out of normative text.
- Describe evidence and outcomes, not hypothetical implementation details.
- Preserve the distinction between authority, responsibility, and observation.

Before handoff, validate links, terminology, cross-references, and `git diff --check`.
