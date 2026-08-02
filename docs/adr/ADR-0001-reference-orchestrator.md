# ADR-0001: Reference orchestrator boundary

- **Status:** Accepted
- **Date:** 2026-08-02

## Context

AGF defines governance rules, while autonomous software-development agents need an operational model for coordination. Without a clear boundary, orchestration can accidentally become a second source of policy or become coupled to a single provider.

## Decision

AGF-Orchestrator is the vendor-neutral reference model for operational coordination. It owns task flow, role coordination, evidence movement, review sequencing, compliance gating, release control, and failure escalation. AGF remains the source of governance rules. Agent providers connect only through the interchangeable adapter boundary described in [ADAPTER_MODEL.md](../ADAPTER_MODEL.md).

The model is layered as described in [ARCHITECTURE.md](../ARCHITECTURE.md), uses the lifecycle in [TASK_MODEL.md](../TASK_MODEL.md), and fails closed when required authority or evidence is absent.

## Consequences

The model can be implemented by different providers and repository environments without changing governance semantics. Implementations must preserve the documented authority boundaries and produce auditable evidence. Provider-specific optimizations belong outside the normative model and cannot bypass Review, Compliance, or Release decisions.
