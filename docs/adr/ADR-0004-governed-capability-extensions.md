# ADR-0004: Governed capability extensions

Status: Proposed for E12 implementation

Issue: #164

## Context

AGF-Orchestrator already contains a completed dynamic capability-discovery and Provider Intelligence substrate. New work should extend that substrate rather than create parallel registries or transfer authority to third-party agent frameworks, skills, catalogs, MCP servers, or hosted research systems.

Several external projects expose useful patterns:

- composable execution procedures/skills;
- loop readiness, diagnostics, bounded loop patterns and cost/fleet observability;
- public API catalogs for capability discovery;
- MCP-accessible knowledge/research services.

These are useful as sources of ideas or optional integrations, but none is trusted as an authority source and none is required for the core governor to function.

`kimi-k3-in-c` and local-model streaming/runtime work are explicitly outside this decision.

## Decision

AGF will add four provider-neutral extension boundaries under the existing Constitution, policy, risk, scheduler, evidence, Provider Intelligence and external-action controls.

### 1. Governed procedures

A procedure is a versioned, attributable description of how a bounded class of work may be attempted. It may declare capabilities, risk ceiling, allowed-path constraints, provider requirements, required evidence and invocation policy.

A procedure never grants authority. Selection of a procedure cannot make an otherwise forbidden task eligible, expand allowed paths, lower risk, clear a kill switch, authorize delivery, satisfy HUMAN_REQUIRED, or create external-action permission.

Reusable loop patterns are compositions of governed procedures and inherit the same constraints.

### 2. Readiness and doctor diagnostics

AGF will expose deterministic mission/readiness diagnostics derived only from persisted AGF state and evidence. Diagnostics may report blockers and remediation suggestions but are observational: they cannot mutate protected state or authorize execution.

Blocking gates remain blocking regardless of any informational score. UNKNOWN, stale, ambiguous, contradictory or unavailable evidence fails closed at the relevant boundary.

### 3. External capability catalogs

External catalogs may contribute unverified discovery candidates to the existing E9 capability-discovery flow. A catalog entry is not evidence that an API/service is safe, available or eligible.

Before a candidate becomes selectable, AGF must have bounded evidence for the applicable official documentation, authentication model, limits, licensing, privacy posture, stability/availability and policy eligibility. Missing or conflicting evidence remains UNKNOWN and cannot be inferred as safe.

The first reference adapter may consume public-apis-style catalog data, but the architecture is catalog-neutral and the core implementation must be testable from deterministic fixtures without live network access.

### 4. MCP tool and knowledge providers

MCP servers are modeled as optional tool/knowledge providers. Their profiles may describe transport, capabilities, credential/session requirements, network requirement, browser automation, privacy classification, mutability, provenance and freshness.

MCP does not become an authority source. Mutating external operations remain subject to the existing external-action and policy boundaries. Credential use, browser automation, upload of project material, paid actions and privacy-sensitive transfers require the applicable existing authorization/policy evidence.

A NotebookLM MCP integration may be supplied only as an optional profile/example. Its default classification is external service, network required, authenticated Google session, browser automation, unofficial integration and privacy review required. Unavailability or integration drift degrades to unavailable/UNKNOWN rather than inferred success.

## Reuse of existing AGF components

E12 must reuse and compose the existing capability profiles, capability selection/invalidation, Provider Intelligence, scheduler budgets/limits, risk engine, kill switch, evidence stores, review/Compliance gates and external-action executor where applicable.

No parallel authority graph, provider registry, scheduler, policy engine, merge path or credential store may be introduced.

## AGF Desktop boundary

All governance and selection logic defined here belongs in AGF-Orchestrator. AGF Desktop may display readiness, doctor findings, selected procedures, providers, costs, tool/knowledge-provider status and kill-switch state, and may invoke already-defined AGF commands/APIs. Desktop does not implement or own the rules in this ADR.

## Security and privacy invariants

- Third-party procedure text, catalog entries and MCP metadata are untrusted input.
- Secret-shaped values must not be persisted in public procedure/catalog profiles or diagnostic reports.
- Uploading repository or project material to an external knowledge service is denied unless explicitly authorized and privacy-eligible.
- Stale capability/procedure/provider evidence cannot authorize current work.
- External tool unavailability cannot be converted into success or silently bypass a required capability.
- Kill-switch state and HUMAN_REQUIRED boundaries cannot be weakened by cost, skill or tool selection.

## Implementation sequence

Issue #164 defines bounded tasks E12-T1 through E12-T9. Each task is implemented and validated independently. CRITICAL changes remain HUMAN_REQUIRED under the active policy.

## Consequences

Positive:

- AGF gains reusable execution procedures without becoming prompt-framework dependent.
- Existing E9 discovery can ingest broader capability sources without trusting them blindly.
- MCP becomes a first-class optional integration boundary while preserving governance.
- Desktop can expose richer status without duplicating governor logic.

Costs and risks:

- More profile types and evidence lifecycles increase validation complexity.
- Third-party integrations introduce availability, privacy and provenance risks that must remain fail-closed.
- Procedure composition can create hidden scope expansion unless constraints are intersected rather than unioned.

## Rejected alternatives

- Vendor `loop-engineering`, `mattpocock/skills`, `public-apis` or `notebooklm-mcp` directly into the AGF core: rejected because it would create unnecessary coupling and supply-chain/runtime dependencies.
- Let procedure/skill metadata authorize work: rejected because procedure knowledge is not authority.
- Treat public API catalog membership as eligibility evidence: rejected because catalog presence does not establish security, licensing, stability or policy compliance.
- Allow MCP servers to bypass the ExternalActionExecutor for convenience: rejected because it would create a parallel mutation path.
- Implement this in AGF Desktop: rejected because governance must remain reusable without the GUI.
