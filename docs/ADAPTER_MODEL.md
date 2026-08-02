# Adapter Model

An adapter is the boundary through which an interchangeable agent provider receives an authorized task context and returns a bounded result. The adapter does not own AGF policy, task transitions, review, compliance, or release decisions.

## Contract

An adapter must support attributable invocation, constrained context, explicit status, returned artifacts or references, evidence provenance, failure reporting, and safe termination. The orchestration model remains authoritative for authorization, state, and decisions.

Providers are interchangeable when they satisfy the same contract. Provider capabilities, credentials, data handling, reliability, and output limitations are evaluated as task constraints and recorded as evidence. No normative workflow depends on a provider name, proprietary feature, or provider-specific protocol.
