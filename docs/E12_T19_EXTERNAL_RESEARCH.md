# E12-T19 — Governed External Research / Internet Reach Provider

Status: PLANNED

Parent: Issue #164

Source/reference: `Panniantong/Agent-Reach`

## Objective

Define a provider-neutral external-research boundary so AGF can use web, GitHub, RSS, YouTube, Reddit/X and other supported sources without allowing an Internet-access tool to become an authority source or silently change privacy/authentication semantics.

## Architectural boundary

`AGF -> ExternalResearchProvider -> source/backend`

AGF remains authoritative for task eligibility, source eligibility, privacy, credentials/session use, network policy, evidence acceptance, risk, budgets, completion and escalation.

The provider may retrieve evidence only within the scope explicitly authorized by AGF. It cannot create work, widen scope, lower risk, authorize mutations, satisfy `HUMAN_REQUIRED`, or fabricate success.

## Scope

- Define `ExternalResearchProvider` distinct from browser-validation, documentation, model-gateway and execution-harness providers.
- Model source/platform capabilities and backend implementations explicitly.
- Default to read-only research.
- Represent authentication, browser-session and cookie requirements as sensitive external-session evidence.
- Bind every research request/result to project, session, task, query intent, source, backend, timestamp and provenance.
- Track freshness and bounded confidence/quality evidence without converting heuristic scores into authority.
- Allow fallback between backends only when the fallback remains within the AGF-approved source/privacy/authentication policy.
- Treat a backend change that alters credential, cookie, region, privacy or mutability semantics as a new eligibility decision.
- Add source-specific health/doctor observations without granting remediation or authority.
- Enforce network/domain/source allowlists and bounded output sizes.
- Preserve citations/source references where available.
- No requirement that Agent-Reach be installed for AGF core operation.

## Agent-Reach pilot

Disposable pilot should exercise a subset of sources that can be validated safely, preferably:

- public web/search;
- GitHub public content;
- YouTube public metadata/transcript where available;
- Reddit or X only when access method is explicitly eligible.

The pilot must not require production credentials, paid actions, posting, messaging, liking, following, account mutation or unrestricted cookie export.

## Fail-closed canaries

- requested source not in the AGF-approved source set;
- fallback changes from anonymous/public access to authenticated-cookie access;
- stale/unhealthy backend;
- result provenance missing or contradictory;
- project/session/task mismatch;
- redirect to an unapproved domain/source;
- provider attempts a mutating action during a read-only research task;
- cookie/session material appears in retained evidence;
- source becomes unavailable mid-task;
- two backends disagree materially and no reconciliation rule exists.

## Acceptance

- Agent-Reach cannot decide what AGF is allowed to research.
- Read-only is the default and mutation is blocked unless separately governed through existing external-action policy.
- Cookie/session reuse is classified as sensitive credential/session access and never silently enabled.
- Backend fallback cannot weaken privacy, authentication, provenance or policy constraints.
- Every accepted result is attributable to its effective source/backend and retrieval time.
- UNKNOWN/contradictory evidence remains blocking where the research result is required for a decision.
- Core AGF remains functional when Agent-Reach is absent.
- Full pytest, Ruff, `git diff --check`, independent review and Compliance must pass for implementation PRs.

## Scope freeze after T19

E12 capability discovery is considered frozen after T19 unless a new item is required to fix a demonstrated blocker, security defect or missing capability that prevents an existing roadmap task from completing. New interesting third-party repositories should be recorded for later review rather than expanding the active completion path.

The execution priority after this documentation PR is to finish the already-approved AGF roadmap rather than continue adding integrations.
