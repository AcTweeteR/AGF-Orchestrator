# Task Model

A task is a bounded unit of work with an owner, scope, acceptance criteria, policy context, evidence, and lifecycle history.

## Lifecycle

**Backlog → Planned → Ready → Executing → Review → Compliance → Release → Done → Archived**

| State | Meaning | Entry evidence | Exit condition |
|---|---|---|---|
| Backlog | Accepted intent awaiting shaping | Request and source context | Plan is created or task is rejected by Director |
| Planned | Scope and approach are defined | Decomposition, dependencies, criteria | All readiness conditions are satisfied |
| Ready | Authorized for execution | Approved plan, context, policy scope, stop conditions | Implementer starts or task is returned |
| Executing | Work is actively produced | Execution contract | Outcome and evidence are complete, or failure is recorded |
| Review | Independent quality evaluation | Change and execution evidence | Review accepts or identifies rework |
| Compliance | Policy conformance evaluation | Review result and policy mapping | Compliance approves or records non-conformance |
| Release | Delivery authorization | Compliance approval and release intent | Release Manager authorizes or blocks |
| Done | Accepted outcome is delivered | Release record and final evidence | Retention criteria permit archival |
| Archived | Immutable historical record | Complete task history | No normal transition |

Rework returns to Planned or Executing as determined by the finding. A blocked task remains in its current state with an escalation record. Invalid transitions fail closed. [DECISION_MODEL.md](DECISION_MODEL.md) governs who may authorize transitions.
