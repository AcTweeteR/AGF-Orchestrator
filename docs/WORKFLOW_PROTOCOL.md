# Workflow Protocol

This protocol defines the default operational sequence. A transition is valid only when its required inputs, outputs, and validation evidence are recorded. The protocol coordinates development; it does not execute development.

## Default flow

**Goal → Director → Planner → Architect → Implementer → Reviewer → Compliance Officer → Release Manager → Done**

| Transition | Required inputs | Required outputs | Mandatory validation |
|---|---|---|---|
| Goal → Director | Human goal, constraints, risk context | Bounded objective and decision scope | Goal is understandable and authority is identified |
| Director → Planner | Approved objective and scope | Planning mandate, epics, and constraints | Scope has one strategic owner |
| Planner → Architect | Epic map, tasks, dependencies, criteria | Architecture request and technical acceptance conditions | Tasks are bounded and dependencies are explicit |
| Architect → Implementer | Accepted architecture, Ready task, policy scope | Implementation boundary and stop conditions | No unresolved architectural decision remains |
| Implementer → Reviewer | One task outcome and execution evidence | Review package | Outcome is in scope and evidence is attributable |
| Reviewer → Compliance Officer | Accepted review and findings disposition | Compliance package | Review is independent and criteria-based |
| Compliance Officer → Release Manager | Compliance approval, policy mapping, audit record | Release package | All mandatory controls pass or have authorized exceptions |
| Release Manager → Done | Release package, Director readiness approval | Publication record and final task record | Repository cleanliness, changelog, versioning, and scope are verified |

## Transition rules

Every transition records the source state, destination state, accountable role, timestamp, inputs, outputs, validation result, and [DECISION_PROTOCOL.md](DECISION_PROTOCOL.md) decision record when a judgment was required. A failed validation blocks the transition and routes the task to the role named in the finding. No role may silently skip a gate.

The durable task states remain defined in [TASK_MODEL.md](TASK_MODEL.md). Escalations follow [ESCALATION_PROTOCOL.md](ESCALATION_PROTOCOL.md). The Director may coordinate a return to an earlier stage but cannot replace a gate owner.
