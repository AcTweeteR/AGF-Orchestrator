# Threat Model

This document describes the primary security and governance threats AGF-Orchestrator is designed to contain. It is informative; normative authority and lifecycle rules remain in the Constitution, decision model, policy, and protocol documents.

## Assets to protect

- owner/human authority and protected policy;
- project/session identity and scope;
- repository target and lineage;
- plans, architecture decisions, reviews, Compliance results, and delivery evidence;
- signing keys and credentials;
- external mutation boundaries such as Git push, PR creation, merge, deployment, or other privileged actions;
- persistent state needed for restart-safe recovery.

## Trust assumptions

AGF-Orchestrator does **not** assume that a model/provider is correct or trustworthy. Provider output may be mistaken, incomplete, stale, maliciously influenced, or inconsistent across calls.

The system does assume that the host, trusted cryptographic primitives, configured root-of-trust material, and explicitly owner-controlled actions are not already fully compromised. If the host/root of trust is compromised, AGF cannot guarantee authority integrity.

## Threat classes

### Provider self-authorization

A worker/provider attempts to broaden scope, skip a gate, change protected policy, or treat its own output as permission.

**Controls:** authority/execution separation, signed/bound authorization records, policy gates, allowed paths, fail-closed validation.

### Evidence fabrication or substitution

An actor fabricates a review, receipt, plan, target SHA, or evidence file, or substitutes an artifact from another session/project.

**Controls:** exact project/session/target binding, persisted hashes, signatures where required, canonical identity checks, independent review/Compliance, lineage verification.

### Replay and stale state

A previously valid authorization/checkpoint is replayed after the target, policy, session, or campaign state has advanced.

**Controls:** anti-replay checks, exact target/lineage binding, terminal-state semantics, stale-state detection, bounded recovery.

### Confused-deputy external mutation

A valid-looking provider result causes the orchestrator or an adapter to perform an external mutation outside the authority that produced the result.

**Controls:** explicit ExternalActionExecutor/policy boundary, separate delivery intent/receipt semantics, owner-controlled protected actions, no authority inferred from success.

### Retroactive authorization

An external change already happened and reconciliation incorrectly records it as if AGF had authorized the original action.

**Controls:** external-result acceptance is distinct from authorization; observed results can be accepted/reconciled without fabricating DeliveryIntent, receipt, or prior approval.

### Recovery escalation

Restart/retry logic wakes terminal work, resets bounded retry state without authority, or loses provenance while reconstructing state.

**Controls:** durable campaign state, terminal-state checks, explicit retry-reset semantics, atomic persistence, canonical session reconciliation, lineage preservation.

### Scope escape

Generated work modifies files, repositories, branches, or commands outside an approved task.

**Controls:** allowed-path verification, repository preflight, clean/named branch requirements, isolated worktrees, task-declared validation commands, no shell interpretation for provider invocation.

### Secret exposure

Credentials appear in commits, evidence, logs, model prompts, test fixtures, or managed repositories.

**Controls:** secrets remain outside version control, controlled dotenv loading, documentation prohibitions, redaction expectations, private security reporting.

### Provider or dependency compromise

A provider CLI, dependency, or local model runtime behaves maliciously.

**Controls:** provider neutrality, bounded adapters, explicit invocation, timeouts, post-execution scope verification, independent gates. This does not fully protect a host already compromised by privileged malware.

## Out of scope / residual risk

AGF-Orchestrator cannot guarantee safety when:

- the host OS or root-of-trust key material is compromised;
- an owner intentionally authorizes unsafe scope/action;
- external services violate their own security guarantees;
- arbitrary validation commands themselves are malicious and were explicitly authorized;
- users bypass AGF and mutate a target directly.

The system should still preserve the distinction between externally observed results and AGF-authorized actions when direct mutation occurs outside AGF.

## Security review expectations

Changes affecting authority, signatures, evidence identity, target/lineage binding, recovery, external actions, policy, or protected state should be treated as security-sensitive and should include adversarial regression coverage.
