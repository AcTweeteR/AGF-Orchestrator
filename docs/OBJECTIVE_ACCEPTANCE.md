# Objective acceptance runtime

The implementation follows [ADR-0007](adr/ADR-0007-objective-acceptance-boundary.md).
Design approval and deployment activation are separate. Nothing in the runtime
creates signatures, installs keys or activates a generation.

## Authenticated contract

Authority generation schema `2.0` requires Ed25519 and the existing six
components plus `objective_acceptance`. Legacy generation serialization and
operations remain supported; legacy generations cannot authorize Objective
closure. The runtime uses the central authority resolver and existing trust root.

The Objective component has schema `1.0` and binds its generation, project,
canonical repository identity, session, normalized goal hash, normalized Objective
and Objective hash. Its `criteria` list contains exactly every mandatory requirement
criterion, completion criterion, constraint and prohibited outcome. Each item binds
`criterion_id`, `statement_sha256`, `method`, `task_ids` and `validation_commands`.
Methods are `validation` or `human`. Mechanical validators must belong to the
reviewed task definitions. Human criteria cannot also specify commands.
The plan Objective identity must match the signed Objective, mandatory requirements
must be referenced, and mapped tasks must reference the corresponding requirement.
Historical Objective and requirement bindings must remain unchanged.
All canonical plan tasks must be covered. Task IDs and criterion text cannot be
silently replaced, removed or accepted from a caller's report.

## Closure

After integration and reconciliation, invoke:

```sh
agf-orchestrator session complete --session SESSION_ID --execute --confirm-execution --json
```

These flags authorize running current validation; they do not approve the Objective.
The Director verifies the active project, canonical plan and retained lineage,
integrated delivery intents and receipts, independent Reviewer and Compliance
Officer decisions, current policy and constitution, target HEAD/tree, stop signal,
criterion coverage and unresolved findings. It runs all reviewed task validators
in an isolated worktree at the integrated target. Failed validation, modified
worktree or failed cleanup prevents closure.

Human criteria require an exact signed acceptance record under the existing root.
A pending report supplies the expected payload and immutable artifact name. The
payload binds the current Objective, criterion text, plan, target and authority;
a caller's boolean or actor label cannot substitute for that signature. Producing
and installing that record remains an external owner action.

Canonical authority, project registry, target, receipts and human signatures are
rechecked after validation and before recording completion under session and
project locks. The final project comparison and session persistence additionally
hold the registry writer lock, so project disablement cannot interleave between
those operations. Remote comparisons use canonical repository identity.
Only the internal operation can persist `COMPLETED`. Public status
transitions still reject it. The report is stored by content hash before the
session update. A crash before session persistence leaves the session uncompleted;
retry recomputes the decision. A repeated call validates current evidence again.
A later failure does not erase a historical terminal record, but does return
`objective_completed: false` rather than treating history as current success.

`session audit-completion` remains read-only. It can report verified Objective
authentication and integration, but never executes commands or returns SUCCESS.

## Evidence and remaining work

Integration tests use real temporary Git repositories, actual validator processes
and ephemeral signed authority fixtures. They cover valid closure, human acceptance,
failed validation, tampering during validation, project disablement, target drift,
crash before persistence and restart. These fixtures do not activate deployment
authority and do not demonstrate live-provider engineering.

A governed continuation driver connecting assessment, delivery, external-action
waiting, reconciliation and this closure operation remains pending. Generic
campaign COMPLETE is still only an operation result. Owner-side publication of a
compatible Objective generation, a qualified live execution target, complete
failure/recovery/resume E2E and the final independent mission audit remain open.
