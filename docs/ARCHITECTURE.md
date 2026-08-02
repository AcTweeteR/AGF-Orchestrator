# Architecture

AGF-Orchestrator is a layered control system. Work flows downward toward execution and evidence flows upward toward decisions. A layer may request work from the layer below it, but cannot silently assume the authority of another layer.

| Layer | Purpose | Inputs | Outputs | Responsibilities | Escalation rules |
|---|---|---|---|---|---|
| Human | Supply intent and reserved decisions | Intent, constraints, risk acceptance | Goals, approvals, clarifications | Set objectives; resolve reserved decisions; accept exceptional risk | Must decide when [HUMAN_INTERVENTION.md](HUMAN_INTERVENTION.md) requires it |
| Director | Own orchestration outcome | Goal, policy scope, status, escalations | Authorized plan, routing, disposition | Set boundaries; coordinate roles; resolve operational conflicts | Escalate ambiguity, policy conflict, or material risk to Human |
| Planning | Turn intent into bounded work | Goal, constraints, repository context | Task decomposition, dependencies, acceptance criteria | Define sequence, scope, and readiness evidence | Escalate unbounded scope or missing acceptance criteria to Director |
| Execution | Produce the requested change | Ready task, approved plan, authorized context | Change, execution evidence, status | Implement within scope; preserve provenance; report blockers | Escalate inability, unsafe instruction, or scope drift to Director |
| Review | Independently evaluate quality | Change, criteria, execution evidence | Findings, review decision, verification evidence | Test claims; identify defects; recommend acceptance or rework | Escalate unresolved or high-severity findings to Director |
| Compliance | Determine conformance | Change, policy mapping, review evidence | Compliance decision, exceptions, audit record | Check AGF and project policy; validate evidence; enforce controls | Escalate non-conformance or unapproved exception to Human |
| Release | Authorize and record delivery | Compliance approval, review result, release intent | Release decision, release record | Confirm gates; control release; preserve record | Escalate missing gates, conflict, or production risk to Human |
| Repository | Persist authoritative state | Task artifacts, decisions, evidence, release record | Durable records and retrievable history | Preserve integrity, identity, version, and traceability | Report unavailable, corrupt, or conflicting records to Director |

The layers are logical, not a prescription for components. An implementation may combine or distribute them only if authority, evidence, and separation of duties remain observable.
