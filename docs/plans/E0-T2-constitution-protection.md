# E0-T2 — Immutable Constitution Hash and Protection Checks

Status: bounded implementation plan; not implemented in this
documentation-only repository.

## Traceability

- Roadmap item: E0-T2.
- Foundation: Constitution Foundation v1, sections on the root of trust,
  active policy objects, fail-closed behavior, and known-good rollback.
- Objective: preserve owner control of the constitution and prevent any
  worker, task, branch, model, or environment from selecting or mutating
  the active constitutional version.

## Scope

The implementation must verify the active constitution before every
consequential operation and record the verified constitution ID, version,
canonical hash, signature key ID, and compatibility result in bounded
evidence. It must reject missing, stale, malformed, unsigned, mismatched,
or incompatible active constitutional state.

## Allowed implementation paths

- The existing AGF runtime and its tests.
- A dedicated constitution verification module only if the existing
  architecture has no suitable boundary.
- Deterministic fixtures and disposable local state under tests.
- Documentation and ADR updates required to record the implementation
  decision.

## Prohibited paths and behaviors

- No changes to the master objective, authority ownership, permissions,
  risk thresholds, merge policy, or credential policy.
- No self-activation, self-promotion, or in-place replacement of AGF.
- No network, external key service, production deployment, or credential
  access in tests.
- No worker-controlled constitution selection or environment override.
- No acceptance based only on a filename, branch, mutable label, or
  unsigned hash.

## Required behavior

1. Canonical serialization is deterministic and rejects ambiguous input.
2. The active pointer resolves only to an owner-approved constitution
   record.
3. The record's canonical hash, signature, key ID, version, and
   compatibility are verified before consequential work.
4. Missing or invalid root-of-trust state fails closed with an actionable
   bounded error.
5. Verification evidence is attached to the operation and excludes
   secrets and complete prompts/transcripts.
6. Activation and rollback are atomic, externally authorized, auditable,
   and cannot be performed by the running worker.
7. Verification is idempotent and safe to repeat after restart.

## Acceptance criteria

- Valid known-good constitution state is accepted deterministically.
- Changed content, hash, signature, key ID, version, or compatibility is
  rejected.
- Missing active pointer, record, key, signature, or compatibility data is
  rejected rather than inferred.
- Attempts by a task, branch, model, or environment to select a different
  constitution are rejected.
- Concurrent verification and restart fixtures produce no partial state.
- Cross-project constitution state cannot be read or substituted.
- Existing execution, review, Compliance, and delivery gates remain
  unchanged.
- Failure, restart/resume, idempotency, isolation, and security evidence
  is captured.

## Validation commands

- `python -m pytest`
- `python -m ruff check .`
- `git diff --check`
- deterministic malformed-state and tamper fixtures
- disposable local canary with no external remote

## Risk and gates

Preliminary risk: MEDIUM because the work enforces an approved trust
boundary. It is HIGH or CRITICAL if it changes authority, key ownership,
permission scope, activation control, or policy thresholds; those changes
require human approval and are outside this plan.

Required gates: Architect decision where a new boundary is needed,
independent Reviewer, Compliance Officer, deterministic validation, a
failure-path pilot, rollback evidence, and a clean caller repository.

## Completion evidence

The implementation task may be considered complete only when the active
constitution verification behavior, negative tests, bounded evidence,
failure classification, and disposable canary results are recorded in a
separate implementation PR. This plan itself does not claim that runtime
enforcement exists.
