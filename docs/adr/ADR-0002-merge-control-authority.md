# ADR-0002: Merge control authority

- Status: Accepted
- Date: 2026-08-05
- Scope: E6-T1 only

## Context

AGF needs a bounded merge-control decision for the approved E6 roadmap. The
decision must preserve the Constitution, owner authority, protected
policies, deterministic risk classification, separation of review and
Compliance, and the existing controlled delivery workflow. E6-T1 defines
the decision record and aggregation boundary; it does not implement the
full E6 epic or authorize an unbounded autonomous merge capability.

## Decision

AGF may determine and execute a merge action only through one dedicated
Merge Policy Engine. The engine consumes the active Constitution, the
owner-approved merge policy, deterministic risk classification, independent
review, Compliance, CI and validation evidence, and certain remote state.
It is the only runtime component that may return an executable merge
authorization.

The engine cannot change risk thresholds, merge classes, owner approval
requirements, protected-path treatment, the active merge policy, or its own
authority. A reviewer provides an independent recommendation and is never
the merge authority.

## Authority hierarchy

1. The active Constitution is the highest runtime authority.
2. Owner-approved merge policy governs the permitted classes and approvals.
3. The Merge Policy Engine applies that policy deterministically.
4. Independent review, Compliance, CI, validation, and remote-state checks
   provide required evidence; none can replace the authority above.
5. Git execution consumes only a valid authorization artifact from the
   engine and cannot create or amend one.

## Risk-to-merge matrix

| Risk class | Merge behavior | Required approval |
|---|---|---|
| LOW | Autonomous squash merge is eligible only after every required gate passes. | None beyond the active policy. |
| MEDIUM | Autonomous squash merge is eligible only when the active owner-approved policy permits it; an executive summary is recorded. | Human approval only when required by project policy. |
| HIGH | Autonomous merge is prohibited. | Explicit owner approval. |
| CRITICAL | Autonomous merge is prohibited. | Extraordinary explicit owner authorization; permanently prohibited critical classes remain prohibited. |
| UNKNOWN or unresolved | No merge authorization is produced. | Escalation and resolution under the active policy. |

The engine must never lower a deterministic risk class because of reviewer
opinion or incomplete evidence.

## Required gates

An authorization requires, at minimum, verified Constitution and policy
identity, applicable risk evidence, independent review approval, Compliance
PASS, successful CI and task validations, clean caller state, unchanged
base identity, authorized paths, certain canonical remote state, and a
non-default delivery branch/worktree. Missing, stale, contradictory, or
failed evidence blocks authorization.

Constitution, objective, authority, credential, permission, risk-policy,
and merge-policy changes are protected classes and may not be autonomously
merged. Unresolved findings, failed Compliance, failed validation, and
uncertain remote state also block.

## Authorization artifact

The engine produces an integrity-bound, machine-readable authorization
artifact containing the decision identity, project and task identity, base
revision, delivery revision, risk class, gate evidence references, policy
identity/version, expiry or freshness boundary, and decision status. The
artifact is immutable after creation and is accepted by Git execution only
when its integrity, identity, freshness, and all referenced evidence still
verify. E6-T1 defines this contract; later tasks may integrate it with
delivery under their own approved scope.

## Idempotency and remote reconciliation

The same valid decision inputs produce the same decision identity and
artifact content. Repeating a request does not create a second authorization
for identical evidence. Before any delivery action, remote identity, base
revision, target branch, and branch state are reconciled. Drift, duplicate
delivery state, unavailable remote state, or contradictory observations
invalidate the artifact and block rather than retrying through uncertainty.

## Cancellation behavior

Cancellation, kill-switch activation, policy withdrawal, evidence expiry,
or owner escalation invalidates any not-yet-consumed artifact. Cancellation
is recorded as an audit event and cannot be undone by replaying an older
artifact. A new authorization requires fresh evidence and policy checks.

## Audit evidence

Every decision records bounded references to the project, task, policy and
Constitution identities; input evidence hashes or stable identifiers; risk
class; gate outcomes; reviewer and Compliance outcomes; remote-state
observations; authorization or blocking status; timestamps; and
cancellation/reconciliation outcomes. Raw transcripts, credentials, and
secret-shaped values are excluded.

## Rollback expectations

E6-T1 is additive and does not change existing delivery behavior. If the
engine or artifact validation fails, the safe result is no authorization.
The implementation can be reverted as one bounded change while retaining
the pre-E6 controlled delivery gates. Any later delivery integration must
provide a reversible branch/commit path and preserve the caller repository.

## Alternatives rejected

- Letting a reviewer authorize merge: rejected because review must remain
  independent from merge authority.
- Letting Git or a delivery adapter infer authorization: rejected because
  policy aggregation would be duplicated and could diverge.
- Allowing runtime configuration to replace the active policy: rejected
  because it violates the constitutional root of trust.
- Treating missing or uncertain evidence as approval: rejected because all
  uncertain states must fail closed.
- Implementing all E6 capabilities in E6-T1: rejected because the roadmap
  requires bounded dependency-ordered tasks.

## Consequences

E6-T1 has a clear single authority boundary and can be tested without
enabling autonomous merge. Low and medium authorization behavior remains
subject to the owner-approved policy, while high, critical, unknown, and
protected classes remain fail-closed. Later E6 tasks must consume this
contract and may not redefine it. The human merge requirement remains
unchanged unless an owner-approved policy explicitly says otherwise.
