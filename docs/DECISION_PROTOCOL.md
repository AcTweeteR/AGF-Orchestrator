# Decision Protocol

This protocol defines the minimum record for any decision that changes scope, authority, risk, architecture, policy interpretation, task transition, parallelism, intervention, or release readiness.

## Required decision record

Every decision must include:

| Field | Requirement |
|---|---|
| Decision ID | Unique, stable identifier linked to the task or project |
| Context | The objective, state, constraints, and question requiring judgment |
| Evidence | Facts, artifacts, findings, and prior decisions supporting the choice |
| Alternatives | Viable options considered and why they remained available |
| Risks | Expected harms, uncertainties, reversibility, and affected controls |
| Chosen option | The selected action and its authority boundary |
| Expected outcome | Observable result and acceptance condition |
| Review trigger | Event or condition that requires the decision to be revisited |

## Ownership

The Director owns strategic scope, orchestration, parallelism, intervention routing, and release-readiness approval. The Planner owns decomposition proposals but not strategic approval. The Architect owns technical boundary decisions within scope. The Reviewer owns quality acceptance and rework findings. The Compliance Officer owns AGF and policy conformance. The Release Manager owns publication authorization after all gates pass. Human ownership is reserved for the conditions in [ESCALATION_PROTOCOL.md](ESCALATION_PROTOCOL.md).

No role may approve its own output when the workflow requires independent review. A recommendation is not a decision until the owner records the required fields. Decisions with missing evidence fail closed and follow the escalation protocol.
