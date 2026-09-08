# ADR-0006: Governed completion contracts

- Status: Proposed for human integration
- Scope: Final completion mission; execution scopes, dependency admission and no-work disposition
- Decision ownership: Architect for technical contracts; Director for disposition

## Context

The completion audit found different task-path acceptance in Executor,
Reviewer, Compliance Officer and delivery. It also found an explicit no-work
assessment persisted as a blocked session. Neither inconsistency is justified
by the task or decision model. The user requested both corrections in the
final completion mission. This candidate does not activate policy or grant
new signing, delivery or merge authority.

## Path decision

A task's file path authorizes that exact relative path. Only a trailing slash
explicitly authorizes descendants of a directory. For example, `src/` permits
`src/module.py`; `src` and `src/module.py` do not authorize descendants.
Prefix siblings, absolute paths, traversal, ambiguous separators and `.git`
components are rejected. All execution, review, Compliance, patch, Git delivery
and delivery-reconciliation checks use the same lexical contract.

This decision narrows the Executor's former implicit prefix interpretation;
it never silently converts an existing file permission into a directory
permission. Old plans relying on implicit directory behavior must be reassessed
with explicit scope. Repository containment, symlink and patch checks remain
separate mandatory controls. A textual match does not bypass them.

The assessed planner continues to propose evidenced files only. A caller may
supply an explicitly authorized directory task through the existing plan API,
but this does not imply the assessed planner can authorize a directory from
file enumeration alone. No protected descendant authorization is inferred.

## No-work decision

`NO_JUSTIFIED_WORK` is a distinct operational disposition for a clean baseline
with explicit, evidence-referenced Architect assessment and no unresolved
unknowns. Missing proposals or failed evaluation remain `BLOCKED`. Active
reproduced findings retain precedence over a provider's no-work proposal.

A no-work plan contains no tasks, pending human intervention or dependency
work, and retains assessment/architecture bindings. Its session is terminal,
distinct from successful engineering completion. Restart retains the disposition.
Architecture derivation requires the selected provider's validated response,
its hash, original request and matching assessment, repository and objective.
Selection metadata alone cannot establish no-work. Invalid responses cannot
retain a no-work outcome from an unsuccessful provider attempt.
Public status-transition requests
cannot mint it without assessment. Historical `BLOCKED` sessions are not
silently reclassified; they require fresh assessment.

This is not a change to the canonical Task lifecycle. It does not mark an
Objective complete, certify that completion criteria are satisfied or permit
skipping Reviewer, Compliance Officer or Release Manager decisions.

The campaign driver does not yet expose this disposition. Accepting an arbitrary
driver outcome without canonical assessment verification would bypass the
contract. Campaign propagation remains pending the governed continuation driver.

## Dependency admission

Executor and delivery admit dependencies only from the current persisted session
and canonical plan lineage. Both per-task dependencies and plan graph edges are
enforced. A READY predecessor, caller completion flag or unmerged delivery branch
is insufficient. Each required predecessor needs a verified reconciliation
receipt bound to its intent, original plan and task definition, project, session,
target branch, base, integrated commit, tree and patch. Changed prerequisite
edges invalidate a historical task definition.

The reader verifies canonical artifact hashes and Git contents, rejects dirty
or advanced targets, incomplete or cyclic lineage, cross-session evidence,
tampering, ambiguous repeated delivery and subsequently changed predecessor
files. Multiple independent predecessor deliveries remain usable after restart.
The CLI accepts an explicit managed session for dependency admission.

Historical delivery establishes predecessor availability only. It does not
grant current execution or delivery authority, replace present policy/risk/scope
checks, approve an Objective or certify current validation results. Legitimate
overlapping edits require reassessment instead of weakening historical checks.

## Validation and remaining work

Tests exercise the same scope decisions across execution, review, Compliance
and patch checks, real isolated file changes, persistence of no-work assessment,
terminal restart behavior, malformed paths and rejection of unknown evidence.
Independent review and human integration remain required.

Evidence-based objective closure and the complete live continuation flow are
still separate work. This ADR does not declare the final
mission successful or replace owner-controlled activation.
