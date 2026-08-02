# Handoff Protocol

A handoff is an explicit transfer of accountability between roles. The sender remains accountable for the accuracy of its output until the receiver accepts the package. The receiver must reject the handoff when any required field is absent, contradictory, stale, or unauditable. No role may infer missing context.

## Universal handoff envelope

Every handoff includes: handoff ID, task ID, source state, destination state, sender, receiver, context versions, objective, scope, inputs, outputs, acceptance criteria, constraints, dependencies, known risks, evidence index, open questions, stop conditions, requested action, timestamp, and acceptance or rejection status.

## Role-to-role contracts

| Sender → receiver | Mandatory transfer | Receiver acceptance test |
|---|---|---|
| Human → Director | Goal, authority, constraints, risk tolerance, success condition, reserved decisions | Goal and authority are identifiable; ambiguity is resolved or escalated |
| Director → Planner | Approved objective, scope boundary, epics requested, constraints, priority, risk context, planning deadline | Objective is bounded and one strategic owner is recorded |
| Planner → Architect | Epic map, task list, dependencies, acceptance criteria, blockers, parallelism proposal, uncertainty | Tasks are independently bounded and technical questions are explicit |
| Architect → Implementer | Accepted architecture decision, one Ready task, implementation boundary, interfaces, constraints, acceptance conditions, stop conditions | No unresolved architectural choice is left to the Implementer |
| Implementer → Reviewer | One task outcome, changed artifact references, execution evidence, validation results, deviations, blockers, task status | Outcome is attributable, in scope, and reviewable against criteria |
| Reviewer → Compliance Officer | Review decision, criteria results, findings, severity, evidence, rework disposition, residual risks | Review is independent, complete, and accepted for compliance evaluation |
| Compliance Officer → Release Manager | Compliance decision, control mapping, evidence index, exceptions, residual risk, policy scope, audit record | All mandatory controls pass or have authorized exceptions |
| Release Manager → Done | Release decision, publication record, version, changelog, repository cleanliness, final scope, post-release observations | Release record is complete and outcome is terminal |
| Any role → Director | Failure, blocker, scope change, conflict, missing context, or escalation record | Director can identify owner, impact, next safe disposition, and whether human intervention is required |

## Acceptance and rejection

The receiver records Accepted, Rejected, or Accepted with bounded follow-up. Rejection names each missing or conflicting field and returns the package to the sender without changing task state. Follow-up cannot weaken an entry or exit criterion. An accepted handoff freezes the transferred context version; later changes require a new handoff.
