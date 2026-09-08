# ADR-0007: Objective acceptance boundary

- Status: Proposed; owner decision required; not active
- Scope: Final completion mission, Objective acceptance and autonomous continuation
- Decision owner: Human for Objective intent and authority; Architect for implementation contracts

## Observed gap

The Objective model validates and hashes a JSON object whose status may say
APPROVED. No runtime reader authenticates that approval. The installed authority
generation has no Objective component. Free-text criteria have no approved
correspondence to executable evidence. Roadmap labels and provider responses
cannot supply these missing decisions.

Public session transitions previously allowed PR_READY to become COMPLETED
without acceptance proof. The accompanying correction rejects that request,
including actor labels and evidence strings supplied by callers. Historical
terminal records remain readable; their label is not a new acceptance proof.

## Proposed owner decision

Approve implementation of an Objective acceptance component within the existing
owner-controlled authority-generation mechanism, using the existing pinned key.
Do not introduce another root, runtime signer, caller-controlled approval flag
or second policy engine.

Its signed, immutable content would bind:

- schema/version, project ID and canonical repository identity;
- exact normalized Objective payload and content hash;
- applicable session identity and approved intent/goal binding;
- every mandatory requirement criterion and global completion criterion;
- constraints and prohibited outcomes needing an acceptance check;
- explicit mapping from each criterion to required task/requirement references
  and its approved verification method;
- criteria requiring human rather than mechanical acceptance;
- applicable authority generation and amendment/supersession identity.

A verification method specifies what constitutes evidence; it does not assert
that evidence already passes. Runtime must not invent the mapping, infer coverage
from text similarity or promote a test count to acceptance.

This requires a versioned extension of the existing generation contract. Legacy
generations must retain their existing operations but cannot authorize Objective
closure. The external owner controller alone publishes the signed component and
selects a compatible generation. An ADR, merged code, fixture signature or
approved PR does not perform activation.

## Required runtime implementation after the decision

An evaluator reads the installed generation and canonical session artifacts. It
verifies complete plan lineage, required integrated deliveries, retained task
definitions, current HEAD/tree, criterion coverage, fresh validation results,
independent Reviewer acceptance and Compliance Officer PASS. Active unresolved
findings prevent closure. Unknown, stale, failed and missing evidence remain
explicit. Non-mechanical criteria require their designated acceptance record.

The Director's internal closure operation recomputes/verifies the content-bound
decision under session and project locks before recording COMPLETED. Caller
reports and public transition APIs cannot mint success. Target, Objective or
authority changes invalidate reuse as current acceptance without erasing history.

The coordinator performs one durable action per tick: reconcile, assess when
required, select work using verified dependencies, invoke DeliveryPipeline, wait
for reserved external actions, reconcile observed effects and evaluate acceptance.
It reuses campaign locks, budgets and bounded recovery. A driver's COMPLETE text
or one successful external action does not establish Objective completion.

## Current diagnostic implementation and limits

`session audit-completion` checks the canonical plan and integrated delivery
evidence. It rejects silently removed historical work, missing integration,
tampered artifacts and inconsistent baselines. It does not change session state,
execute validation commands, approve an Objective or return SUCCESS.

Even when all plan work is integrated, it reports HUMAN_REQUIRED because the
authenticated Objective contract and criterion acceptance are absent. This is
a diagnostic of the prerequisite gap, not the proposed evaluator. Campaign
COMPLETE remains an operation-level result and is not consumed as Objective
acceptance. Full continuation and live engineering E2E remain pending.

## Acceptance and activation gates

Before activation, tests must reject forged APPROVED objects, signatures from
another root, cross-project/session replay, omitted or remapped criteria, stale
authority/target, unmatched review decisions and unknown human criteria. They
must also prove valid current evidence, atomic closure, crash/restart, idempotent
reconciliation and full governed continuation. Independent Reviewer and Compliance
Officer acceptance remain mandatory.

The requested owner decision is approval or rejection of this existing-root
Objective component and explicit criterion-mapping design. Approval to implement
is separate from installing a signed activation record or live pilot credentials.
Neither installation nor credential changes are part of this proposal.
