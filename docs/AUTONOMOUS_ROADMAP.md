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

#### E6 execution backlog

E5 is complete through E5-T3 and PR #61. E6-T1 through E6-T8 are complete
through merged PRs #65, #69, #74, #76, #78, #80, #82, and #84, with their
required evidence retained in the AGF state store. The following bounded tasks
decompose only the approved E6 scope. All E6 dependencies are complete.
with their required evidence. No task
grants authority to change
the Constitution, protected policies, risk thresholds, or human merge
requirement.

| ID | Objective | Scope | Dependencies | Expected files/components | Acceptance criteria | Validation requirements | Risk | Rollback |
|---|---|---|---|---|---|---|---|---|
| E6-T1 (`COMPLETED`) | Define an immutable merge-gate decision record and deterministic gate aggregation. | Combine plan, implementation, review, Compliance, validation, risk, caller-clean, and base-SHA evidence into one bounded decision without authorizing merge. | E5-T3 | `src/agf_orchestrator/merge_models.py`; `src/agf_orchestrator/merge_policy.py`; focused tests. | Required gates are explicit; missing, contradictory, stale, or failed evidence blocks; forbidden risk classes cannot produce authorization; output is deterministic and serializable. | Full pytest; Ruff; diff check; valid, missing, contradictory, and forbidden-risk fixtures; restart/idempotency check. | HIGH: incorrect aggregation could permit unsafe delivery. | Additive schema/module only; revert the task commit and retain existing delivery gates. |
| E6-T2 (`COMPLETED`) | Authorize only low-risk merges after all mandatory gates pass. | Apply the E6-T1 decision to the existing controlled delivery path; preserve non-default branch/worktree and human merge policy. | E6-T1 | `src/agf_orchestrator/delivery.py`; `src/agf_orchestrator/git_delivery.py`; merge-policy tests. | Only a fully evidenced LOW decision can be eligible; no direct main/master execution or autonomous merge is introduced; repeated authorization is idempotent. | Full pytest; Ruff; diff check; disposable low-risk delivery and direct-default-branch failure canaries. | HIGH: merge authorization is a protected control boundary. | Revert authorization integration; existing delivery remains fail-closed. |
| E6-T3 (`COMPLETED`) | Produce bounded medium-risk summaries in the Director inbox. | Convert an evidenced MEDIUM decision into a concise, project-isolated, auditable human action item. | E6-T1, E6-T2 | `src/agf_orchestrator/inbox.py`; `src/agf_orchestrator/scheduler_journal.py`; inbox/report models and tests. | Summary contains stable identity, risk, failed/pending gates, evidence references, and required human action; no secrets or transcripts; no merge authorization. | Full pytest; Ruff; diff check; bounded serialization, persistence, restart, idempotency, isolation, and secret-rejection tests. | MEDIUM: omission or leakage could mislead human decisions. | Revert summary/inbox additions; retain existing inbox behavior. |
| E6-T4 (`COMPLETED`) | Route HIGH and CRITICAL decisions to explicit human escalation. | Preserve conservative risk outcomes and expose only bounded decision context; do not lower risk or infer approval. | E6-T1, E6-T3 | `src/agf_orchestrator/inbox.py`; `src/agf_orchestrator/risk_models.py`; escalation/report tests. | HIGH/CRITICAL and UNKNOWN states cannot authorize merge; human decision is explicit, auditable, resumable, and required before continuation. | Full pytest; Ruff; diff check; high/critical/unknown fixtures; restart/resume and fail-closed canaries. | HIGH: escalation errors can bypass safety. | Revert escalation integration; fail closed on unresolved high/critical decisions. |
| E6-T5 (`COMPLETED`) | Add a bounded kill-switch gate to merge decisions. | Consume the existing policy-controlled stop signal at the final authorization boundary; do not create a new authority source. | E6-T2, E6-T4 | `src/agf_orchestrator/merge_policy.py`; `src/agf_orchestrator/constitution.py`; policy/merge tests. | Active kill switch blocks authorization and delivery; state is observable and auditable; clearing it never retroactively authorizes stale evidence. | Full pytest; Ruff; diff check; enabled/disabled, stale-evidence, restart, and idempotency canaries. | CRITICAL: a faulty kill switch could permit forbidden execution. | Revert integration; default to blocked authorization. |
| E6-T6 (`COMPLETED`) | Escalate remote uncertainty without weakening local gates. | Classify unavailable, divergent, stale, or contradictory remote state and route it to the Director inbox. | E6-T2, E6-T4, E6-T5 | `src/agf_orchestrator/remote_identity.py`; `src/agf_orchestrator/git_delivery.py`; remote-state tests. | Remote uncertainty never authorizes merge; canonical identity and base-SHA checks remain mandatory; no unapproved network behavior is added. | Full pytest; Ruff; diff check; local fixture canaries for unavailable, drifted, and equivalent remote states; no external repository mutation. | HIGH: remote ambiguity can cause wrong-target delivery. | Revert remote decision integration; block on uncertainty. |
| E6-T7 (`COMPLETED`) | Generate bounded executive summaries from persisted E6 decisions. | Summarize one project decision and its required action using stable references only; no new policy or merge authority. | E6-T3, E6-T4, E6-T6 | `src/agf_orchestrator/inbox.py`; executive-summary model/tests. | Summary is bounded, deterministic, secret-safe, attributable, and omits raw transcripts; unresolved blockers remain visible. | Full pytest; Ruff; diff check; size-bound, secret-scan, restart, and project-isolation tests. | MEDIUM: lossy summaries could hide blockers. | Revert summary generation; preserve detailed audit records. |
| E6-T8 (`COMPLETED`) | Prove E6 compatibility with the existing bootstrap PR workflow. | Integrate the completed E6 gates with current controlled delivery without changing constitutional or human-controlled boundaries. | E6-T2, E6-T5, E6-T6, E6-T7 | `src/agf_orchestrator/delivery.py`; `src/agf_orchestrator/cli.py`; integration tests and E6 canary fixtures. | Existing bootstrap flow remains compatible; all mandatory gates run; only approved delivery branches are pushed; no autonomous merge is enabled; caller repositories remain clean. | Full pytest; Ruff; diff check; disposable success/failure, restart, idempotency, isolation, and security canaries; independent review and Compliance PASS. | HIGH: cross-component integration can bypass a gate. | Revert integration and retain the pre-E6 controlled workflow. |

### E7 — Global completion and self-audit

Scope: requirement-by-requirement completion, milestone reconciliation,
security/documentation/dependency/test audits, technical debt, and final
completion report.

Acceptance: completion requires every mandatory criterion and evidence;
deferred work is authorized; open high/critical blockers prevent complete;
audits create tasks without changing objective/constitution.

#### E7 execution backlog

E7 has no previously defined executable tasks. The following decomposition is
deterministic from the approved E7 scope and acceptance criteria; it adds no
product scope or authority. E7-T1 through E7-T3 are complete; E8 is the next
approved epic requiring deterministic decomposition.

| ID | Objective | Scope | Dependencies | Expected files/components | Acceptance criteria | Validation requirements | Risk | Rollback |
|---|---|---|---|---|---|---|---|---|
| E7-T1 (`COMPLETED`) | Audit approved requirements and retained evidence. | Reconcile roadmap, Constitution, ADRs, task evidence, validation and delivery records; identify only deterministic gaps. | E6-T8 | `docs/AUTONOMOUS_ROADMAP.md`; completion audit documentation. | Every approved task is classified with traceable evidence; unresolved high/critical blockers remain explicit; no protected source is changed. | Link/terminology checks; `git diff --check`; deterministic audit and secret-scan canary. | MEDIUM: omitted evidence could falsely signal completion. | Revert audit documentation; retain source records. |
| E7-T2 (`COMPLETED`) | Record technical debt and authorized deferred work. | Convert E7-T1 gaps and existing bounded debt into an attributable register without silently removing work. | E7-T1 | `docs/AUTONOMOUS_ROADMAP.md`; technical-debt documentation. | Every deferred item has owner/status/evidence/next action; no item weakens policy or changes objective; unknown status remains blocked. | Link/terminology checks; `git diff --check`; deterministic restart/readback and secret-scan canary. | MEDIUM: untracked debt can hide blockers. | Revert debt register; preserve audit findings. |
| E7-T3 (`COMPLETED`) | Produce the controlled release-readiness and completion report. | Summarize architecture, security, tests, Compliance, delivery, residual debt and remaining roadmap state. | E7-T1, E7-T2 | `docs/AUTONOMOUS_ROADMAP.md`; final readiness documentation. | Completion is claimed only when no READY/PLANNED work or blocking findings remain; Constitution is VERIFIED and main is clean/aligned. | Full repository validation; Ruff; `git diff --check`; deterministic readiness and secret-scan canaries; independent review and Compliance PASS. | HIGH: premature completion could hide unsafe residual work. | Revert readiness report; retain non-terminal roadmap state. |

#### E7-T1 completion audit

The deterministic audit reconciles the approved backlog against the retained
delivery evidence. E0 through E5 are complete through their recorded PRs;
E6-T1 through E6-T8 are complete through PRs #65, #69, #74, #76, #78, #80,
#82, and #84. The current validation baseline is 400 passing tests, Ruff
passing, `git diff --check` passing, and Compliance passing. Constitution
Authority remains VERIFIED; ADR-0002 and active ADR-0003 remain unchanged.

Task-to-delivery traceability is: E0-T1/#14 and E0-T2/#17; E1-T1/#19,
E1-T2/#21, E1-T3/#23 and E1-T4/#25; E2-T1/#27, E2-T2/#29, E2-T3/#31 and
E2-T4/#33; E3-T1/#35, E3-T2/#37 and E3-T3/#39; E4-T1/#41, E4-T2/#43,
E4-T3/#45, E4-T4/#47, E4-T5/#49, E4-T6/#51, E4-T7/#53 and E4-T8/#55;
E5-T1/#57, E5-T2/#59 and E5-T3/#61; and E6-T1/#65, E6-T2/#69, E6-T3/#74,
E6-T4/#76, E6-T5/#78, E6-T6/#80, E6-T7/#82 and E6-T8/#84. Each reference
resolves to the merged delivery record and its associated validation, review,
and Compliance evidence.

No unresolved HIGH or CRITICAL blocker was found in the reconciled records.
At the time of this audit, E7-T3 was the remaining E7 task; its readiness
report below is now complete and explicitly leaves the project non-terminal.

#### E7-T2 technical-debt and deferred-work register

| Item | Owner | Status | Evidence | Next action |
|---|---|---|---|---|
| E7-T3 controlled readiness report | Release Manager | COMPLETED | E7-T1 audit; E6-T1–E6-T8 PR evidence | Preserve the non-terminal readiness result and continue with E8 decomposition. |
| E8 staged self-hosting and transfer | Director | COMPLETED | E8-T1–E8-T4 controlled evidence; PRs #91–#94 | Preserve disposable-only evidence; decompose E9 next. |
| E9 dynamic capability discovery | Director | PLANNED; local Qwen remains diagnostic-only | E9 scope and acceptance above; no promotion evidence | Decompose only after approved capability interfaces exist; preserve diagnostic-only status. |
| E10 self-audit and controlled learning | Director | PLANNED | E10 scope and acceptance above; no implementation evidence | Decompose after E9 dependencies and evidence are complete. |
| E11 end-to-end autonomous pilots | Director | PLANNED | E11 scope and acceptance above; no pilot evidence | Decompose after all prerequisite epics and controlled pilot authority are verified. |

No item in this register changes the objective, Constitution, owner authority,
protected policy, risk threshold, or merge policy. Unknown evidence remains a
blocker and no deferred item is silently removed.

#### E7-T3 controlled release-readiness report

| Dimension | Result | Evidence |
|---|---|---|
| Architecture and governance | PASS | ConstitutionAuthority VERIFIED; ADR-0002 unchanged; ADR-0003 implementation and external activation controls verified, while the repository ADR remains `Proposed` until its governed activation record is applied. CRITICAL remains human-controlled. |
| Implemented validation baseline | PASS | 400 tests passing; Ruff PASS; `git diff --check` PASS; Compliance PASS; E6 canaries and reviews recorded by PRs #65, #69, #74, #76, #78, #80, #82, and #84. |
| Repository delivery state | PASS | Main reconciled with origin/main after merged PR #88; controlled delivery preserves isolated branches and caller cleanliness. |
| Roadmap completion | NOT READY | E8 controlled evidence is complete; E9, E10, and E11 remain PLANNED and must be decomposed and executed before final completion. |
| Protected boundaries | PASS | No Constitution, owner authority, root of trust, protected policy, or merge threshold was changed. Local Qwen remains diagnostic-only. |

Final readiness status is `NOT_READY_FOR_ROADMAP_COMPLETE`. This report does
not claim project completion while approved roadmap work remains PLANNED. E8
is now complete as controlled evidence; the next action is deterministic
decomposition of E9 from its approved scope.

### E8 — Staged self-hosting and transfer

Scope: candidate versions, activation, rollback, directing-version
recording, kill switch, and transfer from provisional Director to AGF.

Acceptance: AGF never mutates itself in place; candidate validation and
atomic/reversible activation pass; a known-good version remains available;
constitutional/policy changes cannot self-activate; controlled pilots
demonstrate autonomous continuation.

#### E8 execution backlog

E8 had no previously defined executable tasks. This deterministic
decomposition derives only from the approved E8 scope and acceptance criteria;
it adds no release, production, or constitutional authority. E8-T1 through
E8-T4 are now `COMPLETED`; E8 remains bounded to controlled evidence and no
production transfer.

#### E8-T1 candidate validation evidence

Candidate pin: `0ced6a433cdd7650cc91bcfcc214884031a9a959` (the reconciled main
commit before this evidence record). The candidate is identified by its
immutable full Git commit and canonical repository identity;
`git fsck --strict` and exact commit resolution establish integrity. No
artifact was activated and no repository was mutated in place.

The disposable canary evidence is explicit: the successful isolated delivery
path is covered by `test_bootstrap_delivery_keeps_caller_main_clean_and_never_merges`,
the failure path by `test_failed_validation_prevents_completed`, restart and
idempotency by `test_put_is_atomic_restartable_and_idempotent`, integrity and
activation rejection by `test_tampered_policy_hash_fails_closed` and
`test_invalid_activation_signature_fails_closed`, and secret safety by
`test_secret_redaction`. The side-effect-free restart/dry-run
boundary is covered by `test_bootstrap_dry_run_is_side_effect_free`.

Compatibility evidence is the 400-test passing baseline, Ruff PASS,
`git diff --check` PASS, Compliance PASS, and these disposable canaries
proving isolated branch/worktree delivery, caller-main cleanliness, failure
blocking, restart/idempotency, integrity rejection, secret-safe evidence, and
no merge action.

The candidate remains inactive, the known-good pin is retained, and the
provisional Director remains authoritative. E8-T2 is a CRITICAL activation and
rollback boundary requiring separate owner authorization; it is not performed
by this task.

#### E8-T2 transactional activation and rollback evidence

E8-T2 is satisfied by the existing owner-controlled transactional architecture.
The external `OwnerPolicyController` is the mutation authority exposed by the
AGF architecture: it prepares and signs policy/activation records, while
`PolicyAuthority` is verifier-only and exposes no activation API. The runtime
consumption path uses `PolicyAuthority`; the controller lives outside that
runtime path, and the store mutators are not runtime authorization APIs.
`PolicyStateStore` uses one SQLite database with
`BEGIN IMMEDIATE`, foreign-key enforcement, WAL/full durability settings, and
restrictive state-directory/database permissions. Activation atomically binds
the prepared policy, signed activation record, active state, generation, and
anti-replay journal. Rollback atomically records the signed tombstone, journal
entry, superseded generation, and pinned constitutional fallback target; the
active ADR policy is cleared so stale authority fails closed until the external
known-good state is re-established.

The exact verified evidence is the transactional-store and authority suite:
`test_activation_failure_at_each_boundary_is_atomic`,
`test_commit_restart_and_duplicate_are_deterministic`,
`test_rollback_failure_is_atomic_and_invalidates_active_generation`,
`test_concurrent_activation_has_one_committed_winner`,
`test_delivery_transaction_wins_or_loses_switch_race_deterministically`,
`test_delivery_commit_crash_leaves_non_replayable_recovery_state`,
`test_tampered_policy_hash_fails_closed`,
`test_invalid_activation_signature_fails_closed`,
`test_wrong_project_binding_and_missing_activation_fail_closed`, and
`test_rollback_is_owner_controller_only_and_removes_active_state`. Together
they cover commit-boundary failure, restart, duplicate/replay, generation and
project binding, activation/rollback races, crash recovery, integrity, and
runtime fail-closed behavior. The named binding test covers wrong-project
rejection; missing activation remains covered by the runtime resolver's
inactive-state fail-closed contract and is not claimed as a separate fixture.
No live self-hosted authority was activated by
this roadmap evidence task; the provisional Director and known-good fallback
remain authoritative.

#### E8-T3 directing-version and kill-switch evidence

The immutable directing-version evidence record is bound to project
`project-efc8e8ef7be7050b`, canonical repository
`https://github.com/AcTweeteR/AGF-Orchestrator.git`, candidate pin
`0ced6a433cdd7650cc91bcfcc214884031a9a959`, current reconciled main
`ecab7c1f4f5099e10a86655feecf155d9623a7c7`, the retained provisional Director
as known-good authority, Constitution identity `constitution-v1`, constitution
record hash `75f1fe5bae1bb303fdb7fb6234d9c87a4040cab118076b9470363360eacefa3c`,
constitution document hash
`9e9821e161331e26881211d898d063e01e7d416807f14cadfeb2fd191ad03fd6`, active
ADR-0003 policy generation `1`, evidence generation `1`, and operation identity
`operation-e8-t3-directing-version-evidence` (a documentary evidence operation,
not a self-authorizing transfer). No self-hosted activation generation is
claimed because live transfer is explicitly outside E8-T3.

The evidence is restart-safe because the authoritative generation and event
identity come from the E8-T2 SQLite state model rather than process memory.
The external owner controller alone may change kill-switch state. Each change
increments the authority generation and appends an anti-replay journal entry;
delivery holds the same transactional authority lock through consequential
commit. A stale generation, replayed operation, active switch, rollback, or
candidate/policy mismatch therefore fails closed and requires fresh evidence.
Clearing the switch requires a new owner-signed operation at the current
generation and cannot resurrect prior directing evidence.

The verified canary mapping is: valid generation and restart persistence via
`test_kill_switch_generation_and_clear_invalidate_old_state`; stale-generation
and replay rejection via the same test plus
`test_delivery_commit_crash_leaves_non_replayable_recovery_state`; concurrent
kill-switch/authority ordering via
`test_delivery_transaction_wins_or_loses_switch_race_deterministically`;
atomic crash boundaries via `test_activation_failure_at_each_boundary_is_atomic`
and `test_rollback_failure_is_atomic_and_invalidates_active_generation`;
project binding and policy-hash/signature fail-closed behavior via the
policy-authority tests; the candidate/policy mismatch case is covered by the
same exact hash-bound rejection exercised by
`test_tampered_policy_hash_fails_closed`; and provisional-authority/no-transfer
behavior via the existing bootstrap compatibility canaries. No candidate can
write the owner authorization,
clear the switch, replace the rollback target, or perform live transfer.

E8-T3 review/compliance evidence is retained with this record: independent
review APPROVE after bounded evidence correction, Compliance PASS, 400-test
baseline PASS, focused authority/concurrency suite PASS, Ruff PASS, and
`git diff --check` PASS. E8-T4 is covered by the disposable controlled pilot
record below; no live transfer occurred.

#### E8-T4 controlled transfer and continuation pilot evidence

The E8-T4 pilot used disposable `tmp_path` repositories and state namespaces;
it did not mutate the caller repository, the real external policy store, the
owner key, production systems, or user data. The pilot records the provisional
Director as the initial authority, verifies candidate/policy/Constitution
identities and the clear owner kill-switch generation, then exercises the
owner-controlled transactional path and bounded delivery workflow. The
transfer is a controlled contract simulation: no live self-hosted authority
was promoted, and the provisional Director remains authoritative outside the
disposable environment.

The successful continuation path is covered by
`test_bootstrap_delivery_keeps_caller_main_clean_and_never_merges`,
`test_scheduler_store_persists_and_resumes_after_restart`,
`test_selection_uses_roadmap_priority_and_returns_leased_state_without_mutation`,
and the bounded
scheduler-loop/recovery suites. These prove persisted state, restart/resume,
eligible-task selection, isolated delivery, mandatory review/Compliance
integration, checkpoint-safe failure, and continuation without conversational
state. Project isolation is covered by
`test_scheduler_store_is_project_isolated`,
`test_pointer_for_another_project_cannot_cross_project_boundary`, and the
project-isolated inbox/journal tests.

The kill-switch pilot is covered by
`test_kill_switch_generation_and_clear_invalidate_old_state` and
`test_delivery_transaction_wins_or_loses_switch_race_deterministically`:
owner activation advances generation, stale authority is rejected, no new
commit crosses the lock, transactional restart persistence is covered by
`test_commit_restart_and_duplicate_are_deterministic`, and clearing uses a new
owner authorization without resurrecting old evidence. The rollback
pilot is covered by
`test_rollback_failure_is_atomic_and_invalidates_active_generation` and
`test_rollback_is_owner_controller_only_and_removes_active_state`; tombstone,
journal, generation advancement, stale-candidate rejection, and recoverable
known-good fallback are retained.

Failure pilots cover invalid hashes/signatures, wrong project, stale
generation, replay, active kill-switch, crash before/after commit,
reviewer `REQUEST_CHANGES`, Compliance failure, remote uncertainty, and scope
violation through exact references: `test_tampered_policy_hash_fails_closed`,
`test_invalid_activation_signature_fails_closed`,
`test_stale_generation_wrong_project_and_hash_fail_without_state_change`,
`test_delivery_commit_crash_leaves_non_replayable_recovery_state`,
`test_repeated_unchanged_finding_stops_with_human_required`,
`test_compliance_fails_rejected_review_and_dirty_caller`,
`test_compliance_blocks_missing_risk_evidence`,
`test_delivery_preflight_reports_uncertain_remote_state`, and
`test_unauthorized_change_is_rejected`. Every case blocks, checkpoints, or
fails closed; no pilot failure mutates protected state. The
full controlled pilot baseline is 400 tests passing, with 19 focused authority,
restart, isolation, and delivery tests passing, Ruff PASS, diff-check PASS,
independent review APPROVE, and Compliance PASS.

E8-T4 is complete as a disposable controlled pilot record. It does not claim
production transfer, irreversible authority promotion, or self-activation.

E8-T2 review/compliance evidence: the independent Reviewer examined this
architecture, requested correction of evidence precision, and then returned
`APPROVE` after the corrections were limited to this roadmap record. The real
Compliance Officer check returned `PASS` with READY plan/task status, allowed
path conformity, review approval, passing validation evidence, objective
traceability, clean caller evidence, and no protected-file changes. The final
delivery record retains both the review approval and Compliance `PASS`.

| ID | Objective | Scope | Dependencies | Expected files/components | Acceptance criteria | Validation requirements | Risk | Rollback |
|---|---|---|---|---|---|---|---|---|
| E8-T1 (`COMPLETED`) | Validate immutable self-hosting candidates. | Define candidate identity, artifact integrity, compatibility evidence, known-good pin, and isolated validation without in-place mutation. | E7-T3 | `docs/AUTONOMOUS_ROADMAP.md`; candidate validation evidence. | Candidate validation is deterministic and restart-safe; current Director remains authoritative; no activation or production mutation occurs. | Full repository validation; Ruff; `git diff --check`; disposable candidate success/failure, restart, integrity and secret-scan canaries; independent review and Compliance PASS. | HIGH: invalid candidates could corrupt self-hosting. | Revert candidate evidence; retain known-good version. |
| E8-T2 (`COMPLETED`) | Prove atomic candidate activation and rollback. | Specify reversible activation transaction, pinned rollback target, generation and crash recovery without self-mutation. | E8-T1 | `docs/AUTONOMOUS_ROADMAP.md`; activation/rollback evidence. | Activation and rollback are atomic, crash-safe and owner-controlled; Constitution/policy changes cannot self-activate. | Crash/race/restart/idempotency/security canaries; independent review and Compliance PASS. | CRITICAL: activation changes the execution authority boundary. | HUMAN_REQUIRED; restore pinned known-good candidate. |
| E8-T3 (`COMPLETED`) | Record directing version and kill-switch behavior. | Bind directing-version evidence to verified candidate state and preserve immediate owner kill-switch control. | E8-T2 | `docs/AUTONOMOUS_ROADMAP.md`; directing-version/kill-switch evidence. | Stale or mismatched directing versions fail closed; kill switch remains owner-controlled and auditable. | Stale-generation, replay, fail-closed and race canaries; independent review and Compliance PASS. | CRITICAL: modifies the self-hosting authority boundary. | HUMAN_REQUIRED; retain provisional Director. |
| E8-T4 (`COMPLETED`) | Demonstrate controlled transfer to AGF. | Run only disposable/controlled pilots proving restart, isolation, rollback and autonomous continuation after verified transfer. | E8-T1, E8-T2, E8-T3 | `docs/AUTONOMOUS_ROADMAP.md`; pilot evidence. | No real production mutation; transfer is reversible, kill-switchable and constitutionally bounded; all pilot failures checkpoint safely. | Controlled success/failure pilots; full validation; independent review and Compliance PASS. | CRITICAL: transfer changes the directing authority. | HUMAN_REQUIRED; abort pilot and restore provisional Director. |

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
| E2-T4 | Add deterministic roadmap priority and version transitions | E2-T3 |
| E3-T1 | Define Engineering Memory schema and fixtures | E2-T4 |
| E3-T2 | Add atomic project-isolated memory storage and bounded search | E3-T1 |
| E3-T3 | Record bounded memory query evidence in planning and review | E3-T2 |
| E4-T1 | Define scheduler state and lifecycle schema | E3-T3 |
| E4-T2 | Add persistent resumable scheduler state transitions | E4-T1 |
| E4-T3 | Add deterministic task selection with leases and budget gates | E4-T2 |
| E4-T4 | Add bounded scheduler loop and status events | E4-T3 |
| E4-T5 | Add bounded scheduler command and audit surface | E4-T4 |
| E4-T6 | Persist bounded scheduler events and inbox items | E4-T5 |
| E4-T7 | Add lease expiry and interruption recovery gates | E4-T6 |
| E4-T8 | Add no-progress and deadlock stop gates | E4-T7 |
| E5-T1 | Define evidence-based risk schema and fixtures | E4-T8 |
| E5-T2 | Add deterministic risk signal aggregation | E5-T1 |
| E5-T3 | Carry risk assessment into review and Compliance evidence | E5-T2 |
| E6-T1 | Define immutable merge-gate decision and aggregation | E5-T3 |
| E6-T2 | Authorize fully evidenced low-risk delivery | E6-T1 |
| E6-T3 | Add bounded medium-risk Director inbox summaries | E6-T1, E6-T2 |
| E6-T4 | Escalate high and critical decisions explicitly | E6-T1, E6-T3 |
| E6-T5 | Add the bounded kill-switch merge gate | E6-T2, E6-T4 |
| E6-T6 | Escalate remote uncertainty safely | E6-T2, E6-T4, E6-T5 |
| E6-T7 | Generate bounded executive decision summaries | E6-T3, E6-T4, E6-T6 |
| E6-T8 | Prove E6 compatibility with bootstrap delivery | E6-T2, E6-T5, E6-T6, E6-T7 |
| E7-T1 | Audit approved requirements and retained evidence | E6-T8 |
| E7-T2 | Record technical debt and authorized deferred work | E7-T1 |
| E7-T3 | Produce controlled release-readiness report | E7-T1, E7-T2 |
| E8-T1 | Validate immutable self-hosting candidates | E7-T3 |
| E8-T2 | Prove atomic candidate activation and rollback | E8-T1 |
| E8-T3 | Record directing version and kill-switch behavior | E8-T2 |
| E8-T4 | Demonstrate controlled transfer to AGF | E8-T1, E8-T2, E8-T3 |

No task is complete until its evidence is stored and the next checkpoint
is deterministic. E0-T1 is approved as Constitution Foundation v1
documentation by PR #14. E0-T2 is complete through PR #17. Dynamic
capability discovery is recorded as E9 and is not authorized to bypass the
E1–E5 prerequisites. E1-T1 is complete through PR #19; E1-T2 is complete
through PR #21; E1-T3 is complete through PR #23; E1-T4 is complete through
PR #25; E2-T1 is complete through PR #27; E2-T2 is complete through PR #29;
E2-T3 is complete through PR #31; E2-T4 is complete through PR #33; E3-T1
is complete through PR #35; E3-T2 is complete through PR #37; E3-T3 is
complete through PR #39; E4-T1 is complete through PR #41; E4-T2 is complete
through PR #43; E4-T3 is complete through PR #45; E4-T4 is complete through
PR #47; E4-T5 is complete through PR #49; E4-T6 is complete through PR #51;
E4-T7 is complete through PR #53; E4-T8 is complete through PR #55; E5-T1
is complete through PR #57; E5-T2 is complete through PR #59; E5-T3 is
implemented in the current delivery.

## Checkpoint after E5-T3 implementation

- Active main base SHA: `e3e05e5606013dda1e82a2bc4557514d6ef4bcd0`.
- Completed items: E0-T1 foundation documentation, E0-T2 immutable
  Constitution Authority enforcement, E1-T1 objective schema and fixtures,
  E1-T2 deterministic normalization and hashing, E1-T3 contradiction,
  ambiguity and amendment gates, E1-T4 requirement traceability, the E9
  roadmap definition, E2-T1 roadmap schema/dependency validation, E2-T2
  lifecycle/supersession transitions, E2-T3 eligible selection/critical
  path analysis, E2-T4 deterministic priority/version transitions, and E3-T1
  Engineering Memory schema/fixtures, E3-T2 atomic storage/bounded search,
  E3-T3 bounded query evidence propagation, E4-T1 scheduler state and
  lifecycle schema, E4-T2 persistent resumable scheduler state, and E4-T3
  deterministic task selection with leases and budget gates, and E4-T4
  bounded scheduler loop/status events, E4-T5 bounded scheduler command and
  audit surface, E4-T6 bounded scheduler event/inbox persistence, E4-T7
  lease expiry/interruption recovery gates, and E4-T8 no-progress/deadlock
  stop gates, E5-T1 evidence-based risk schema/fixtures, E5-T2
  deterministic risk signal aggregation, and E5-T3 risk evidence
  propagation into review and Compliance.
- Evidence: PR #14, PR #15, PR #16, PR #17, PR #18, PR #19, PR #20, PR #21,
  PR #22, PR #23, PR #24, PR #25, PR #26, PR #27, PR #28, PR #29, PR #30,
  PR #31, PR #32, PR #33, PR #34, PR #35, PR #36, PR #37, PR #38, PR #39,
  PR #40, PR #41, PR #42, PR #43, PR #44, PR #45, PR #46, PR #47, PR #48,
  PR #49, PR #50, PR #51, PR #52, PR #53, PR #54, PR #55, PR #56, PR #57,
  PR #58 and PR #59 merged; 337 tests
  passed;
  Ruff
  and diff check
  passed;
  authority, objective and roadmap canaries passed; deterministic Reviewer
  returned APPROVE; Compliance returned PASS.
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
  supersession. E2-T3 selects eligible work and computes a deterministic
  critical path without a scheduler. E2-T4 adds bounded integer priority,
  stable priority/ID ordering, and immutable monotonic numeric roadmap
  revisions with backward-compatible priority defaults. E3-T1 defines
  immutable, bounded, project-isolated entries with typed memory categories,
  evidence and requirement references, content hashes, sensitivity, explicit
  supersession, and secret/transcript rejection.
  E3-T2 persists validated entries atomically under project namespaces,
  serializes writes with the existing project lock, accepts identical
  retries, rejects conflicting IDs and provides bounded deterministic search
  that excludes superseded entries.
  E3-T3 records only normalized query terms, result limit and stable entry IDs
  in planning and review evidence; memory content is never copied into the
  report. E4-T1 defines immutable scheduler state, explicit lifecycle
  transitions, lease pairing, bounded budgets, event sequence and human
  escalation fields without executing work. E4-T2 persists state atomically
  under project namespaces, resumes after restart and makes repeated current
  status transitions idempotent. E4-T3 selects the first eligible roadmap
  item by priority and ID, then applies one lease and a conservative budget
  gate without executing work. E4-T4 runs only a finite cooperative step
  budget, requires sequence/identity progress and emits bounded transition
  events; terminal and human-required states stop immediately. E4-T5 exposes
  bounded lifecycle commands and read-only status/audit snapshots with
  idempotent retries. E4-T6 persists monotonic transition events and bounded
  project-isolated inbox items with idempotent retries and secret rejection.
  E4-T7 releases only expired leases, pauses interrupted work and never auto-
  resumes HUMAN_REQUIRED or terminal states. E4-T8 blocks after repeated
  identical observations and when unfinished READY work is dependency-blocked
  with no eligible item. E5-T1 defines typed risk levels/signals, rollback
  difficulty, incident history, protected paths and evidence, with UNKNOWN
  inputs conservatively requiring CRITICAL and protected paths requiring HIGH
  or CRITICAL. E5-T2 derives reproducible signals from change size, protected
  paths, rollback, incidents, reviewer blockers and validation, then
  aggregates by maximum severity. E5-T3 carries a bounded risk summary into
  deterministic Reviewer and Compliance checks, rejects invalid assessments,
  and blocks when required risk evidence is absent.
- E5 is complete through E5-T3 and PR #61. E6-T1 through E6-T8 are complete
  through PRs #65, #69, #74, #76, #78, #80, #82, and #84. E7-T1 through E7-T3
  are complete; E8-T1 through E8-T4 are complete as controlled evidence.
  E9 remains the next approved roadmap epic and requires deterministic
  decomposition before execution. Final readiness remains
  `NOT_READY_FOR_ROADMAP_COMPLETE`.
