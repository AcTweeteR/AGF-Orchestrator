# Context Protocol

Context objects are the minimum shared state for governed execution. They are versioned, attributable, task-linked, and immutable after acceptance. A new fact or correction creates a new version and records its source. Missing context blocks action; it is never inferred from conversation, defaults, or prior tasks.

## Project Context

The Project Context defines the governing frame: project identity, objective, repository identity, requesting authority, scope boundary, applicable AGF and project policies, risk tolerance, success conditions, release target, retention requirements, and active architecture decision references.

## Architecture Context

The Architecture Context defines the approved technical frame: architecture decision IDs, selected boundaries, interfaces, constraints, assumptions, rejected alternatives, non-functional requirements, compatibility requirements, known risks, and technical acceptance conditions. An unresolved architectural question makes the context incomplete.

## Task Context

The Task Context defines one unit of work: task ID, parent epic, objective, scope in and out, accountable role, assigned Implementer, dependencies, inputs, expected outputs, acceptance criteria, policy scope, allowed context, stop conditions, time or resource limits, current state, and required evidence. One Implementer may receive one task at a time.

## Review Context

The Review Context defines the evaluation frame: task and artifact references, acceptance criteria, architecture constraints, validation evidence, reviewer identity, independence statement, findings, severity, residual risks, rework disposition, and review decision. It cannot be produced solely by the Implementer.

## Release Context

The Release Context defines delivery authorization: release ID, exact scope, version, changelog evidence, repository cleanliness evidence, Review decision, Compliance Officer decision, Director readiness approval, Release Manager decision, publication conditions, rollback or recovery conditions, and post-release observation plan.

## Context rules

- Every handoff names the exact version of each applicable object.
- A role may consume only objects within its authorized scope.
- Conflicting versions block the transition until the Director resolves routing and the owner issues a corrected version.
- Context edits are decisions when they change scope, authority, risk, architecture, policy, or release conditions; use [DECISION_PROTOCOL.md](DECISION_PROTOCOL.md).
- The [HANDOFF_PROTOCOL.md](HANDOFF_PROTOCOL.md) defines transfer and acceptance; this document defines content, so neither protocol duplicates the other.
