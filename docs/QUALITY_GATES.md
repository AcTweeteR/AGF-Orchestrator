# Quality Gates

Quality gates are mandatory transition checks over the single runtime lifecycle. The gate owner records the result and evidence. A gate is Pass, Rework, or Blocked; there is no implicit pass.

| Stage | Entry criteria | Exit criteria | Blocking conditions | Evidence required |
|---|---|---|---|---|
| Goal admission | Identifiable requester, objective, authority, constraints, and success condition | Project Context accepted by Director | Ambiguous goal, unknown repository, missing authority, or unresolved risk | Goal record, authority record, Project Context |
| Planning | Approved objective and scope | Planner package accepted by Director | Unbounded tasks, hidden dependencies, missing criteria, or unsafe parallelism | Epic map, task graph, dependency and parallelism rationale |
| Architecture | Planner package and applicable constraints | Architecture Context accepted by Architect and Director | Unresolved architecture, policy conflict, unnecessary complexity, or material unowned risk | Architecture decisions, alternatives, constraints, technical criteria |
| Ready dispatch | Accepted Architecture and complete Task Context | Implementer assigned and task marked Ready | Missing context, dependency, stop condition, or accountable owner | Task Context, handoff acceptance, dispatch record |
| Implementation | Ready task and accepted context versions | Review package complete | Scope drift, unauthorized action, failed validation, missing evidence, or stop condition | Outcome references, execution evidence, validation results, deviation record |
| Review | Complete Review Context inputs and independent Reviewer | Review accepted for Compliance or bounded rework recorded | Conflict of interest, critical defect, inconclusive evidence, or unreviewed criteria | Review record, findings, verification evidence, independence statement |
| Compliance | Accepted review and policy scope | Compliance approval or recorded non-conformance | Failed control, insufficient evidence, AGF conflict, or unapproved exception | Control mapping, evidence index, exception and audit record |
| Release readiness | Compliance approval, review result, release intent | Director approves readiness | Scope mismatch, unresolved risk, missing gate, or missing human decision | Release Context, readiness decision, residual risk record |
| Release authorization | Director readiness approval and complete Release Context | Release Manager authorizes publication or blocks | Dirty repository, version/changelog mismatch, missing record, or release risk | Cleanliness, version, changelog, publication, and final records |
| Done | Authorized release or explicit terminal disposition | Final record is durable and retrievable | Incomplete history, unrecorded outcome, or unresolved terminal state | Final task record, evidence index, decision history, observations |

Rework follows [RECOVERY_PROTOCOL.md](RECOVERY_PROTOCOL.md). Quality gates do not change role authority: the Reviewer owns quality, the Compliance Officer owns conformance, and the Release Manager owns publication authorization.
