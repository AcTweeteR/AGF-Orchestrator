# E12 Roadmap — Governed capability extensions

Status: ACTIVE

Parent: Issue #164

ADR: `docs/adr/ADR-0004-governed-capability-extensions.md`

## Objective

Extend AGF-Orchestrator with reusable governed procedures, external capability and knowledge discovery, code-intelligence providers, browser validation, current technical documentation providers, session resilience, and evidence ergonomics without creating new authority sources or coupling the governor to a specific vendor.

`kimi-k3-in-c` and local-model streaming/runtime work remain explicitly out of scope.

## Governing rules

- AGF remains the sole governor for task eligibility, risk, allowed paths, provider/tool selection, evidence, delivery, merge and escalation.
- Third-party repositories are sources of ideas, procedures, adapters or optional providers; they do not become authority sources.
- Optional integrations fail closed on missing, stale, contradictory, unauthenticated, privacy-ineligible or policy-ineligible evidence.
- No tool, skill, MCP server, browser, code-intelligence engine or documentation service may expand `allowed_paths`, lower risk, clear a kill switch, authorize delivery/merge, satisfy `HUMAN_REQUIRED`, or fabricate evidence.
- AGF Desktop consumes these capabilities but does not own their governance logic.

## Sources and intended use

| Source | AGF use | Runtime dependency |
|---|---|---|
| `cobusgreyling/loop-engineering` | readiness/doctor, bounded loop patterns, cost/fleet observability | No |
| `mattpocock/skills` | source format and ideas for composable governed procedures | No |
| `public-apis/public-apis` | optional external capability discovery source | No |
| `PleasePrompto/notebooklm-mcp` | optional MCP knowledge-provider profile/pattern | No |
| `qwen-code-dev-bot/oh-my-cli` | session resilience, workspace trust, checkpoints, evidence ergonomics | No |
| `oraios/serena` | optional code-intelligence provider for symbols/references/precise edits | Optional only |
| `wshobson/agents` | source catalog of candidate agents/skills/procedures | No |
| `microsoft/playwright-mcp` | optional browser/real-workflow validation provider | Optional only |
| `upstash/context7` | optional current-library/documentation knowledge provider | Optional only |
| `obra/superpowers` | source of disciplined development workflow patterns | No |

## Execution backlog

### E12-T1 — Architecture and schemas — Done

- ADR defining governed procedure/tool/knowledge-provider boundaries.
- Deterministic versioned `ProcedureProfile`, `ProcedureSelection`, `ToolCandidate`, and `KnowledgeProviderProfile` evidence schemas.
- Project/session bindings, deterministic hashes, freshness handling, unknown-field rejection and secret-shaped-data rejection.

Delivered through PR #166 after ADR PR #165.

### E12-T2 — Mission readiness and doctor — Done

- Deterministic readiness result derived from explicit persisted evidence.
- UNKNOWN remains blocking.
- Informational score cannot override a blocking gate.
- `doctor` diagnostics are observational and have no authority effect.

Delivered through PR #167.

### E12-T3 — Governed procedure/skill registry — Done

- Project-isolated reusable procedure registry.
- Capabilities, risk ceiling, allowed paths, provider requirements, required evidence, invocation policy, provenance/version/hash.
- Deterministic selection and fail-closed ambiguity/staleness behavior.

Delivered through PR #167. The residual `SKILL.md` import ergonomics follow-up is tracked separately as `Backlog` below.

### E12-T4 — Governed loop patterns — Done

- CI repair.
- PR babysitting.
- Issue triage.
- Dependency update.
- Release preparation.
- Patterns respect kill switch and finite-progress requirements and grant no auto-merge/external-mutation authority.

Delivered through PR #167.

### E12-T5 — External capability catalog adapters — Done

- Public-APIs-style rows become `UNVERIFIED` discovery candidates only.
- Catalog membership is never treated as eligibility evidence.
- Independent official-doc/auth/limits/license/privacy/stability/policy evidence remains required before use.

Delivered through PR #168.

### E12-T6 — MCP tool/knowledge-provider boundary — Done

- Provider-neutral MCP profile support.
- Transport, capabilities, auth/session, network, browser automation, privacy, mutability, stability and provenance modeled explicitly.
- MCP does not bypass external-action or policy controls.

Delivered through PR #168.

### E12-T7 — NotebookLM MCP optional profile — Done

- External/private, authenticated, networked, browser-automated, unofficial and privacy-review-required classification.
- Project material upload requires separate explicit authorization and privacy eligibility.
- Drift/unavailability degrades to unavailable/UNKNOWN.

Delivered through PR #168.

### E12-T8 — Cost/fleet observability — Done

- Read-only per-task/provider/procedure budget and remaining-capacity observations.
- Kill-switch visibility without new clear/stop authority.
- Cost ranking may only rank candidates already declared eligible upstream.

Delivered through PR #168.

### E12-T9 — End-to-end disposable pilot — Done

- Readiness -> procedure selection -> optional verified tool -> optional knowledge provider -> execution readiness.
- Canaries for budget exhaustion, kill switch, unverified catalog candidate and privacy-denied knowledge provider.
- No live network call or external mutation required for the deterministic pilot.

Delivered through PR #168.

### Follow-up register

- `E12-T3-SKILL-MD` — explicit `SKILL.md` import ergonomics — `Backlog`.

Residual follow-up is not implied to be complete by E12-T3.

### E12-T10 — Session resilience, workspace trust and evidence ergonomics — PLANNED

Inspired by `qwen-code-dev-bot/oh-my-cli`, but implemented in AGF-native form.

Scope:
- compare existing AGF recovery/session/evidence behavior against `oh-my-cli`;
- workspace trust-boundary checks preventing resumed work against the wrong repository/workspace;
- checkpoint/recovery with explicit lineage and stale-checkpoint rejection;
- evaluate governed undo/redo for reversible local mutations;
- exportable evidence archives derived from existing AGF evidence;
- run scorecards derived from evidence rather than model opinion;
- extend doctor/preflight for workspace mismatch, checkpoint staleness, broken recovery lineage and missing rollback capability;
- protect Constitution, policy roots, kill-switch authority, credential policy, merge authority and audit truth from autonomous/provider mutation.

Acceptance:
- wrong-workspace resume fails closed;
- stale/foreign checkpoints cannot continue a mission;
- undo/redo never rewrites external reality or bypasses external-result reconciliation;
- evidence archives are bounded, attributable, deterministic and secret-safe;
- no `oh-my-cli` runtime dependency.

Delivered evidence so far:
- workspace identity mismatch and unverifiable identity fail closed or remain `UNKNOWN`;
- archives are deterministic, size-bounded and reject secret-shaped evidence;
- scorecards contain only persisted session/evidence-derived facts;
- focused resilience and CLI tests pass;
- `session doctor` and `session archive` are read-only, project/session-bound
  and machine-readable with `--json`.

Undo/redo evaluation: `NO_JUSTIFIED_IMPLEMENTATION` is the current bounded
decision. AGF already has Git/worktree isolation, immutable artifacts and
external-result reconciliation. A second undo/redo store would duplicate Git,
could obscure remote provenance, and could not reverse pushes, PRs, merges or
other external reality. Local reversible mutation remains governed by the
existing worktree and delivery controls; this decision does not authorize any
rollback or external action.

### E12-T11 — Serena code-intelligence provider — Done

Source: `oraios/serena`.

Objective: let eligible providers obtain symbol/reference-aware repository intelligence and precise edit targets without making Serena an authority source.

Scope:
- provider-neutral advisory evidence boundary implemented in `agf_orchestrator.code_intelligence`;
- persistence reuses `SessionStore` artifacts; provider eligibility reuses `CapabilityProfile` and `CapabilitySelector`;
- define a provider-neutral `CodeIntelligenceProvider` capability boundary;
- optional Serena adapter/profile;
- operations such as symbol lookup, reference discovery and bounded code-navigation evidence;
- precise-edit assistance only after normal AGF task/risk/path/provider eligibility;
- freshness/project/repository binding on returned intelligence;
- fallback to existing repository understanding when Serena is unavailable and the capability is optional; block when code intelligence is explicitly required.

Acceptance:
- Serena cannot expand allowed paths or authorize edits;
- all symbol/reference results are project/revision bound and attributable;
- stale/indexed-against-wrong-revision intelligence cannot authorize current work;
- missing/ambiguous symbol evidence fails closed where required;
- disposable comparison shows reduced unnecessary file/context loading without weakening correctness gates.

Current implementation evidence:
- symbol, definition, references, navigation and bounded edit-target operations are represented as evidence, without direct editing;
- project, canonical repository, revision/index revision, requested operation/query and provenance bindings are hash-validated;
- stale, ambiguous, unavailable, malformed, mismatched and path-blocked outcomes remain distinct;
- empty scopes, malformed repository identities, traversal and non-recursive glob escapes fail closed;
- deterministic fixture comparison measures repository paths versus evidence-selected paths;
- no mandatory concrete-provider dependency is introduced; a concrete provider remains an optional adapter/profile under existing capability selection.
- final review corrected empty-scope acceptance, operation/query replay, non-valid efficiency evidence, malformed repository identity, and recursive glob handling;
- final gates: 803 full-suite tests, 39 focused boundary tests, Ruff PASS and `git diff --check` PASS; review threads resolved and CI PASS.

### E12-T12 — Governed agent/skill catalog adapter for `wshobson/agents` — Planned

Objective: treat large third-party agent/skill collections as candidate procedure sources, never as trusted autonomous roles.

Scope:
- catalog ingestion for agent/skill metadata;
- convert entries into `UNVERIFIED` procedure candidates;
- require a separate AGF governance envelope for capabilities, max risk, allowed paths, provider requirements, evidence requirements and invocation policy;
- provenance/hash/version pinning;
- deduplication and conflict detection across skill sources;
- candidate review before promotion into the project procedure registry.

Acceptance:
- imported prompt/skill text cannot grant itself permissions or authority;
- conflicting candidates do not select nondeterministically;
- provenance is preserved to exact source/version/hash;
- no repository-wide bulk import becomes automatically selectable;
- no runtime dependency on `wshobson/agents`.

### E12-T13 — Playwright browser/real-workflow validation provider — PLANNED

Source: `microsoft/playwright-mcp` and/or a provider-neutral Playwright adapter.

Objective: allow AGF to validate real user workflows in a browser rather than relying only on static code/tests.

Scope:
- `BrowserValidationProvider` boundary;
- browser sessions bound to project/task/test intent;
- deterministic validation plans: open, navigate, interact, assert result, collect bounded evidence;
- screenshots/snapshots/logs only as evidence, never as authority;
- network/domain allowlists and explicit treatment of authenticated sessions;
- default non-production/disposable targets;
- mutating browser actions classified through existing external-action policy.

Acceptance:
- a successful unit/integration test suite cannot substitute for required browser validation;
- browser validation cannot silently operate against an unintended host/environment/account;
- production mutation remains blocked without explicit policy/authority;
- failed/partial workflows produce failure/UNKNOWN, never inferred success;
- evidence is replayable enough to identify URL/step/assertion/revision without storing secrets.

### E12-T14 — Context7 current-documentation provider — Executing

Source: `upstash/context7`.

Objective: provide current library/API documentation to eligible providers so implementation does not rely only on potentially stale training knowledge.

Scope:
- model as an optional documentation/knowledge provider under the E12 MCP/provider boundary;
- library/package/version resolution;
- documentation provenance and retrieval timestamp;
- explicit distinction between project-pinned dependency version and latest documentation;
- citation/evidence references usable by planning/review;
- privacy/network/policy classification.

Acceptance:
- documentation for a different dependency version cannot silently satisfy current-project requirements;
- unavailable or ambiguous docs degrade to UNKNOWN/fallback rather than fabricated API knowledge;
- technical claims used for implementation/review remain attributable to retrieved documentation;
- Context7 has no authority over dependency upgrades or code changes.

Current implementation evidence:
- provider-neutral `DocumentationProvider` evidence binds package identity, declared/locked/resolved/runtime dependency sources, requested/returned topic, documentation version, normalized claims, source, citations, project/repository/revision and hash;
- version compatibility is assessed separately from freshness, with latest-versus-project-version mismatch failing closed;
- bounded citations, excerpt secret-safety, deterministic hashes, persistence, tamper/replay checks and claim-level contradictory-source reconciliation are covered by deterministic fixtures;
- existing `KnowledgeProviderProfile`, network/privacy classification and knowledge-provider eligibility are reused; no Context7 runtime dependency is introduced.

### E12-T15 — Superpowers workflow-pattern review and procedure extraction — PLANNED

Source: `obra/superpowers`.

Objective: extract only development-process patterns that improve AGF's governed procedures without importing a competing orchestration layer.

Scope:
- review plan/build/verify, debugging, review and test discipline patterns;
- map useful patterns onto existing `ProcedureProfile` and `LoopPattern` concepts;
- identify overlaps with current AGF planning, review, Compliance and finite-progress logic;
- add only procedures/gates that close demonstrated gaps.

Acceptance:
- no second scheduler/governor/state machine is introduced;
- duplicated behavior is rejected rather than layered twice;
- any imported procedure remains bounded by normal AGF risk/path/evidence/provider controls;
- no runtime dependency on `obra/superpowers`.

### E12-T16 — Combined governed development intelligence pilot — PLANNED

Objective: prove that the new capabilities compose safely and materially improve execution quality.

Disposable flow:

`Mission`
-> readiness/doctor
-> governed procedure/loop pattern
-> provider selection
-> Serena code intelligence when eligible
-> Context7 current documentation when needed
-> implementation
-> tests/review/Compliance
-> Playwright real-workflow validation when required
-> evidence archive/scorecard
-> delivery decision or `HUMAN_REQUIRED`.

Canaries:
- Serena index/revision mismatch;
- wrong-project skill candidate;
- ambiguous Context7 library/version;
- browser pointed at wrong host/environment;
- missing authentication/privacy evidence;
- kill switch active during validation;
- stale procedure evidence;
- budget exhausted;
- provider/tool unavailable;
- contradictory static-vs-browser validation evidence.

Acceptance:
- no component becomes an authority source;
- contradictory evidence blocks instead of being averaged away;
- all external/network actions remain governed;
- project/revision/environment bindings survive restart;
- pilot demonstrates measurable context-efficiency or validation coverage improvement without reducing safety gates.

## Priority

Recommended execution order for the remaining work:

1. E12-T10 — session resilience/workspace trust/evidence ergonomics.
2. E12-T11 — Serena code-intelligence provider.
3. E12-T14 — Context7 documentation provider.
4. E12-T13 — Playwright browser validation.
5. E12-T12 — `wshobson/agents` governed catalog adapter.
6. E12-T15 — Superpowers pattern review.
7. E12-T16 — combined disposable pilot.

This ordering first strengthens correctness and trust boundaries, then repository/documentation understanding, then real-workflow validation, then expands procedure supply.

## Definition of Done

E12 is complete only when:

- each planned task has bounded implementation evidence;
- full pytest, Ruff and `git diff --check` pass for implementation PRs;
- independent review and Compliance pass under the active AGF policy;
- CRITICAL boundaries remain `HUMAN_REQUIRED` where policy requires;
- no new authority source, parallel policy engine, scheduler, credential store, merge path or audit-truth store has been introduced;
- disposable end-to-end canaries prove fail-closed behavior across procedures, providers, code intelligence, documentation and browser validation.
