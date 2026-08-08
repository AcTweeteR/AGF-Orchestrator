# ADR-0003: Critical-only human merge policy

- Status: Proposed; not active
- Date: 2026-08-08
- Supersedes: the risk-to-merge matrix in ADR-0002 only after owner-controlled activation
- Authority: User/Owner policy proposal

## Context

The owner has requested a merge policy in which LOW, MEDIUM, and HIGH
operations may merge autonomously after every mandatory gate passes, while
CRITICAL operations always require a human decision. The active Constitution
owns merge policy, risk thresholds, and human-approval rules and prohibits AGF
from applying or activating those objects itself.

This ADR records the proposed architecture. It is not an active Merge Policy,
does not authorize a merge, and does not change the current project policy.

## Proposed decision

After external owner approval and activation, the single active Merge Policy
would use this matrix:

| Effective risk | Autonomous merge eligibility | Human decision |
|---|---|---|
| LOW | Eligible only after every mandatory gate passes. | Not required. |
| MEDIUM | Eligible only after every mandatory gate passes and the required bounded summary is persisted. | Not required. |
| HIGH | Eligible only after every mandatory gate passes and deterministic risk evidence remains HIGH rather than CRITICAL. | Not required. |
| CRITICAL | Never eligible for autonomous merge. | Required before the critical action. |
| UNKNOWN, conflicting, missing, or stale at a protected boundary | Classified as CRITICAL and blocked. | Required after deterministic reconciliation. |

The policy changes merge eligibility only. It does not lower risk, waive a
gate, activate releases or production deployment, or grant authority to an
Implementer, Reviewer, Compliance Officer, or Release Manager.

## Non-negotiable controls

An executable authorization requires all gates defined by ADR-0002, including
verified Constitution and policy identity, deterministic effective risk,
independent Reviewer APPROVE, Compliance PASS, successful validations and CI,
authorized paths, clean caller state, unchanged base identity, certain remote
state, and a non-default delivery branch.

Effective risk is computed outside implementation by the active Risk Policy
and is the maximum applicable class. No later participant may lower an earlier
signal. Constitution, objective, policy, credential, permission, merge-rule,
production, financial, destructive, and uncertain protected-boundary changes
remain CRITICAL. Any ambiguity about the trust boundary resolves to CRITICAL.

The Merge Policy Engine consumes an immutable risk result and an owner-signed
policy object. It cannot modify either input. Git execution consumes only an
integrity-bound authorization and cannot create, amend, or reinterpret it.

## Required active policy artifact

Activation requires an owner-controlled policy object outside the managed
repository containing, at minimum:

- policy ID, schema version, effective version, and canonical hash;
- owner signature and signing-key ID;
- the exact risk matrix above;
- mandatory gate identities and freshness limits;
- CRITICAL and protected-object prohibitions;
- compatibility result and pinned rollback target;
- activation time and previous active policy identity.

Missing, unsigned, incompatible, stale, or mismatched policy evidence produces
`POLICY_NOT_ACTIVATED` and no merge authorization.

Activation also produces a separately signed, canonical activation record
binding the project ID, policy ID/version/hash, previous-policy hash, active
pointer value, activation time, compatibility result, rollback target, owner
key ID, and operation ID. Runtime verification requires the owner signature,
canonical record hash, project binding, policy hash, and active pointer to
agree. An unsigned, stale, cross-project, replayed, or mismatched activation
record is rejected with `POLICY_NOT_ACTIVATED`; repository content or an ADR
cannot substitute for that record.

## Activation procedure

1. The owner reviews and explicitly approves this proposal.
2. The external policy controller creates and signs the policy artifact.
3. Independent validation verifies schema, signature, compatibility, rollback,
   protected classes, and fail-closed behavior.
4. Runtime support is implemented and reviewed in isolation without changing
   the active policy pointer.
5. The external controller atomically updates the active-policy pointer, keeps
   the previous version as the rollback target, and signs the activation
   record binding that pointer update to the approved policy hash.
6. AGF verifies both the active policy and the activation record before any
   autonomous merge can occur.

Steps 2 and 5 are owner-controlled external actions. AGF cannot perform them.

## Migration and compatibility

Until activation completes, ADR-0002 and the current human-merge requirement
remain authoritative. Existing decisions are not upgraded or replayed. Every
pending decision must be recomputed against the new policy identity and fresh
evidence after activation.

The bootstrap delivery path remains non-merging unless and until it consumes a
valid authorization from the single active Merge Policy.

## Validation requirements

Before activation, deterministic tests must prove:

- complete LOW, MEDIUM, and HIGH evidence can be eligible;
- CRITICAL and protected-policy changes cannot authorize autonomous merge;
- UNKNOWN or conflicting protected-boundary evidence resolves to CRITICAL;
- implementer-supplied risk cannot override the Risk Engine;
- missing, stale, failed, or contradictory gates block;
- policy/signature/hash mismatch blocks;
- repeated identical decisions are deterministic and idempotent;
- kill-switch and remote uncertainty invalidate authorization;
- no direct main/master mutation or autonomous release is introduced.

Independent review, Compliance PASS, full tests, Ruff, diff checks, security
canaries, and an owner-controlled activation receipt are mandatory.

## Rollback

The external controller restores the pinned previous policy atomically. Any
authorization created under the superseded policy is invalidated and cannot be
replayed. Recovery requires fresh policy, risk, gate, and remote-state evidence.

## Consequences

The proposal permits higher unattended throughput for evidenced HIGH work only
after activation, while retaining a strict human boundary for CRITICAL work.
It also makes policy activation, self-authority changes, and uncertainty at a
protected boundary explicitly CRITICAL, preventing AGF from granting itself
the authority described here.
