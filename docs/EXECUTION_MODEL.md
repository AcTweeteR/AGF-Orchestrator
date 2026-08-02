# Execution Model

Execution is the controlled production of a task outcome by the Implementer under a Ready task. The Implementer may act only within the approved scope, context, acceptance criteria, and policy constraints.

## Execution contract

Before work begins, the system records task identity, approved plan, assigned authority, allowed scope, dependencies, acceptance criteria, policy scope, and stop conditions. During work, it records actions, produced artifacts, observations, deviations, and blockers. At completion, it records the outcome and evidence required by review.

Execution does not decide whether a result is acceptable, compliant, or releasable. Those determinations belong to the Review, Compliance, and Release layers respectively.

## Control rules

- Scope expansion creates a new planning decision; it is not implicit permission.
- A blocked dependency pauses execution and is escalated according to [FAILURE_MODEL.md](FAILURE_MODEL.md).
- An unsafe, unauthorized, or ambiguous instruction stops execution immediately.
- Evidence must be attributable to the task and sufficiently complete for independent review.
- An execution outcome without required evidence cannot advance to Review.
