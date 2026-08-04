# Autonomous Technical Director Roadmap

Status: foundation proposal; no epic is production-ready until its gates
and transfer evidence pass.

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

No task is complete until its evidence is stored and the next checkpoint
is deterministic. The foundation remains pending human review until E0-T1
is explicitly approved.
