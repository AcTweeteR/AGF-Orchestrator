# Objective acceptance runtime

The implementation follows [ADR-0007](adr/ADR-0007-objective-acceptance-boundary.md).
Design approval and deployment activation are separate. Nothing in the runtime
creates signatures, installs keys or activates a generation.

## Authenticated contract

Authority generation schema `2.0` requires Ed25519 and the existing six
components plus `objective_acceptance`. Legacy generation serialization and
operations remain supported; legacy generations cannot authorize Objective
closure. The runtime uses the central authority resolver and existing trust root.

The Objective component supports schemas `1.0` and `2.0` and binds its generation, project,
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

## First executable plan

Component schema `2.0` additionally binds `approved_plan_sha256`, the canonical
content hash of the complete first executable plan. This includes scopes,
validators, dependencies and predecessor lineage. The runtime may add only the
approved Objective and requirement references to a ready, unexecuted plan; the
result must exactly match that signed hash. It cannot replace executed work.

A recorded planning origin and intact draft lineage are required. Prior execution
states, reports, delivery intents or dispatch journals prevent first-plan binding.
Draft ancestors remain immutable and hash-verified during closure. Every delivery
and execution journal must belong to the approved plan or its retained successors.
Schema `1.0` retains conservative full-history checks without this draft boundary.

Prepare reviewable unsigned content with:

```sh
python -m tools.prepare_objective_acceptance --session SESSION_ID --component DRAFT.json --output PROPOSAL.json
```

The draft supplies the complete Objective and criterion mapping described above.
The output contains the projected plan and schema `2.0` component, marked
`PROPOSAL` with `authority_effect: NONE`. Preparation cannot approve, sign or
activate anything. Publication through the existing owner-controlled authority
mechanism remains a separate deployment action.

## Bounded continuation

```sh
agf-orchestrator session continue --session SESSION_ID --execute --confirm-execution --confirm-delivery --max-steps 10 --json
```

Each step reconciles canonical evidence, obtains a current assessment when needed,
checks the installed Objective, selects a task whose dependencies are verified,
or attempts governed delivery. All execution, Reviewer and Compliance Officer
gates still apply. Provider configuration is loaded only when assessment needs it;
a pending delivery can be reconciled without loading unrelated planning settings.

An outstanding exact delivery intent returns `WAIT` for external integration.
A later invocation observes integration, resumes the session and continues.
Unknown dispatch outcomes require inspection; a provider's success text cannot
close the Objective. Known implementation failures have a bounded retry budget
and must leave the Git target and worktrees clean before another attempt.
`CONTINUE` at the step limit is intermediate, and `objective_completed` remains
false unless the canonical closure operation verifies acceptance.

This bounded CLI is not yet a background continuation service. The generic
campaign daemon has a different driver protocol and holds locks incompatible
with invoking this CLI as its work command. Its fixed target binding also needs
verified advancement after delivery reconciliation. Do not configure the CLI as
a generic campaign work command or interpret campaign `COMPLETE` as acceptance.

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

The bounded continuation CLI connects assessment, delivery, external-action
waiting, reconciliation and closure. Persistent background continuation remains
pending. Generic campaign COMPLETE is still only an operation result. Owner-side publication of a
compatible Objective generation, a qualified live execution target, complete
failure/recovery/resume E2E and the final independent mission audit remain open.


## Resource and routing audit limits

Registered delivery retries share the existing correction budget across direct
and continuation entry points. Each durable invocation consumes that budget;
remaining correction rounds shrink after known failures and constructing a new
pipeline does not reset the counter. Unknown outcomes block further invocation.
Adapter timeouts and canonical bounded-timeout evidence must be finite and
positive. These controls bound attempts and duration, not a currency amount.

The capability selector follows authenticated owner priorities and eligibility
gates. A lower-priority fallback is attempted only after earlier candidates are
rejected or fail; the candidate set is finite within one Architect invocation.
This does not yet prove that the effective live model is the least costly eligible
capability. The observational cost-ranking helper is not wired to runtime
selection. Persistent assessment budgets across fresh retries and effective
provider/model routing provenance require further E2E evidence. No new cost
subsystem or unapproved provider promotion is introduced by this implementation.

Historical known failures across a changed baseline may still require human
reconciliation. Unresolved direct or continuation dispatch journals prevent
closure even if all earlier tasks already have integration receipts.
