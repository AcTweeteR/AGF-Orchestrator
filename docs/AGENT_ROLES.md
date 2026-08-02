# Agent Roles

The following roles are normative. A deployment may assign multiple roles to one person or agent only where separation-of-duties requirements remain satisfied.

## Director

- **Mission:** achieve the governed task outcome by coordinating the system.
- **Authority:** route work, prioritize, resolve operational conflicts, and escalate reserved decisions.
- **Responsibilities:** establish boundaries; assign roles; monitor state; manage escalations; prevent gate bypass.
- **Inputs:** human intent, task status, plans, findings, and escalations.
- **Outputs:** routing decisions, authorized transitions, dispositions, and escalations.
- **Escalation conditions:** ambiguity, policy conflict, unbounded scope, material risk, or unresolved authority.
- **Exit criteria:** outcome is Done or the task is explicitly rejected, blocked, or archived.

## Planner

- **Mission:** turn intent into bounded, executable work.
- **Authority:** propose decomposition, dependencies, sequencing, and acceptance criteria.
- **Responsibilities:** clarify scope; identify dependencies; define readiness and stop conditions.
- **Inputs:** intent, constraints, repository context, and applicable policy.
- **Outputs:** plan, task decomposition, criteria, dependencies, and planning risks.
- **Escalation conditions:** missing authority, unbounded scope, incompatible constraints, or unclear success.
- **Exit criteria:** the Director accepts a complete plan or records disposition.

## Architect

- **Mission:** establish a technically coherent and governable solution boundary.
- **Authority:** decide technical structure within approved constraints; recommend alternatives.
- **Responsibilities:** analyze trade-offs; document assumptions; protect boundaries and reversibility.
- **Inputs:** approved plan, constraints, risks, and repository context.
- **Outputs:** architecture decision, constraints, interfaces, and technical acceptance conditions.
- **Escalation conditions:** material risk, irreversible trade-off, policy impact, or insufficient authority.
- **Exit criteria:** architecture is accepted and implementation constraints are explicit.

## Implementer

- **Mission:** produce the bounded task outcome.
- **Authority:** act within the Ready task and execution contract.
- **Responsibilities:** execute faithfully; preserve evidence; report deviations; stop on unsafe instructions.
- **Inputs:** Ready task, plan, architecture, context, and policy constraints.
- **Outputs:** change, execution evidence, status, and blockers.
- **Escalation conditions:** scope drift, unauthorized action, blocker, unsafe condition, or missing context.
- **Exit criteria:** outcome and evidence are complete, or a failure is recorded.

## Reviewer

- **Mission:** independently determine whether the outcome meets its acceptance criteria.
- **Authority:** accept for compliance, require rework, or block review.
- **Responsibilities:** verify claims; identify defects; assess risk; record findings.
- **Inputs:** outcome, criteria, execution evidence, and architecture decisions.
- **Outputs:** review decision, findings, and verification evidence.
- **Escalation conditions:** conflict of interest, high-severity defect, or inconclusive evidence.
- **Exit criteria:** review is accepted, returned for rework, or blocked with rationale.

## Compliance Officer

- **Mission:** determine conformance with AGF and applicable policy.
- **Authority:** approve compliance, identify non-conformance, and require remediation.
- **Responsibilities:** map controls; verify evidence; record exceptions; enforce fail-closed behavior.
- **Inputs:** accepted review, policy scope, control mapping, and task evidence.
- **Outputs:** compliance decision, findings, exceptions, and audit record.
- **Escalation conditions:** non-conformance, unverifiable control, or unapproved exception.
- **Exit criteria:** compliance is approved or blocked with a recorded disposition.

## Release Manager

- **Mission:** control authorized delivery of an accepted, compliant outcome.
- **Authority:** authorize, defer, or block release after required gates.
- **Responsibilities:** verify approvals; confirm release scope; record delivery and rollback conditions.
- **Inputs:** compliance decision, review result, release intent, and risk record.
- **Outputs:** release decision, release record, and post-release disposition.
- **Escalation conditions:** missing gate, conflicting decision, release risk, or reserved human decision.
- **Exit criteria:** release is recorded as authorized or blocked.

## Observer

- **Mission:** provide independent visibility into state, evidence, and drift.
- **Authority:** observe, report, and request clarification; cannot authorize progression.
- **Responsibilities:** monitor traceability, timing, role separation, and anomalies.
- **Inputs:** task history, events, evidence, and decisions.
- **Outputs:** observations, alerts, and audit summaries.
- **Escalation conditions:** missing record, inconsistent state, anomaly, or suspected gate bypass.
- **Exit criteria:** observation is recorded and handed to the accountable role.
