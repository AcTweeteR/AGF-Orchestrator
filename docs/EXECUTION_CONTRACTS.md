# Execution Contracts

This specification defines how the executable agent contracts interact. It does not add a workflow or change the frozen architecture, runtime, governance, or role authority. The role documents in [AGENT_ROLES.md](AGENT_ROLES.md), the runtime in [RUNTIME.md](RUNTIME.md), and the handoff rules in [HANDOFF_PROTOCOL.md](HANDOFF_PROTOCOL.md) remain authoritative.

## Contract registry

| Contract | Role | Receives | Produces | Next authority |
|---|---|---|---|---|
| [DIRECTOR.md](../contracts/DIRECTOR.md) | Director | Goal and Project Context | Director Decision | Planner, Architect, or human |
| [PLANNER.md](../contracts/PLANNER.md) | Planner | Approved Director Decision | Execution Plan | Architect or Director |
| [ARCHITECT.md](../contracts/ARCHITECT.md) | Architect | Accepted Execution Plan | Architecture Decision | Implementer or Director |
| [IMPLEMENTER.md](../contracts/IMPLEMENTER.md) | Implementer | Exactly one approved task | Implementation Report | Reviewer or Director |
| [REVIEWER.md](../contracts/REVIEWER.md) | Reviewer | One Implementation Report | Review Report | Compliance Officer, Implementer, or Director |
| [COMPLIANCE.md](../contracts/COMPLIANCE.md) | Compliance Officer | Accepted Review Report | AGF Compliance Report | Release Manager, Director, or human |
| [RELEASE_MANAGER.md](../contracts/RELEASE_MANAGER.md) | Release Manager | Approved compliance and readiness package | Release Readiness | Done, Director, or human |

## Invocation contract

Every invocation has exactly one contract version, one role identity, one request ID, one task or project ID, one input object, one context version set, and one requested action. The invoker rejects an invocation with extra authority, missing required fields, multiple tasks where one is required, stale context, or an unrecognized status.

Every response contains the common fields `Status`, `Evidence`, `Next Role`, and `Blocking Issues`, plus the contract-specific output fields. A response is invalid when it contains free-text instructions in place of structured fields, omits a required output, or names a next role not permitted by the contract.

## Deterministic interaction rules

1. Validate the input schema and context versions before role reasoning.
2. Apply the contract's mandatory preconditions in listed order.
3. Apply reasoning rules only to supplied structured inputs and referenced evidence.
4. Apply decision rules in the stated precedence: invalid input, blocking condition, escalation, rejection, approval.
5. Emit exactly one terminal status for the invocation and the required evidence list.
6. Create a [HANDOFF_PROTOCOL.md](HANDOFF_PROTOCOL.md) record before invoking the next role.
7. The receiver accepts or rejects the handoff; no context is inferred or silently repaired.
8. A rejected, blocked, or escalated response follows [RECOVERY_PROTOCOL.md](RECOVERY_PROTOCOL.md) or [ESCALATION_PROTOCOL.md](ESCALATION_PROTOCOL.md); it never advances as approved.

## Interaction graph

The dependency graph is acyclic:

**Director → Planner → Architect → Implementer → Reviewer → Compliance Officer → Release Manager → Done**

The Director may receive a failure or escalation from any role and may route a task back to an earlier owner. Such a return is a recorded recovery edge, not a new authority path. The Reviewer cannot invoke the Implementer directly without a Director-coordinated handoff; the Compliance Officer cannot invoke Release Manager without an approved compliance result.

## Common output schema

All seven contracts end with a machine-readable schema containing these keys:

```yaml
Input: structured_contract_input
Output: structured_contract_output
Status: enum
Evidence: list
Next Role: enum
Blocking Issues: list
```

Contract-specific fields are mandatory in addition to these common keys. `Status` is never inferred from prose. A missing required field is `BLOCKED`; a governance condition is `ESCALATED`; a failed quality or policy condition uses the contract's defined rejection status.

## Non-invention and ownership

No role may invent context, assume an omitted value, reinterpret another role's decision, or add an undocumented transition. The Director is the only strategic agent. The Architect owns technical decisions, the Implementer executes one task, the Reviewer owns quality, the Compliance Officer owns conformance, and the Release Manager owns publication authorization. This contract layer describes invocation and data exchange only.

## Completion

An execution chain completes only when Release Manager returns an approved Release Readiness and the final release record is accepted as Done. It terminates without Done when a response is `REJECTED`, `BLOCKED`, or `ESCALATED` and the corresponding disposition is recorded. No contract calls itself, and no pipeline may continue after a blocking response.
