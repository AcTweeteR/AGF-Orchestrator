# Autonomous Technical Director: Foundation Architecture

Status: foundation proposal; implementation begins only after review and
approval of this document and the constitution.

This specification extends the controlled runtime already present in
AGF-Orchestrator. It does not replace the proven plan, execution, review,
compliance, session, locking, and delivery pipelines. New capabilities are
introduced behind explicit gates and remain resumable.

Normative authority, lifecycle, risk, merge, recovery, budget, and safety
rules are defined only in `CONSTITUTION.md`. This document specifies
components and interfaces; summaries here cannot create a second policy.

## 1. Target operating model

The User/Owner supplies a master objective and project policy. The
Autonomous Technical Director (AGF) owns operational continuation inside
those boundaries. The Planner decomposes authorized requirements. The
Architect records material design decisions. The Implementer changes only
an isolated approved task scope. The Reviewer independently evaluates the
result. The Compliance Officer checks governance evidence. The Release
Manager evaluates delivery readiness. The Observer records evidence and
status without authority to approve.

Implementation and review providers are adapters, not authorities. The
bootstrap deployment may assign a local implementation adapter and an
independent review adapter, but those assignments do not change role
authority or the constitution.

Bootstrap mode uses a provisional human-operated Director to create the
initial architecture, roadmap, and backlog. Transfer mode moves one tested
responsibility at a time to AGF. Full autonomy mode lets AGF select and
continue the next eligible task while independent review, compliance, and
human escalation remain active.

Architecture ownership is explicit: the User/Owner owns the approved
architecture baseline and ADR activation. The Architect drafts and evaluates
decisions; AGF selects only within that baseline. Material conflict,
architecture drift, or a design outside the baseline escalates to the
Owner. The Architect never approves its own implementation.

## 2. System architecture

```text
Objective + Policy
        |
        v
  Objective Engine -----> Roadmap/Backlog -----> Scheduler
        |                         |                |
        v                         v                v
  Traceability              Engineering Memory  Safe Plan
                                                       |
                                                       v
             Isolation -> Implementer -> Validation -> Reviewer
                                                       |
                                                       v
                                     Compliance -> Risk -> Delivery
                                                       |
                         +-----------------------------+----------------+
                         |                                              |
                    Merge/Release                                  Human Inbox
                         |                                              |
                         +-------- Checkpoint + Audit + Next Task <-----+
```

Boundaries:

- **Governance boundary:** constitution, policy, objective approval, and
  human decisions.
- **Planning boundary:** normalized requirements, roadmap, tasks,
  dependencies, and traceability.
- **Execution boundary:** isolated worktrees, scoped paths, adapters,
  validation, and bounded evidence.
- **Decision boundary:** independent review, compliance, risk, merge, and
  escalation.
- **Persistence boundary:** project-isolated state outside repositories,
  atomic artifacts, locks, hashes, and append-only audit events.
- **External-effects boundary:** Git remotes, pull requests, releases,
  deployments, and other side effects require explicit policy gates.

## 3. Immutable objective engine

The approved objective is a versioned, content-addressed object:

```json
{
  "schema_version": "1.0",
  "objective_id": "objective-<deterministic-id>",
  "project_id": "registered-project",
  "version": 1,
  "content_sha256": "<hash>",
  "status": "APPROVED",
  "outcomes": [],
  "requirements": [
    {
      "requirement_id": "REQ-001",
      "statement": "",
      "priority": "mandatory",
      "constraints": [],
      "invariants": [],
      "quality": [],
      "security": [],
      "acceptance_criteria": [],
      "prohibited_outcomes": [],
      "traceability": []
    }
  ],
  "operational_limits": {},
  "human_intervention_rules": [],
  "assumptions": [],
  "ambiguities": [],
  "contradictions": [],
  "approved_by": "owner",
  "approved_at": "<timestamp>"
}
```

Normalization is deterministic and preserves source references. Every
requirement must be classified as clear, ambiguous, or contradictory.
Contradictions and unresolved mandatory ambiguities block execution.
Amendments create a new proposed version; the approved hash never changes.
Every roadmap item, task, plan, finding, and completion claim carries one
or more `requirement_id` references.

## 4. Roadmap and backlog model

Roadmap records are versioned and append-only with explicit supersession:

```json
{
  "roadmap_version": 1,
  "objective_id": "objective-...",
  "milestones": [],
  "epics": [],
  "tasks": [
    {
      "task_id": "task-<hash>",
      "requirement_refs": ["REQ-001"],
      "epic_id": "EPIC-001",
      "objective": "",
      "dependencies": [],
      "acceptance_criteria": [],
      "validation_strategy": [],
      "allowed_paths": [],
      "risk_estimate": "LOW",
      "expected_artifacts": [],
      "status": "READY",
      "supersedes": null,
      "completion_evidence": []
    }
  ],
  "critical_path": [],
  "generated_by": "planner",
  "content_sha256": "<hash>"
}
```

IDs are derived from the immutable objective, stable semantic content, and
parent identifiers. Dependency cycles, missing references, oversized tasks,
empty acceptance criteria, and unauthorized path scopes are rejected. A
replan appends a new roadmap version and marks superseded work; it never
silently deletes a task or changes the master objective.

## 5. Engineering memory

Memory is project-isolated, versioned, searchable, attributable, bounded,
and linked to evidence. It contains ADRs, RFCs, rejected alternatives,
invariants, security decisions, accepted risks, technical debt, temporary
exceptions, incidents, recurring findings, performance baselines,
compatibility requirements, and known limitations.

Memory entries contain `entry_id`, type, title, bounded summary, tags,
requirement references, evidence references, author/actor, created and
superseded timestamps, content hash, and sensitivity classification.
Complete prompts and transcripts are prohibited. Rewriting requires a new
entry and explicit supersession. Planning and review must query relevant
memory and record the query scope in evidence.

## 6. Autonomous scheduler

The first scheduler is a resumable CLI loop, not a daemon. It supports:

`start`, `run`, `pause`, `resume`, `cancel`, `status`, `next`, `inbox`, and
`audit`.

Selection order is deterministic: mandatory unblocked requirements, active
critical path, priority, dependency readiness, risk policy, resource
budget, then stable task ID. The scheduler checks project/session locks,
base SHA, current policy and constitution hash before every consequential
operation. It records a lease, operation ID, attempt, and checkpoint before
execution. Interrupted leases become `UNCERTAIN` until reconciled; they are
never silently retried.

The scheduler may run independent tasks sequentially at first. Parallel
execution is allowed only when path scopes, locks, dependencies, budgets,
and merge ordering prove non-conflict. Deadlock, repeated non-convergence,
budget exhaustion, unavailable credentials, and uncertain remotes create
bounded inbox items.

## 7. Safe plan and project loop

The planner produces plans compatible with the current `ExecutionPlan` and
delivery models. A plan must include repository identity, exact base SHA,
objective references, allowed and prohibited paths, assigned roles,
validation commands, acceptance criteria, correction limit, delivery policy,
risk, human actions, and operation identifiers.

The controlled loop is:

1. Verify constitution and policy hashes.
2. Verify project registration and canonical repository identity.
3. Load the immutable objective and relevant memory.
4. Reconcile roadmap, sessions, locks, budgets, and remote state.
5. Select the next eligible task.
6. Generate or reuse a hash-matched safe plan.
7. Execute in isolation through the approved Implementer adapter.
8. Run deterministic validations and scope checks.
9. Obtain independent review and bounded corrections.
10. Run Compliance Officer checks.
11. Classify risk from deterministic rules plus reviewer evidence.
12. Deliver, merge, or escalate under policy.
13. Update roadmap, memory, evidence, and checkpoint.
14. Continue or produce a bounded terminal state.

The loop terminates only on proven completion, pause/cancel, required
human approval, budget exhaustion, deadlock, unavailable credentials,
contradiction, non-convergence, or unprovable safety.

## 8. Risk model

Risk is the maximum applicable class after deterministic signals and
independent evidence are combined:

| Class | Examples | Default merge behavior |
|---|---|---|
| LOW | isolated documentation or low-impact code, reversible, strong tests | automatic merge permitted after all gates |
| MEDIUM | broader code change, public behavior, dependency or persistence impact | automatic merge may be permitted; Director inbox summary required |
| HIGH | auth, secrets, migration, security path, release, difficult rollback | human approval required |
| CRITICAL | constitution, policy, production, financial, destructive or unsafe external effect | autonomous merge prohibited |

Signals include changed paths/components, code/documentation, auth,
authorization, secrets, cryptography, persistence/migrations, destructive
operations, APIs, dependencies, build/release/infrastructure, production,
financial and external effects, security-sensitive paths, test coverage,
review confidence, uncertainty, size, rollback difficulty, and incident
history. Deterministic rules raise risk; reviewer opinion alone cannot lower
it. Unknown signals are conservative and block when material.

The precedence and protected-path rules in `CONSTITUTION.md` are
authoritative. This section is descriptive and cannot infer a lower class
from a local heuristic.

## 9. Merge and release policy

This section is an interface to the single active Merge Policy defined by
the constitution, not an independent policy. The current bootstrap runtime
remains non-merging; future activation, gates, protected objects, and
rollback behavior are governed only by that policy's verified hash.

## 10. Executive decisions and inbox

Human decision summaries contain only project, objective, task, risk,
change summary, significance, tests, review, Compliance, important risks,
rollback, recommendation, and the exact decision requested. Supported
decisions are `APPROVE`, `REJECT`, `PAUSE`, and `REQUEST_MORE_DETAIL`.

The Director inbox includes high/critical approvals, constitutional
proposals, blocked critical paths, budget exhaustion, non-convergence,
security findings, failed cleanup, uncertain remote operations, and final
completion. Medium-risk summaries are optional; routine successful low-risk
work is silent by default.

## 11. Crash-safe state and audit

Every consequential operation persists before and after execution:
project state, objective/roadmap/policy versions, current operation,
completed and uncertain operations, artifact hashes, Git state, delivery
state, decisions, risk, budget, next action, and cleanup. Writes are atomic,
permissions restrictive, and state is outside managed repositories.

The audit stream records event type, operation ID, project/session IDs,
actor, state transition, input/output hashes, bounded evidence references,
policy/constitution hashes, timestamps, and result. Raw prompts, complete
transcripts, and secrets are never persisted.

After interruption, recovery reconciles Git, remote branches, pull
requests, merges, releases, and declared external effects before retrying.
An unknown result is never converted into success.

## 12. Self-hosting and transfer

Bootstrap work is directed by a provisional Director through existing
controlled PRs. A capability transfers to AGF only after automated tests,
disposable canary, controlled low-risk pilot, failure-path pilot,
restart/resume, idempotency, isolation, and security review pass.

The running version never mutates itself in place. Activation uses an
isolated candidate, full validation, an atomic or reversible switch, and a
known-good fallback. Constitution and policy changes cannot be
self-activated. Bootstrap state records the directing version for each
operation.

The active, candidate, and known-good versions are distinct. Promotion,
rollback, and emergency stop belong to the external activation controller
and User/Owner under the constitutional lifecycle; AGF cannot select or
activate its own version.

## 13. Migration and compatibility

Existing plan, execution, review, compliance, project, session, lock,
inbox, remote identity, and delivery schemas remain authoritative during
migration. New fields are additive and versioned. Existing artifacts are
read-only compatible; no implicit schema migration occurs. Each migration
has an ADR, fixture compatibility tests, rollback instructions, and a
dual-read or staged activation period where needed.

## 14. Threat model

| Threat | Control | Evidence |
|---|---|---|
| Objective drift | immutable hash and amendment workflow | approved objective hash |
| Path escape or symlink | canonical paths and isolated worktrees | scope evidence |
| Secret disclosure | allowlist, redaction, bounded artifacts | secret scan |
| Prompt/transcript leakage | no complete transcript persistence | artifact audit |
| Self-approval | independent Reviewer and Compliance gates | role/evidence separation |
| Main-branch mutation | base SHA and controlled delivery | Git state evidence |
| Replay/duplicate work | deterministic operation IDs and locks | idempotency audit |
| Stale or uncertain remote | fetch/identity/base checks and escalation | remote evidence |
| Unsafe autonomy | constitution, risk gates, kill switch | policy hash |
| Crash inconsistency | atomic checkpoint and uncertain state | restart audit |

## 15. Pilot strategy

Each capability requires automated tests, disposable canary, controlled
real-project pilot, failure-path pilot, restart/resume, idempotency,
cross-project isolation, and security review. The sequence is:

1. AI-Skills-Compilation documentation/low-risk changes.
2. AGF-Orchestrator documentation and low-risk self-hosted changes.
3. Mnemosyne documentation and architecture.
4. Mnemosyne low-risk code.
5. Higher-risk projects only after sustained evidence.

AI Virtual Fund and production Home Assistant are excluded from early
pilots. Every pilot has a kill switch, bounded budget, explicit rollback,
and a human owner.

## 16. Definition of Done for autonomous AGF

The program is complete only when a controlled real project demonstrates,
with auditable evidence: objective parsing and immutability; roadmap and
task generation; deterministic selection and dependency handling;
isolated implementation; validation; independent review; bounded
correction; Compliance; risk classification; authorized low-risk merge;
medium-risk summaries; high-risk human decisions; restart/resume;
engineering memory; replan; self-audit; budget limits; cross-project
isolation; rollback; and requirement-by-requirement completion proof.

`PROJECT_COMPLETE` is emitted by AGF/Director only after the Observer's
independent final audit passes the constitutional proof set, including
negative constraints, consistent hashes, and no material uncertainty.
