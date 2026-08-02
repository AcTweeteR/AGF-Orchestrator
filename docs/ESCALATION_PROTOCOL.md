# Escalation Protocol

Escalation preserves autonomous operation by reserving human intervention for defined uncertainty and authority boundaries. Everything else remains with the accountable role and is resolved through normal workflow transitions.

## Mandatory human intervention

Human intervention **must** happen only when one of these conditions exists:

- an architectural decision is required;
- an AGF conflict is detected;
- security is uncertain;
- a destructive operation is proposed or unavoidable;
- repository identity, state, or authority is uncertain;
- policies conflict;
- required evidence is insufficient.

These conditions are exhaustive for the human-intervention gate. A normal blocker, failed quality review, scheduling issue, or routine rework remains autonomous unless it also creates one of the listed conditions.

## Procedure

1. The detecting role stops the affected transition and preserves existing evidence.
2. The detecting role creates an escalation record with task identity, condition, impact, evidence, requested decision, and safe alternatives.
3. The Director confirms scope and routes the escalation to the human decision owner.
4. The human records the decision using [DECISION_PROTOCOL.md](DECISION_PROTOCOL.md).
5. The Director updates the task boundary and resumes, returns, blocks, or rejects the task.
6. The affected gate is repeated when the decision changes inputs or risk.

No escalation record authorizes work by itself. Until disposition is recorded, the affected operation remains blocked.
