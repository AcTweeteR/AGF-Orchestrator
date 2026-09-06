# Completion audit — 2026-09-06

This is an evidence checkpoint, not a completion certificate. FACT means an
observed result; INFERENCE means a conclusion requiring further verification;
UNKNOWN means evidence is missing. No roadmap item is closed by this report.

## A. Git and GitHub

- FACT: remote `main` was `0b8c5a0`; release `v0.1.0` was published on August 26.
- FACT: the initial checkout was `be95b1c` on `codex/e12-t14-documentation`,
  with pre-existing edits in the E12 roadmap, documentation implementation and
  documentation tests. These edits were preserved. Corrections use a separate
  worktree based on remote main.
- FACT: open PRs were #169, #170, #171, #172, #173, #176, #177, #179 and #184;
  open issues were #161 and #164. Several PRs target intermediate branches.
- FACT: main requires the `validate` check with strict freshness. GitHub's
  required approving review count is zero; this does not waive AGF review or
  Compliance requirements. The registered AGF project requires human merge.
- FACT: issue #161 still lists default-branch, protection and initial-release
  actions that have already occurred. Other checklist items need separate
  evidence before reconciliation. There were no open milestones.
- FACT: GitHub reports four open dependency advisories: cryptography (high),
  wheel (high) and setuptools (high and medium). Fix versions reported by the
  advisories are cryptography 49.0.0, wheel 0.46.2 and setuptools 78.1.1/83.0.0.
  PRs #184, #176 and #177 address these dependencies, but main remains exposed
  according to the dependency alerts. Their current CI is green; combined
  compatibility and complete build/CI pin reconciliation still need review.

## B. Architecture observed

- FACT: the core has distinct execution, review, Compliance, delivery, session
  persistence, evidence reconciliation, scheduler and campaign-runner modules.
- FACT: the basic `Director.create_plan` defaults to `MockAdapter`; the session
  assessment/Architect path is separate. The basic demo is not proof of live
  autonomous engineering.
- FACT: Codex, OpenHands and Ollama adapters exist. No explicit FCC adapter or
  persisted FCC execution contract exists on the audited main.
- FACT: FCC's installed launcher supplies ephemeral Codex provider overrides.
  See [provider configuration and resume](PROVIDER_CONFIGURATION.md).

## C. Verified capabilities and baseline

- FACT: the unmodified initial checkout failed pytest collection because
  `test_documentation.py` issues a binding at import time, before isolation
  fixtures, and attempts to write the owner's authority store.
- FACT: without source changes, setting `AGF_STATE_DIR` to a disposable
  directory produced **876 passing tests** on that checkout (Python 3.14).
- FACT: the initial Ruff check and full-history audit passed: 449 reachable
  commits and 998 unique blobs at the time of that audit.
- FACT: remote main's unmodified code produced **794 passing tests** locally.
  CI uses Python 3.12; local results are not a substitute for its checks.
- FACT: tests cover scope, validation, delivery, recovery, policy and evidence
  failure paths. Fixture-based provider success is not live-provider evidence.

## D. Partial capabilities

- FACT: nonempty task dependencies are rejected by `executor._validate_gates`,
  also used by delivery. Representing a dependency graph does not make it
  executable with verified predecessor results.
- FACT: directory scopes are interpreted differently by Executor and delivery,
  Reviewer, Compliance and Git delivery. A shared contract needs architectural
  treatment before any broader path acceptance is introduced.
- FACT: documentation and canonical eligibility changes are in separate open
  PRs, not main. Historical findings require validation against their current
  heads; a green CI run alone does not resolve them.
- INFERENCE: a generic campaign callback running beyond its lease can overlap
  another worker. The daemon's shorter timeout mitigates its normal path, but
  does not prove the generic API safe. A deterministic concurrency reproduction
  is required before choosing a fix.

## E. Missing mission evidence

- UNKNOWN: one real-provider mission exercising discovery, planning, bounded
  execution, independent review, correction, reconciliation, interruption,
  recovery and completion has not been demonstrated by this audit.
- UNKNOWN: resumed FCC sessions' effective upstream model/routing provenance
  is not proven by successful Codex configuration loading.
- FACT: optional capability profiles and isolated tests do not demonstrate
  concrete browser, knowledge, harness or gateway integrations end to end.

## F. Confirmed defects and bounded corrections

1. **P1 — premature delivery success.** `_run_attempt` returned an already
   constructed successful result before `finally` checked cleanup and caller
   cleanliness. Three regression cases failed before the fix: cleanup failure,
   dirty caller and unreadable caller status. Results now finalize afterward;
   failure prevents delivery while captured patch evidence remains on disk.
   The positive control also passes. Independent Reviewer: APPROVE, no findings.
2. **P2 — lost Codex configuration home.** The environment allowlist discarded
   `CODEX_HOME`. A child-process regression exited 7 before correction and
   passed afterward. Preserving the host-selected configuration home does not
   forward arbitrary environment variables or introduce provider selection in
   the core. Independent Reviewer: APPROVE, no findings.
3. **FCC resume configuration.** An installed launcher selects a temporary
   provider definition, while sessions retain its ID. Codex 0.149.1 reproduced
   the missing-provider failure through `config/read`, without inference.
   Registering the same transport in user configuration resolves loading.
   Local repair retains the original config backup, provider default and
   session records. This is not proof of upstream service availability.
   A subsequent TCP probe of the configured loopback endpoint was refused;
   no live-provider success is claimed.

Candidate validation: **799 tests passed**; Ruff, changed-document relative
links and `git diff --check` passed. The refreshed full-history audit passed
with 484 reachable commits and 1,031 unique blobs. Independent Compliance
Officer: PASS for the bounded diff, with 58 relevant tests independently run.
These local results precede commit-specific remote CI and do not authorize
merge or certify the overall mission complete.

## G. Remaining completion plan

1. Deliver independently reviewed, tested corrections through the existing
   human merge requirement; do not self-activate or weaken policy.
2. Fix import-time test authority writes in their owning PR and reconcile the
   canonical provider eligibility prerequisite against all current findings.
3. Specify and implement predecessor-evidence handling, consistent path scopes
   and bounded concurrent recovery without trusting provider assertions.
4. Establish the subordinate harness/gateway contract, including effective
   configuration identity, provider availability and recovery provenance.
5. Run a disposable real-provider campaign with failure, retry, crash/resume,
   HUMAN_REQUIRED and no-justified-work scenarios. Preserve observed failures.
6. Reconcile roadmap, issues, dependent PRs and documentation against merged
   evidence. Run final independent code-first audit and all required gates.

AGF is not declared SUCCESS or NO_JUSTIFIED_WORK by this checkpoint.
