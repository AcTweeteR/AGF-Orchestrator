# E12 Roadmap — Governed capability extensions

Status: ACTIVE

Parent: Issue #164

ADR: `docs/adr/ADR-0004-governed-capability-extensions.md`

## Objective

Extend AGF-Orchestrator with reusable governed procedures, external capability and knowledge discovery, code-intelligence providers, browser validation, current technical documentation providers, pluggable execution harnesses, governed model gateways, governed external research, session resilience, and evidence ergonomics without creating new authority sources or coupling the governor to a specific vendor.

`kimi-k3-in-c` and local-model streaming/runtime work remain explicitly out of scope.

## Governing rules

- AGF remains the sole governor for task eligibility, risk, allowed paths, provider/tool selection, evidence, delivery, merge and escalation.
- Third-party repositories are sources of ideas, procedures, adapters or optional providers; they do not become authority sources.
- Optional integrations fail closed on missing, stale, contradictory, unauthenticated, privacy-ineligible or policy-ineligible evidence.
- No tool, skill, MCP server, browser, code-intelligence engine, documentation service, execution harness, model gateway or external-research adapter may expand `allowed_paths`, lower risk, clear a kill switch, authorize delivery/merge, satisfy `HUMAN_REQUIRED`, create work or fabricate evidence.
- Execution harnesses may execute only work already authorized by AGF and remain subordinate to AGF lineage, budgets and finite-progress rules.
- Model gateways may route/fallback only inside the provider/model set already declared eligible by AGF.
- External research is read-only by default; cookie/session reuse is sensitive credential/session access and cannot be silently enabled.
- AGF Desktop consumes these capabilities but does not own their governance logic.

## Sources and intended use

| Source | AGF use | Runtime dependency |
|---|---|---|
| `cobusgreyling/loop-engineering` | readiness/doctor, bounded loop patterns, cost/fleet observability | No |
| `mattpocock/skills` | source format and ideas for composable governed procedures | No |
| `public-apis/public-apis` | optional external capability discovery source | No |
| `PleasePrompto/notebooklm-mcp` | optional MCP knowledge-provider profile/pattern | No |
| `qwen-code-dev-bot/oh-my-cli` | session resilience, workspace trust, checkpoints, evidence ergonomics | No |
| `oraios/serena` | optional code-intelligence provider | Optional only |
| `wshobson/agents` | source catalog of candidate agents/skills/procedures | No |
| `microsoft/playwright-mcp` | optional browser/real-workflow validation provider | Optional only |
| `upstash/context7` | optional current-library/documentation knowledge provider | Optional only |
| `obra/superpowers` | source of disciplined development workflow patterns | No |
| `deepseek-ai/DeepSeek-Harness` | optional execution-harness provider and architecture reference | Optional only |
| `diegosouzapw/OmniRoute` | optional model-gateway provider and routing/fallback reference | Optional only |
| `Panniantong/Agent-Reach` | optional external-research/Internet-reach provider and source-access reference | Optional only |

## Delivered core

- E12-T1 — Architecture and schemas — Done via PRs #165/#166.
- E12-T2 — Mission readiness and doctor — Done via PR #167.
- E12-T3 — Governed procedure/skill registry — Done core via PR #167; explicit `SKILL.md` import ergonomics remains follow-up.
- E12-T4 — Governed loop patterns — Done via PR #167.
- E12-T5 — External capability catalog adapters — Done core via PR #168.
- E12-T6 — MCP tool/knowledge-provider boundary — Done core via PR #168.
- E12-T7 — NotebookLM MCP optional profile — Done core via PR #168.
- E12-T8 — Cost/fleet observability — Done core via PR #168.
- E12-T9 — End-to-end disposable pilot — Done core via PR #168.

## Remaining execution backlog

### E12-T10 — Session resilience, workspace trust and evidence ergonomics — PLANNED

- wrong-workspace resume protection;
- checkpoint/recovery lineage and stale-checkpoint rejection;
- governed undo/redo evaluation;
- evidence archives and run scorecards from existing evidence;
- doctor/preflight coverage for recovery/workspace/rollback state;
- protected governance plane inspired by `oh-my-cli` without runtime dependency.

### E12-T11 — Serena code-intelligence provider — PLANNED

- provider-neutral `CodeIntelligenceProvider` boundary;
- optional Serena adapter/profile;
- symbol/reference-aware repository intelligence and bounded precise-edit support;
- project/revision/freshness binding;
- fail closed on required ambiguous/stale intelligence;
- no path/risk/authority expansion.

### E12-T12 — Governed `wshobson/agents` catalog adapter — PLANNED

- ingest third-party entries as UNVERIFIED procedure candidates;
- require separate AGF governance envelopes;
- preserve source/version/hash provenance;
- deduplicate and fail closed on conflicts;
- no bulk import becomes automatically selectable.

### E12-T13 — Playwright browser/real-workflow validation provider — PLANNED

- provider-neutral `BrowserValidationProvider` boundary;
- project/task/environment-bound browser sessions;
- deterministic open/navigate/interact/assert evidence plans;
- host/environment/account safeguards;
- authenticated and mutating actions remain governed;
- failed/partial workflows produce FAIL/UNKNOWN, never inferred success.

### E12-T14 — Context7 current-documentation provider — PLANNED

- optional documentation/knowledge provider;
- library/package/version resolution and provenance;
- distinguish project-pinned version from latest documentation;
- cite retrieved current documentation for implementation/review;
- unavailable/ambiguous docs fail closed or use explicitly permitted fallback.

### E12-T15 — Superpowers workflow-pattern review — PLANNED

- review plan/build/verify, debugging, testing and review discipline;
- map only demonstrated gaps into existing ProcedureProfile/LoopPattern concepts;
- reject duplicated behavior;
- no second scheduler/governor/state machine;
- no runtime dependency.

### E12-T16 — Combined governed development-intelligence pilot — PLANNED

Disposable flow:

`Mission -> readiness/doctor -> governed procedure -> execution harness -> model gateway/provider -> Serena code intelligence -> Context7 docs when needed -> governed external research when needed -> implementation -> tests/review/Compliance -> Playwright validation when required -> evidence archive/scorecard -> delivery decision or HUMAN_REQUIRED`

Required canaries include wrong project/revision/environment, stale evidence, privacy/auth mismatch, model-gateway fallback outside eligibility, research-backend semantics drift, active kill switch, exhausted budget, unavailable provider/tool and contradictory evidence.

### E12-T17 — Pluggable execution-harness boundary and DeepSeek Harness pilot — PLANNED

Detailed scope:
- provider-neutral `ExecutionHarnessProvider` distinct from model/provider selection;
- capabilities for tools, sandbox, sessions, replay/resume, events, subagents, loops and scheduling primitives;
- bind invocation to project/session/task/plan hash/allowed paths/risk/provider evidence;
- keep harness state subordinate to AGF evidence and lineage;
- preserve AGF scheduler, risk, completion, delivery, merge, credential and kill-switch authority;
- compare Codex/FCC with a disposable DeepSeek Harness adapter/pilot;
- fail closed on developer-preview API drift or missing capabilities;
- no DeepSeek Harness runtime dependency for core AGF operation.

### E12-T18 — Governed Model Gateway boundary and OmniRoute pilot — PLANNED

Detailed design: `docs/E12_T18_MODEL_GATEWAY.md`.

- provider-neutral `ModelGatewayProvider`, distinct from execution-harness selection and model/provider eligibility;
- model catalog, provider health, quota, price, context limits, routing, fallback and usage provenance;
- AGF produces the eligible provider/model set first;
- gateway routing/fallback is permitted only inside that set;
- record actual gateway/provider/model, fallback reason and cost/usage evidence where available;
- forbid silent substitution to unapproved model/provider/region/privacy class;
- fail closed when the effective model cannot be proven or gateway evidence is stale/contradictory;
- disposable OmniRoute comparison against direct routing;
- no OmniRoute runtime dependency for core AGF operation.

### E12-T19 — Governed External Research / Internet Reach Provider — PLANNED

Detailed design: `docs/E12_T19_EXTERNAL_RESEARCH.md`.

- provider-neutral `ExternalResearchProvider`, distinct from browser validation, documentation, model gateways and execution harnesses;
- read-only by default;
- model source/platform, effective backend, auth/session/cookie requirements, network/privacy policy, timestamp, freshness and provenance;
- cookie/browser-session reuse treated as sensitive credential/session access;
- backend fallback allowed only inside AGF-approved source/privacy/authentication policy;
- fail closed if fallback changes credential, privacy, region, mutability or provenance semantics;
- bind requests/results to project/session/task/query intent/source/backend lineage;
- observational source-health/doctor evidence without remediation authority;
- disposable pilot across public web/search, GitHub, YouTube and optionally Reddit/X where policy-eligible;
- no Agent-Reach runtime dependency for core AGF operation.

## Recommended remaining order

1. E12-T10 — resilience/workspace trust/evidence ergonomics.
2. E12-T17 — execution-harness boundary.
3. E12-T18 — governed model-gateway boundary.
4. E12-T11 — Serena code intelligence.
5. E12-T14 — Context7 current documentation.
6. E12-T19 — governed external research.
7. E12-T13 — Playwright browser validation.
8. E12-T12 — `wshobson/agents` catalog adapter.
9. E12-T15 — Superpowers pattern review.
10. E12-T16 — combined disposable pilot.

## Scope freeze — finish AGF

After E12-T19, capability-discovery scope is frozen. New third-party repositories or attractive integrations are recorded for later review instead of expanding the active roadmap unless required to fix a demonstrated blocker, security defect or missing capability preventing an already-approved task from completion.

Priority is now execution and closure of the approved AGF roadmap, not further feature accumulation.

## Definition of Done

E12 is complete only when:

- each planned task has bounded implementation evidence;
- full pytest, Ruff and `git diff --check` pass for implementation PRs;
- independent review and Compliance pass under the active AGF policy;
- CRITICAL boundaries remain `HUMAN_REQUIRED` where policy requires;
- no new authority source, parallel policy engine, scheduler, credential store, merge path or audit-truth store is introduced;
- contradictory evidence blocks rather than being averaged away;
- disposable end-to-end canaries prove fail-closed behavior across procedures, providers, execution harnesses, model gateways, external research, code intelligence, documentation and browser validation.
