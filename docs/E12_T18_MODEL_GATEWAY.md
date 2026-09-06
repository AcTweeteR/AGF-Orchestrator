# E12-T18 — Governed Model Gateway boundary and OmniRoute pilot

Status: PLANNED

Parent: Issue #164

Roadmap PR: #169

Source/reference provider: `diegosouzapw/OmniRoute`

## Objective

Separate model routing from execution-harness selection and from AGF's provider/model eligibility decision. AGF remains the authority that determines which providers/models are eligible; an optional model gateway may optimize routing, quotas, price and fallback only within that explicitly approved set.

Target layering:

`AGF Governor -> ExecutionHarnessProvider -> ModelGatewayProvider -> effective provider/model`

The layers are intentionally distinct:

- `ExecutionHarnessProvider`: how an authorized task is executed (for example Codex, FCC, DeepSeek Harness).
- `ModelGatewayProvider`: how an eligible inference request reaches one of the already-approved models/providers.
- AGF Provider Intelligence/policy: whether a provider/model is eligible at all.

## Scope

- Define a provider-neutral `ModelGatewayProvider` contract.
- Model gateway capabilities including:
  - model/provider catalog;
  - availability/health;
  - quota and rate-limit evidence;
  - price/cost evidence;
  - context-window and capability metadata;
  - routing and fallback support;
  - effective-model provenance;
  - usage/cost observations.
- AGF computes the eligible provider/model set before gateway invocation using existing capability, policy, privacy, health, budget, empirical-evidence, independence and risk gates.
- Gateway routing and fallback are allowed only inside that eligible set.
- Bind each routing decision/result to project, session, task, inference request, selected harness, model/provider profile version and relevant evidence freshness.
- Record the actual gateway, provider and model used for each completed inference, including fallback reason and usage/cost evidence where available.
- Forbid silent substitution to an unapproved provider, model, region, privacy class or capability level.
- Treat model-catalog drift, unprovable effective-model identity, stale health/quota evidence and contradictory routing evidence as fail-closed conditions.
- Preserve direct-provider operation so AGF remains functional with no gateway installed.
- Implement a disposable OmniRoute adapter/pilot without making OmniRoute a core runtime dependency.

## OmniRoute pilot

Compare at least:

1. direct eligible provider/model routing;
2. existing OpenRouter/FCC path where applicable;
3. OmniRoute as a governed model gateway.

The pilot should exercise:

- a free-model candidate path;
- a paid-model candidate path only when existing budget/policy permits;
- quota-triggered fallback inside the eligible set;
- attempted fallback outside the eligible set;
- gateway unavailability;
- catalog/model identity drift;
- a critical-session attempted model switch.

No production external mutation is required for this pilot.

## Required invariants

- OmniRoute cannot create work or decide task eligibility.
- OmniRoute cannot add candidates to AGF's eligible provider/model set.
- Cost or quota optimization cannot bypass capability, privacy, policy, risk, empirical-evidence or independence gates.
- Fallback outside the eligible set is blocked, not merely reported after the fact.
- An inference cannot be considered evidenced if the effective provider/model cannot be proven.
- A gateway cannot lower required model capabilities to maintain availability unless AGF has already declared that lower-capability candidate eligible for the task.
- Model changes during critical work cannot occur invisibly.
- Gateway failure can fall back to direct routing only when AGF policy explicitly permits it and the target remains independently eligible.
- Gateway telemetry is evidence, not authority.
- Core AGF operation has no OmniRoute dependency.

## Fail-closed canaries

- eligible set contains A/B; gateway attempts C -> BLOCKED;
- provider is eligible but model variant is not -> BLOCKED;
- effective model cannot be identified after response -> UNKNOWN/BLOCKED;
- gateway reports conflicting provider/model provenance -> BLOCKED;
- quota evidence is stale and fallback is required -> UNKNOWN/BLOCKED unless fresh evidence is obtained;
- privacy class changes through fallback -> BLOCKED;
- critical-session model substitution without compatible evidence -> BLOCKED/HUMAN_REQUIRED according to active policy;
- gateway unavailable with direct fallback disabled -> UNAVAILABLE/BLOCKED;
- gateway unavailable with direct fallback enabled and eligible -> direct path may proceed with new evidence lineage.

## Acceptance

- Provider-neutral contract contains no OmniRoute-specific core types.
- Exact effective gateway/provider/model is attributable for every successful pilot inference.
- Routing/fallback never expands AGF's approved candidate set.
- Existing Provider Intelligence remains the eligibility source rather than being duplicated.
- Existing budget, privacy, risk, evidence, kill-switch and HUMAN_REQUIRED behavior is preserved.
- Full pytest, Ruff and `git diff --check` pass for implementation PRs.
- Independent review and Compliance PASS under active policy before implementation is treated as complete.
