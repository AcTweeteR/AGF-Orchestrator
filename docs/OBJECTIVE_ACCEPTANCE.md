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

## External owner publication

The owner runs `tools.owner_objective_publication` in the existing independently
controlled owner environment. It accepts no signing key, trust-root path or
runtime callback. It requires the installed pinned Ed25519 generation and the
complete proposal above, naming exactly the next generation. Its first operation
is publication of a verified candidate, with no selector or floor change:

```sh
python -m tools.owner_objective_publication prepare --project PROJECT_ID --operation-id OPERATION_ID --proposal PROPOSAL.json
python -m tools.owner_objective_publication verify --project PROJECT_ID --operation-id OPERATION_ID --proposal PROPOSAL.json
```

Preparation preserves all six existing component contents and adds only Objective
acceptance. It revalidates the entire proposal against current canonical planning
without silently correcting the reviewed plan hash. The signed publication record
binds the operation, proposal, candidate manifest and exact active predecessor.
Repeating identical preparation is idempotent; occupied generation identities,
different content, changed state or a changed predecessor are rejected.

Activation remains a separate owner decision, bound to the manifest returned by
preparation and verification. An owner who authorizes that specific activation
runs:

```sh
python -m tools.owner_objective_publication activate --project PROJECT_ID --operation-id OPERATION_ID --proposal PROPOSAL.json --expected-manifest REVIEWED_MANIFEST_HASH
```

This implementation does not itself authorize running that command. Approval to
implement ADR-0007 remains separate from deployment activation. No command creates
keys or another root. A manifest hash identifies content; it is not a caller flag
that grants runtime approval. Session, project and registry locks protect final
validation and commit. A committed activation can be recovered or repeated without
activating twice. ALREADY_ACTIVE reports the historical owner operation, not
current Objective completion or fresh provider eligibility.

The generic legacy migration controller cannot activate an Objective candidate or
replace installed Objective authority with a legacy bundle. Superseding an already
accepted Objective remains outside this publication operation. After an exact
owner-authenticated external target advancement, the owner may instead publish a
successor generation for the same Objective, criteria, session, repository identity,
and goal, bound to the freshly projected plan. Any change to accepted Objective
content is rejected. Generation changes can invalidate generation-bound provider
evidence; current eligibility must still be
verified and, when required, refreshed through the existing owner-controlled flow.

Tests use ephemeral authority only. Actual owner publication and activation are
still required for the live scenario; the runtime never imports this owner tool.

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

The daemon has a built-in session driver for background continuation. Do not
configure this CLI as a generic campaign work command: generic command drivers
have a different protocol and lock ownership. Register the built-in driver:

```sh
agf-orchestrator campaign-runner register-session --state-dir "$AGF_STATE_DIR" --session SESSION_ID --campaign-id campaign-example --retry-budget 3 --execute --confirm-execution --confirm-delivery --json
agf-orchestrator campaign-runner run --state-dir "$AGF_STATE_DIR"
```

The state directory must match the configured session state. An explicit Architect
configuration is stored as an absolute path so later daemon working directories
do not change its meaning. Loading still verifies root and symlinks. Registration
binds one immutable driver configuration and existing campaign retry budget to the
session. It requires an active registered project and installed generation
authority; it does not publish or activate authority. The confirmation flags
authorize execution and delivery, not Objective approval or protected merges.
The existing `install-launchd` command can render a supervisor configuration;
registering a session does not install or start an operating-system service.

Polling verifies the exact repository, authority, plan lineage and delivery
candidate without invoking a provider or writing integration receipts. The work
step reconciles through SessionManager. Only verified canonical reconciliation
advances the campaign target and plan binding, retaining its budget and authority.
Restart between reconciliation and campaign save revalidates the full lineage.
Transient session/project lock contention uses the existing bounded retry/backoff
budget rather than declaring canonical evidence corrupt. If the same project lock
also prevents saving that transition, the runner leaves a content-hashed deferred
retry record. A restart applies it by exact before/after comparison ahead of lease
handling. Corruption or unrelated concurrent state blocks without deleting the
record; a crash after saving it is idempotent. The deferred record does not reset
or increase either retry or assessment budgets.
An exact merge arriving during a wait is reconciled on the next cycle.

Built-in session `COMPLETE` requires canonical Objective acceptance; generic
campaign `COMPLETE` remains an operation result. `NO_JUSTIFIED_WORK` is a separate
terminal outcome and does not imply a satisfied Objective.

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

The bounded continuation CLI and built-in daemon driver connect assessment,
delivery, external-action waiting, reconciliation and closure. Generic campaign
COMPLETE is still only an operation result. Deployment of the owner publication
flow, a qualified live execution target, complete
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
selection. For campaign-bound sessions, fresh assessment invocations persist a
start and outcome before another attempt is allowed. Both returned and raised
invocations consume the existing retry budget (initial invocation plus retries).
Restart, direct assessment entry and campaign retry reset cannot erase this
consumption; an unknown interrupted outcome requires reconciliation. Legacy
sessions without a campaign binding retain their existing behavior. Canonical
provider timeout evidence is also applied to the actual planning adapters.
Effective provider/model routing provenance and live budgets still require E2E
evidence. No new cost
subsystem or unapproved provider promotion is introduced by this implementation.

Historical known failures across a changed baseline may still require human
reconciliation. Unresolved direct or continuation dispatch journals prevent
closure even if all earlier tasks already have integration receipts.
