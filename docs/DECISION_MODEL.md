# Decision Model

Decision ownership is separate from task activity. A role may recommend a decision without owning it.

| Decision | Owner | Required evidence | Escalate when |
|---|---|---|---|
| Intent, objectives, reserved risk, and exceptional acceptance | Human | Stated objective and constraints | Ambiguous objective or material risk acceptance |
| Routing, prioritization, scope conflict, and operational disposition | Director | Plan status, dependencies, findings | Policy conflict, unbounded scope, or unresolved authority |
| Technical boundaries, trade-offs, and architecture fitness | Architect | Constraints, alternatives, rationale | Safety impact, irreversible trade-off, or missing authority |
| Quality acceptance, defects, and rework recommendation | Reviewer | Criteria, verification, findings | High-severity defect or inconclusive evidence |
| AGF and policy conformance, exceptions, and control adequacy | Compliance Officer | Policy mapping, review evidence, exception record | Non-conformance or any unapproved exception |

Release authorization belongs to the Release Manager after Compliance approval; the Release Manager cannot waive a compliance decision. The Director cannot convert a failed compliance decision into approval. Human authority is reserved for intent, exceptional risk, unresolved governance conflict, and decisions explicitly reserved by AGF.
