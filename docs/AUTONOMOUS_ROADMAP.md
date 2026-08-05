# Autonomous Technical Director Roadmap

Status: Constitution Foundation v1 approved documentation; no
implementation epic is production-ready until its gates and transfer
evidence pass.

## Sequencing rules

- Preserve the existing controlled pipelines and schemas.
- Deliver one bounded epic per change set.
- Every task references objective requirements and has deterministic
  acceptance criteria.
- No epic may activate constitutional, policy, credential, or merge-rule
  changes autonomously.
- A blocked epic checkpoints its exact state and does not claim completion.
- The active constitution is the sole normative source for authority,
  activation, risk, merge, recovery, budget, and completion behavior.

## Dependency graph

```text
E0 Foundation
 |
 +--> E1 Objective and traceability
 |       |
 |       +--> E2 Roadmap and backlog
 |               |
 |               +--> E3 Engineering memory
 |                       |
 |                       +--> E4 Scheduler and resumable loop
 |                               |
 |                               +--> E5 Risk engine
 |                                       |
 |                                       +--> E6 Merge policy and inbox
 |                                               |
 |                                               +--> E7 Completion and self-audit
 |                                                       |
 |                                                       +--> E8 Staged self-hosting
 |                                                               |
 |                                                               +--> E9 Dynamic capability discovery and provider intelligence
 |                                                                       |
 |                                                                       +--> E10 Self-audit and controlled learning
 |                                                                               |
 |                                                                               +--> E11 End-to-end autonomous project pilots
```

Cross-cutting gates apply to every epic: security, compatibility,
observability, pilot, rollback, and documentation review.

## Ordered epics

### E0 — Constitutional architecture and bootstrap foundation

Scope: constitution, operating model, state boundaries, schemas,
roadmap, risk/merge design, migration, threat model, pilots, and complete
Definition of Done.

Acceptance: foundation documents are internally consistent, linked to the
existing runtime, contain no provider authority, define fail-closed gates,
define the root of trust, authority graph, recovery protocol, convergence
limits, and completion authority, and pass link/terminology/diff checks.
Human review is required before E1.

### E1 — Immutable objective engine

Scope: parse, normalize, hash, approve, version, amend-propose,
contradiction/ambiguity detection, and requirement traceability.

Acceptance: equivalent inputs normalize deterministically; approved hashes
are immutable; contradictions block; amendments are proposals only; every
existing plan can carry requirement references; secrets and transcripts are
excluded. Test, disposable, failure, restart, idempotency, isolation, and
security gates must pass.

### E2 — Roadmap and backlog engine

Scope: milestones, epics, tasks, dependencies, critical path, sizing,
priorities, replanning, versioning, supersession, and completion evidence.

Acceptance: IDs are deterministic; cycles and missing references reject;
supersession is explicit; no silent deletion or false completion occurs;
roadmap output is compatible with existing plan loading.

### E3 — Engineering memory

Scope: project-isolated bounded entries, ADR/RFC references, search,
attribution, hashes, evidence links, supersession, and secret controls.

Acceptance: memory is consulted and evidenced by planning/review; complete
transcripts cannot enter memory; concurrent writes are locked and atomic;
restart and cross-project isolation are proven.

### E4 — Autonomous scheduler and resumable loop

Scope: `start`, `run`, `pause`, `resume`, `cancel`, `status`, `next`,
`inbox`, and `audit`; deterministic selection; leases; locks; budgets;
deadlock; interruption recovery; idempotent operations.

Acceptance: the scheduler chooses only eligible tasks, never bypasses
existing gates, resumes from checkpoints without model memory, stops on
uncertainty, enforces finite progress and resource limits, and emits bounded
status and inbox events.

### E5 — Evidence-based risk engine

Scope: deterministic LOW/MEDIUM/HIGH/CRITICAL signals, reviewer evidence,
conservative unknown handling, rollback difficulty, incident history, and
risk evidence.

Acceptance: model opinion cannot lower deterministic risk; protected paths
are high/critical; classifications are reproducible; known fixtures cover
all risk signals and false-safe cases.

### E6 — Merge policy and Director inbox

Scope: gate aggregation, low-risk merge authorization, medium-risk
summaries, high/critical decisions, kill switch, remote uncertainty, and
bounded executive summaries.

Acceptance: all mandatory gates are enforced; forbidden classes never
auto-merge; uncertain remote state escalates; human decisions are explicit,
audited, and resumable; bootstrap PR workflow remains compatible.

### E7 — Global completion and self-audit

Scope: requirement-by-requirement completion, milestone reconciliation,
security/documentation/dependency/test audits, technical debt, and final
completion report.

Acceptance: completion requires every mandatory criterion and evidence;
deferred work is authorized; open high/critical blockers prevent complete;
audits create tasks without changing objective/constitution.

### E8 — Staged self-hosting and transfer

Scope: candidate versions, activation, rollback, directing-version
recording, kill switch, and transfer from provisional Director to AGF.

Acceptance: AGF never mutates itself in place; candidate validation and
atomic/reversible activation pass; a known-good version remains available;
constitutional/policy changes cannot self-activate; controlled pilots
demonstrate autonomous continuation.

### E9 — Dynamic capability discovery and provider intelligence

Scope: local-first discovery of approved interfaces, model enumeration,
safe capability probes, versioned capability registry, empirical evidence,
explainable selection, safe fallback, freshness, invalidation, and
project isolation.

Discovery is bounded, non-destructive, secret-safe, and network-free unless
the active policy explicitly permits network probing. Provider or model
names are metadata only; selection is based on verified capability,
policy eligibility, privacy, health, context/tool support, budget, and
bounded empirical evidence. Unknown values remain UNKNOWN and cannot be
treated as verified capability.

Acceptance: newly available compatible local capabilities can be detected
without role-specific manual mapping; unavailable or stale capabilities
become ineligible; no automatic fallback violates capability, privacy,
cost, or independence requirements; no arbitrary network scanning or
credential disclosure occurs; provider upgrades invalidate stale profiles;
cross-project registries and evidence remain isolated; deterministic,
failure, restart/resume, idempotency, and security pilots pass.

Dependencies: E1 objective and traceability, E2 roadmap/backlog, E3
engineering memory, E4 scheduler, E5 risk engine, and the active
credential, budget, privacy, review, and Compliance policies. E10 is not
eligible for implementation before those dependencies provide their
required policy and evidence interfaces.

### E10 — Self-audit and controlled learning

Scope: bounded outcome analysis, confidence updates, regression detection,
capability-profile invalidation, and owner-visible learning proposals.

Acceptance: one result cannot create an extreme permanent score; learning
cannot change authority, objective, constitution, permissions, risk
thresholds, or merge policy; stale and contradictory evidence blocks
unsafe selection; every update is attributable and reversible.

### E11 — End-to-end autonomous project pilots

Scope: disposable and controlled pilots proving objective intake through
completion under approved policies, with restart, idempotency, failure,
isolation, and rollback evidence.

Acceptance: no pilot changes a real production project or external system;
all required gates pass; human-controlled boundaries remain intact; any
uncertainty checkpoints safely.

## Capability gate template

Every epic must attach:

1. objective and requirement references;
2. bounded scope and prohibited paths;
3. architecture decision or ADR when authority changes;
4. migration and rollback plan;
5. deterministic tests;
6. disposable canary;
7. controlled pilot and failure-path pilot;
8. restart/resume and idempotency evidence;
9. cross-project isolation evidence;
10. security review and secret scan;
11. independent review and Compliance PASS;
12. checkpoint, next action, and transfer decision.

## Initial backlog

The first implementation tasks are intentionally small:

| ID | Task | Depends on |
|---|---|---|
| E0-T1 | Review and approve foundation documents | none |
| E0-T2 | Add immutable constitution hash/protection checks | E0-T1 |
| E1-T1 | Define objective schema and fixtures | E0-T2 |
| E1-T2 | Implement deterministic normalization and hashing | E1-T1 |
| E1-T3 | Add contradiction, ambiguity, and amendment proposal gates | E1-T2 |
| E1-T4 | Attach requirement traceability to plans and reports | E1-T2 |
| E2-T1 | Define roadmap/backlog schema and fixtures | E1-T4 |
| E2-T2 | Add explicit lifecycle and supersession transitions | E2-T1 |
| E2-T3 | Add eligible-task selection and critical-path analysis | E2-T2 |

No task is complete until its evidence is stored and the next checkpoint
is deterministic. E0-T1 is approved as Constitution Foundation v1
documentation by PR #14. E0-T2 is complete through PR #17. Dynamic
capability discovery is recorded as E9 and is not authorized to bypass the
E1–E5 prerequisites. E1-T1 is complete through PR #19; E1-T2 is complete
through PR #21; E1-T3 is complete through PR #23; E1-T4 is complete through
PR #25; E2-T1 is complete through PR #27; E2-T2 is complete through PR #29;
E2-T3 is now the next dependency-correct task.

## Checkpoint after PR #29

- Active main SHA: `7b0286b75eac227bfe74a219cac99d49e8e6f790`.
- Completed items: E0-T1 foundation documentation, E0-T2 immutable
  Constitution Authority enforcement, E1-T1 objective schema and fixtures,
  E1-T2 deterministic normalization and hashing, E1-T3 contradiction,
  ambiguity and amendment gates, E1-T4 requirement traceability, the E9
  roadmap definition, E2-T1 roadmap schema/dependency validation, and E2-T2
  lifecycle/supersession transitions.
- Evidence: PR #14, PR #15, PR #16, PR #17, PR #18, PR #19, PR #20, PR #21,
  PR #22, PR #23, PR #24, PR #25, PR #26, PR #27, PR #28 and PR #29 merged;
  285 tests passed; Ruff and diff check passed; authority, objective and
  roadmap canaries passed; deterministic Reviewer returned APPROVE;
  Compliance returned PASS.
  authority and objective canaries passed; deterministic Reviewer returned
  APPROVE; Compliance returned PASS.
- Scope: Constitution Authority is implemented without modifying the
  Constitution, master objective, owner authority, or protected policies.
  Live execute, deliver, and session resume fail closed without verified
  constitutional state. E1-T1 defines the schema, E1-T2 normalizes/hashes,
  and E1-T3 detects ambiguity/contradiction and creates inert proposals
  without approving or amending objectives. E1-T4 carries optional
  objective/requirement references and bounded evidence through execution,
  review, Compliance and delivery. E2-T1 defines immutable roadmap items
  and rejects unknown dependencies and cycles. E2-T2 enforces explicit
  lifecycle transitions, dependency-gated completion and non-destructive
  supersession.
- Next deterministic action: add deterministic eligible-task selection and
  critical-path analysis without starting a persistent scheduler.
