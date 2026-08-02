# Runtime

This document specifies the operational runtime of AGF-Orchestrator. It coordinates work under the frozen architecture and role definitions; it does not execute development or change decision ownership.

## Execution lifecycle

1. **Admission.** A goal enters with a requesting authority, objective, constraints, risk context, repository identity, and success condition. The Director rejects an unidentifiable or ambiguous goal.
2. **Planning.** The Director assigns the Planner to produce epics, tasks, dependencies, acceptance criteria, and a proposed execution order. The Planner cannot authorize its own plan.
3. **Architecture.** The Architect evaluates the approved plan, records technical boundaries and decisions, and emits an implementation contract. An unresolved architectural decision blocks execution.
4. **Dispatch.** The Director marks tasks Ready and assigns exactly one Implementer per task. Parallel execution is allowed only when dependencies, repository ownership, and review independence are recorded.
5. **Execution.** The Implementer works within one task contract, preserves evidence, and reports completion or a stop condition. Agents communicate through the handoff records in [HANDOFF_PROTOCOL.md](HANDOFF_PROTOCOL.md), not through unstated shared assumptions.
6. **Review.** The Reviewer independently evaluates the outcome against the task and architecture. Acceptance advances the package; rework returns it through [RECOVERY_PROTOCOL.md](RECOVERY_PROTOCOL.md).
7. **Compliance.** The Compliance Officer verifies AGF, policy, documentation, and evidence requirements. A failed control blocks release.
8. **Release.** The Director approves release readiness; the Release Manager verifies release gates, cleanliness, changelog, and versioning, then authorizes publication or blocks it.
9. **Completion.** The runtime records the final transition, release outcome, evidence index, decisions, and unresolved observations. A successful release reaches Done; a rejected or permanently blocked outcome reaches its documented terminal disposition and is not treated as Done.

## Context preservation

Every active task carries the mandatory objects in [CONTEXT_PROTOCOL.md](CONTEXT_PROTOCOL.md). A role receives a versioned context snapshot and returns a versioned result. Context is append-only for history: corrections create a new version and retain the prior record. A role must request a handoff correction when a required field is missing; it must not infer the value.

## Communication

Role communication is structured around task identity, state transition, handoff package, decision record, escalation record, or recovery record. The [HANDOFF_PROTOCOL.md](HANDOFF_PROTOCOL.md) defines the required payload between accountable roles. The Observer may report anomalies but cannot authorize a transition.

## Failure propagation

A failure stops the affected transition, preserves the last valid context and evidence, and creates a failure record. The accountable role routes it according to [RECOVERY_PROTOCOL.md](RECOVERY_PROTOCOL.md). Only the role owning the failed gate may re-evaluate that gate; the Director coordinates returns but cannot replace the gate owner. Human intervention occurs only under [ESCALATION_PROTOCOL.md](ESCALATION_PROTOCOL.md).

## Runtime invariants

- No task is dispatched without a complete Task Context and an approved architecture boundary.
- No role acts on a missing, stale, or conflicting mandatory context object.
- Every transition has one accountable sender, one accountable receiver, and a recorded result.
- Every pipeline uses the same handoff, context, quality, and recovery protocols.
- Every pipeline ends in Done or an explicit blocked, rejected, or cancelled terminal disposition.
