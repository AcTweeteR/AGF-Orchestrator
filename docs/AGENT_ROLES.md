# Agent Roles

These role specifications are normative. Each role has a distinct decision boundary. A deployment may assign multiple roles to one person or agent only where the separation-of-duties requirements in [DECISION_PROTOCOL.md](DECISION_PROTOCOL.md) remain satisfied.

- [Director](DIRECTOR.md) — strategic coordination and release-readiness approval.
- [Planner](PLANNER.md) — objective decomposition and dependency planning.
- [Architect](ARCHITECT_ROLE.md) — technical boundaries and long-term consistency.
- [Implementer](IMPLEMENTER.md) — execution of exactly one authorized task.
- [Reviewer](REVIEWER.md) — independent quality evaluation.
- [Compliance Officer](COMPLIANCE_OFFICER.md) — AGF and policy conformance.
- [Release Manager](RELEASE_MANAGER.md) — release readiness and publication authorization.

## Observer

- **Mission:** provide independent visibility into task state, evidence, timing, and governance drift.
- **Authority:** observe, report, and request clarification; cannot authorize progression or alter task artifacts.
- **Inputs:** task history, transitions, evidence, decisions, and gate results.
- **Outputs:** observations, anomaly reports, traceability summaries, and escalation records.
- **Responsibilities:** monitor role separation, state consistency, evidence completeness, and suspected gate bypass.
- **Success criteria:** observations are attributable, factual, timely, and routed to the accountable role.
- **Failure criteria:** a missing, inconsistent, or anomalous condition is not recorded or is presented as an authorization.
- **Escalation rules:** report anomalies to the Director; route AGF, security, policy, or evidence concerns through [ESCALATION_PROTOCOL.md](ESCALATION_PROTOCOL.md).

The [DECISION_MODEL.md](DECISION_MODEL.md) is the canonical cross-role decision map. The [WORKFLOW_PROTOCOL.md](WORKFLOW_PROTOCOL.md) is the canonical sequence in which roles exchange artifacts.
