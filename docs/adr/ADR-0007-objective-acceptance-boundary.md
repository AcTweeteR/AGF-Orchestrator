# ADR-0007: Objective acceptance boundary

- Status: Design approved by owner for implementation; activation not authorized
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

## Approved owner decision

The owner approved implementation of an Objective acceptance component within the existing
owner-controlled authority-generation mechanism, using the existing pinned key.
Do not introduce another root, runtime signer, caller-controlled approval flag
or second policy engine.

Its signed, immutable content binds:

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

## Runtime contract

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

Even when all plan work is integrated, the read-only audit reports HUMAN_REQUIRED.
It recognizes an authenticated installed Objective contract when available, but
does not execute its validators or establish criterion acceptance. Campaign
COMPLETE remains an operation-level result and is not consumed as Objective
acceptance. The built-in session campaign driver calls canonical closure before
recording COMPLETE; live engineering E2E remains pending.

## Acceptance and activation gates

Before activation, tests must reject forged APPROVED objects, signatures from
another root, cross-project/session replay, omitted or remapped criteria, stale
authority/target, unmatched review decisions and unknown human criteria. They
must also prove valid current evidence, atomic closure, crash/restart, idempotent
reconciliation and full governed continuation. Independent Reviewer and Compliance
Officer acceptance remain mandatory.

The owner explicitly approved this existing-root Objective component and criterion
mapping for implementation. Approval does not authorize a new trust root, runtime
signer, caller-controlled approval flags, another policy engine, a new active
authority generation, or installation of keys or credentials. Activation remains
an external owner decision. See [runtime acceptance](../OBJECTIVE_ACCEPTANCE.md)
for the implemented closure interface and remaining continuation work.


## First-plan binding within the approved boundary

Objective component schema `2.0` binds the complete first executable plan hash.
This is a content-bound implementation of the approved intent and criterion
mapping, not another authority source. Projection adds only Objective and
requirement references; it preserves scopes, commands, dependencies and lineage.
The owner must sign the exact resulting content through the existing mechanism.

Only planning with recorded origin, intact ancestry and no execution history can
use this boundary. All draft ancestors remain verified. No executed or uncertain
work may be reclassified as a draft; canonical intents and execution journals
outside the approved plan lineage prevent closure. Legacy contracts retain the
strict full-history rule. This does not authorize supersession of executed
obligations, owner publication, activation or installation of credentials.

## Background continuation within the existing boundary

The built-in session campaign driver delegates each work step to the same
continuation and closure gates. It does not hold the generic command driver's
project lock across SessionManager operations. The campaign invocation lock and
existing session, project and delivery locks prevent duplicate dispatch.

Its target and plan binding may advance only after canonical reconciliation;
restart verifies the retained plan ancestry and integration evidence. Read-only
polling cannot issue provider work or integration receipts. Immutable session
registration binds the driver and campaign retry budget. Fresh assessments consume
that budget durably, and unknown interruptions block another invocation. These
records account for attempts; they supply no approval authority. Publication,
issuance and activation remain external owner operations.

## Owner publication implementation

The external owner tool publishes a verified next-generation candidate from the
complete unsigned proposal, preserving the existing six components and pinned
root. Its record binds the operation to the exact proposal and active predecessor.
Activation is a separate explicit owner operation with the reviewed manifest hash;
it revalidates canonical planning and registration under the existing locks.
Neither the tool nor a manifest hash delegates approval to the runtime.

Preparing a candidate must not invalidate active authority. The anti-downgrade
floor therefore checks authenticated committed generations, excluding prepared
candidates. Missing or reduced floors after a committed generation still fail
closed. Objective candidates cannot be overwritten or activated over a different
predecessor, and legacy cutover cannot remove installed Objective authority.

Atomic publication and the existing metadata transaction support interrupted
preparation and activation. These changes implement this ADR's approved boundary;
they do not authorize actual activation, Objective supersession or key installation.
