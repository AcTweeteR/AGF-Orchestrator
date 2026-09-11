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
- FACT: a node-ID comparison explains the 876 versus 799 totals: the initial
  checkout has 73 documentation tests and nine code-intelligence tests absent
  from main; the initial PR #185 candidate adds four delivery cases and one
  adapter case. Thus `876 = 794 + 82` and `799 = 794 + 5`. No tests were removed
  to obtain the lower total; these are different branch contents.
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
   Subsequent GitHub review found a P1: relative `CODEX_HOME` could become a
   trusted config location inside the child worktree. Five regression cases
   reproduced this; relative/empty/unexpanded-tilde values now fail closed
   before any Codex subprocess. Independent Reviewer and Compliance Officer
   approved the follow-up; 32 adapter tests and 804 full-suite tests passed.
   Commit-specific CI remains required.
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


## Integration checkpoint — 2026-09-07

This checkpoint supersedes the current-state claims above while retaining the
initial baseline as historical evidence. It does not certify mission completion.

- FACT: GitHub reports #185 merged to main at `0f286086`, #187 merged to main
  at `776d56aa`, and #186 merged to the documentation branch at `c12d0f5a`.
  The expected commits are ancestors of their target branches. The latest
  main validation run, `34059993879`, passed; the documentation target run,
  `34060000192`, passed. Main immediately after #185 had one failing freshness
  test (`34059987528`); its real-clock timestamp race is corrected in this
  candidate using deterministic file timestamps, preserving the stale-output gate.
- FACT: dependency alerts now report all four advisories fixed. Superseded
  dependency PRs #176, #177 and #184 are closed. Issues #161 and #164 remain
  open; neither a release nor the presence of capability modules closes them.
- FACT: the integration candidate preserves the ancestry of #169, #170, #171,
  #172 (including #186), #173 and #179. It reconciles their roadmap conflicts
  without dropping the newer harness, gateway and research scope. These PRs
  remain pending until reviewed integration reaches their intended target.
- FACT: documentation test collection no longer creates authority state or
  replaces verification functions globally. The canonical prerequisite's
  richer tests use per-test authority fixtures; the subprocess import test
  verifies no import-time state mutation. The earlier 876/799 comparison is
  explained in section C; later totals include additional merged tests.
- FACT: owner-worker startup failure now preserves the original typed failure
  instead of masking it with `join` on an unstarted process. Both IPC endpoints
  are closed; lack of an operational owner attestor still fails closed.
- FACT: an expired campaign lease allowed a second runner to invoke work while
  the first callback remained active. A deterministic reproduction failed before
  correction. A per-campaign OS lock now spans the complete tick, including
  probes, invocation and persistence. A real child-process exit test verifies
  that restart respects the outstanding lease, then continues with the same
  lineage. This is local POSIX exclusion, not distributed or exactly-once
  execution; external side effects require reconciliation after interruption.
- FACT: independent Reviewer approved the startup, isolation, freshness and
  campaign-lock corrections. Compliance passed the final controls inspected,
  including 19 independently executed campaign tests; Reviewer independently
  ran 32 runner/daemon tests. The protected workflow changes retain human merge.
- FACT: the documentation site builds with `mkdocs build --strict`. Publication
  permissions belong only to the main-push deployment job; PR builds do not
  receive Pages write or OIDC permissions. The workflow publishes documentation
  on applicable main pushes after human integration.

### Live transport evidence and limits

- FACT: with the loopback FCC service available, an actual Codex 0.149.1
  invocation through the AGF Codex adapter returned `AGF_FCC_CANARY_OK`, exit 0,
  with verified final-message transport in a read-only sandbox.
- FACT: a subsequent actual CLI resume of the same persisted session returned
  `AGF_FCC_RESUME_OK`, exit 0, without missing-provider errors. FCC's existing
  catalog converter generated the isolated Codex catalog; this removed the
  model-list decoding error without adding catalog conversion to AGF.
- FACT: these diagnostic invocations used a locally configured model that the
  existing provider-validation record does not qualify as primary or fallback
  for governed engineering. Text success does not promote provider eligibility.
- FACT: the owner-level configuration repair retains the provider default,
  session history and backup; no credentials or host-specific configuration
  are committed here. The failure can recur for any persisted custom provider
  whose definition exists only in ephemeral invocation overrides.
- UNKNOWN: a full live AGF engineering campaign has not been demonstrated.
  Read-only text inference and CLI resume do not establish planning, bounded
  modification, independent review, correction or campaign completion.

### Independent code-first mission audit

Removing the roadmap does not make the implementation complete. Independent
inspection confirms these remaining implementation gaps:

1. Executor and delivery block every nonempty dependency list rather than
   verifying completed predecessor evidence.
2. Directory scope matching differs between Executor and delivery/review/
   Compliance; changing acceptance requires an explicit consistent contract.
3. Session handling maps `NO_JUSTIFIED_WORK` to `BLOCKED`, so the mission's
   terminal outcomes are not yet represented distinctly throughout the flow.
4. Objective completion criteria are stored and validated structurally, but
   have no evidence-based evaluator connected to global completion.
5. Session resume authorizes a phase; it does not execute that phase. The
   generic campaign driver depends on an external work command. A complete
   governed continuation path still needs implementation and demonstration.
6. Canonical eligibility has a data-only external owner client, but no
   operational owner signer or CLI/session wiring. The old callback process
   was removed after the open #173 finding was independently reproduced;
   see the owner issuance protocol for remaining operator obligations.

The first five items are engineering work, not missing credentials. Separately,
production authority activation and protected integration remain owner actions.
Do not bootstrap authority from test fixtures or claim that nominal module
coverage completes the mission. Continue after human integration with these
contracts, their regression tests, and a genuinely owner-provisioned live target.


### Subsequent review corrections

The pending GitHub review exposed a confirmed P1 in #173: a caller-supplied
callback ran with issuance state and could obtain an extra signed subject.
Verifying only the envelope returned to the parent did not detect that side
effect. The runtime callback/registry/fork mechanism is now removed. A bounded
data-only socket client consumes an independently issued owner envelope and
verifies its exact subject through the existing trust root. Legacy callbacks
are rejected without execution. This supersedes the startup workaround above;
the runtime no longer needs `fork` or child-process permission for issuance.
The external service remains an owner integration requirement, not an activated
feature. The adapted 195 documentation tests and 12 additional real local IPC
tests pass; test signing material remains entirely in fixtures.

An actual failed transport probe timed out without a final message. Restoring
the valid isolated configuration and resuming the original Codex session
returned `AGF_FCC_RECOVERED_OK`, exit 0. This is transport recovery evidence,
not a complete AGF engineering campaign.


Reviewer subsequently approved the data-only owner client after 29 independent
relevant tests, including real local sockets. Compliance passed the client and
protocol without authorizing activation. The historical #172 findings have
matching version, freshness, claim, secret-screening and replay regressions in
the combined suite; obsolete standalone issuance-database paths were removed.
The final integration PR must remain subject to commit-specific CI and human
merge. No pending implementation gap is closed solely by this reconciliation.


Final local integration validation: **1,112 tests passed** (Python 3.14,
120.14 seconds), Ruff passed, strict documentation build passed, all eight
GitHub YAML files parsed, and the complete integration diff passed whitespace
checks. The pre-final-commit public-history audit passed for 499 commits and
1,060 unique blobs; CI repeats that audit against the pushed history. The
runtime has no configured typecheck gate. These results do not replace CI on
Python 3.12 or authorize human-reserved integration.


The first PR #188 CI run (`34124126202`) passed 1,111 tests and failed the
configuration-home forwarding test on a filesystem/Python clock race. The
follow-up fixes that test's invocation clock to a deterministic epoch; it does
not relax production freshness checks. All 32 adapter tests pass locally.
Docs and CodeQL passed on the first candidate; CI must pass on the follow-up
commit before human integration.


## Post-merge continuation of #188

GitHub confirms #188 merged to main at `c3cf7f2`. CI `34125039038`, CodeQL
`34125039064` and the documentation build/deployment `34125039015` passed.
All six incorporated branch heads were verified as ancestors of main. The
remaining intermediate PRs #170–#173 were closed as incorporated; #169 and
#179 had closed on integration. New dependency updates #189–#194 are separate
candidates and have not been accepted solely because they are automated.

CodeQL reported alert #1 in the public-history auditor. Independent inspection
found that matched credential bytes were never printed, but Git filenames
were printed and can themselves contain sensitive information. The continuation
candidate omits filenames from public findings, preserving category and blob
SHA for local investigation without weakening detection.

The continuation candidate uses the explicit path contract and distinct
no-work disposition in [ADR-0006](adr/ADR-0006-governed-completion-contracts.md).
These corrections remain subject to final tests, independent review and human
integration. The original checkout remains unchanged. No full mission E2E or
objective completion is asserted by these changes.

The dependency reader now admits execution from canonical integrated delivery
evidence. Real temporary Git repositories demonstrate an integrated predecessor,
isolated successor execution, two predecessor reconciliations and restart.
Negative cases cover missing integration, changed definitions and prerequisite
edges, cross-session reuse, tampered plans/intents/receipts and target drift.
The execution provider in these tests is simulated; this is not the live E2E.

Independent Compliance review reproduced two unsafe no-work entrances during
development: campaign text without assessment, and architecture selection metadata
without a validated response. The candidate removes the first entrance and requires
the bound request/response for the second. Invalid provider attempts cannot retain
a no-work disposition. Reviewer approved the corrected lot with 113 independent
focused tests; Compliance approved with 89 focused tests. Complete-suite and
remote CI evidence are recorded separately before integration.

The six new dependency PRs have passing checks and their diffs were inspected.
They contain routine version updates; all four Dependabot security alerts remain
fixed. #189 and #191 change packaging pins but omit the explicit CI bootstrap
pins, so those updates require coordinated handling. #194 updates several major
Action versions and requires supply-chain/runtime review. These PRs remain open
and are not silently incorporated into the behavioral correction.

Independent of roadmap labels, code inspection still finds no operational
Objective-to-evidence closure evaluator and no complete governed continuation
driver connecting session assessment, task selection, delivery, reconciliation
and final disposition. The live owner-issued provider endpoint and qualified
engineering-provider proof remain prerequisites for the complete live mission.
Existing FCC canary/resume/recovery evidence remains valid within its diagnostic
scope and does not fill those gaps.

Final local gate for this candidate: **1,145 tests passed** on Python 3.14;
Ruff, strict documentation build and whitespace validation passed. The public
history auditor passed over 508 commits and 1,086 unique blobs in the local
reference set. No tests were removed to obtain this result. The obsolete test
expectation that dependency execution was unavailable now checks the required
persisted-session gate; positive dependency behavior has separate integration
tests. This gate does not replace CI on the published commit or human integration.

## Post-merge continuation of #195

PR #195 is integrated at `7766d55`. Post-merge CI `34190159978` passed all
1,145 tests and the history audit (498 commits, 1,096 unique blobs in the CI
reference set). CodeQL `34190160005` and Docs `34190159965` also passed.
CodeQL alert #1 remained on main despite its absence on the PR reference.
Independent inspection confirmed a false positive: fixed labels and Git object
IDs reach output; matched bytes and filenames do not. Both privacy tests passed
independently. Only that alert was dismissed; the CodeQL rule remains enabled.

The next audit reproduced a public PR_READY-to-COMPLETED transition without
Objective acceptance evidence. The correction blocks that API and adds the
read-only `session audit-completion` command. Canonical integrated deliveries
establish plan-work integration, including retained lineage; they cannot
authenticate Objective approval or criterion coverage. Removing historical work
or changing its definition or prerequisite edges fails full-plan verification.
The diagnostic never returns SUCCESS and leaves session state unchanged.

Objective `status=APPROVED` is currently only a JSON value. No installed component
authenticates it, and no approved mapping connects free-text criteria to evidence.
Adding one without an owner decision would invent authority. The concrete
proposal is [ADR-0007](adr/ADR-0007-objective-acceptance-boundary.md): extend the
existing owner-controlled generation with an Objective/criterion-mapping
component, retain the existing root, preserve legacy operations and keep
activation external. It is not active; this change does not implement that source.

Authenticated closure, full autonomous continuation, live governed engineering
E2E and final independent acceptance audit remain open. The diagnostic and tests
are not substitutes for those capabilities. Existing FCC canary/resume/recovery
evidence remains valid within its diagnostic scope.

Validation of this correction: 1,152 local tests passed; Ruff, strict MkDocs and
diff checks passed. The local public-history audit passed over 510 commits and
1,113 blobs. Independent Reviewer approved after 62 focused tests; Compliance
Officer passed after 18 focused tests. A reproduced finding about reusing a task
ID while changing historical criteria was corrected and retained as a regression
test. Read-only inspection of a real registered project session returned BLOCKED
with unverified integration and unknown Objective acceptance, without changing
its state. This is negative-path evidence, not a full engineering E2E.


## Post-merge continuation of #196 and approved ADR-0007 implementation

GitHub confirms #196 merged at `c412b13563d441a37db7eed9afc44cc5281ec554`.
Post-merge CI `34215238697`, CodeQL `34215238732` and Docs `34215238749`
all passed. The subsequent read-only CodeQL query returned zero open alerts.
The original dirty checkout is preserved; implementation uses an isolated branch
based on that main commit. Dependency PRs #189–#194 remain separate candidates.

The owner explicitly approved implementation of ADR-0007 within the existing
trust root and authority mechanism. The approval excludes activation, key or
credential installation, a runtime signer, caller approval flags and another
policy engine. The versioned generation reader and
[Objective acceptance runtime](OBJECTIVE_ACCEPTANCE.md) implement the closure
portion of that design. They do not activate deployment authority.

Independent review reproduced and corrected three premature-closure cases:
receipts or human evidence replaced during validation; project disablement during
validation; and relabeling the plan to an unrelated Objective with matching task
IDs and commands. The runtime now rereads canonical evidence and the project
registry after validation and before persistence, and enforces Objective,
requirement and historical lineage bindings. Regression tests preserve each case.

Real temporary Git integration tests exercise current validators, signed human
acceptance, failed validators, target drift, interrupted session persistence and
restart. Signing keys and owner activation in these tests are ephemeral fixtures.
They do not demonstrate a live engineering provider or activate real authority.
The existing FCC diagnostic canary, failure and resume evidence retains its
previous limited scope.

The full governed continuation driver, owner-side publication workflow for the
Objective generation, live engineering E2E with failure/recovery/resume, remaining
PR/issue reconciliation and the final independent mission audit remain open.
Neither this implementation nor a successful fixture Objective closes the final
completion mission or establishes NO_JUSTIFIED_WORK.


Final local validation of the closure candidate: **1,177 tests passed** on
Python 3.14 in 125.68 seconds, with real temporary Unix sockets enabled. This
is 1,152 inherited tests plus 25 new acceptance/closure cases; no tests were
removed or skipped to obtain green. Ruff, strict MkDocs, changed-document
relative links and whitespace checks passed. The pre-commit history audit
passed over 512 reachable commits and 1,123 unique blobs. Reviewer independently
approved with 50 focused tests; Compliance approved the final binding correction
with 36 focused tests. Commit-specific CI and human-reserved merge remain gates.


### PR #197 remote review follow-up

The first published commit passed CI (1,177 tests), CodeQL and Docs. Remote
review subsequently identified a remaining registry race after the final evidence
check and before session persistence. The follow-up holds the registry writer
lock across the final project comparison and session save. Regression cases
exercise disablement immediately after the evidence check and exclusion of a
registry writer during the save boundary; the writer succeeds once closure
releases the lock.

A second finding concerned equivalent remote URL spellings. Objective snapshots
and integrated intent/receipt checks now compare canonical repository identities.
An integration regression changes the remote spelling while retaining the same
repository, verifies the registry and completes acceptance against existing
canonical integration evidence. These fixes remain scoped to verified closure;
continuation and the full live mission remain pending.


The follow-up passes **1,180 tests** locally in 126.64 seconds, including all
1,177 previous cases and three additional regressions. Ruff, strict documentation
and whitespace checks pass. Independent Reviewer APPROVE: 50 focused tests;
Compliance PASS: 27 focused tests. Remote checks must be rerun on the follow-up
commit before human integration.


## Post-merge #197 and bounded continuation checkpoint — 2026-09-09

- FACT: #197 is merged at `48866a809f279882b9e0f436a390d4577c80fe6c`.
  Post-merge CI `34304570698`, CodeQL `34304570693` and Docs `34304570697`
  report success. The earlier checkout's pre-existing edits remain preserved.
- FACT: the continuation candidate connects canonical resume, assessment,
  dependency selection, governed delivery, external-integration waiting and
  Objective acceptance through bounded `session continue` steps.
- FACT: first-plan projection is bound to an owner-signed complete plan hash.
  It adds traceability without changing scope or validators and retains draft
  ancestry. Prior execution cannot be relabeled as unexecuted planning.
- FACT: independent review reproduced duplicate dispatch after a direct execution
  had started without a durable outcome. This is a required recovery correction;
  nominal continuation tests alone did not detect it.
- FACT: a separate closure regression verifies that prior dispatch journals block
  acceptance even when an approved plan was subsequently installed. Unsigned
  proposal preparation neither changes session state nor activates authority.
- FACT: direct daemon wiring is incompatible with its project-lock ownership,
  command-result protocol and fixed target binding. A dedicated coordinator must
  renew target binding from verified reconciliation without admitting unknown
  target changes. Background continuation is not claimed by the bounded CLI.
- UNKNOWN: qualified live-provider engineering, an operational owner issuance
  endpoint and published Objective generation, full governed failure/recovery/
  resume, and final mission acceptance remain undemonstrated. Existing FCC
  transport canaries retain their diagnostic scope.

This checkpoint does not declare mission SUCCESS or NO_JUSTIFIED_WORK. Remaining
owner publication, daemon integration, dependency PRs, issues and real E2E require
separate evidence; passing fixture tests cannot close them.


### Additional resource and recovery findings

The owner reaffirmed resource economy as a final acceptance criterion. Audit
reproduced five direct delivery invocations under an existing three-attempt
budget. The correction now charges persisted invocation history at the shared
entry point and reduces available correction rounds to `[2, 1, 0]`. Adapter and
canonical budget evidence also reject NaN, infinity and nonpositive timeouts.
These corrections reuse existing attempt and duration limits.

Two further closure regressions reproduced SUCCESS after a new uncertain dispatch
was recorded against an already integrated plan: one direct runtime journal and
one coordinator journal written before pipeline entry. Closure now applies
recovery checks to both rather than treating older receipts as resolution of a
later invocation. Preserved uncertainty blocks acceptance.

The selector uses canonical owner priority and eligibility, with finite fallback
within one Architect call. The cost-ranking helper is observational and not wired
to selection. Persistent budgets across fresh assessment retries, avoidance of an
unnecessarily powerful effective live model, and effective routing identity are
still unproved. These remain mission acceptance gaps, not reasons to add a new
cost subsystem or claim success from the present fixture suite.


### Recovery of the implementation workspace — 2026-09-10

The temporary implementation worktree and validation environments were absent at
resumption, while the original checkout and Git branch remained intact. Source
edits were recovered in order from the task and review tool records into a
persistent worktree. The recovered candidate passed **1,232 tests** in 161.79
seconds on Python 3.14.6; Ruff, strict documentation and the public-history audit
(515 commits, 1,143 unique blobs) also passed. No source or authority was restored
from an unverified provider response. This is development-workspace recovery,
not evidence that AGF has completed the required live mission recovery scenario.


Independent recovery review found one additional entry-point discrepancy: an
interrupted coordinator journal blocked `continue`, but direct delivery could
ignore it. New dispatches now use the single runtime journal created inside the
shared delivery lock. Historical coordinator dispatch journals remain checked by
every entry point and by closure. Coordinator reports are evidence only and do
not introduce a parallel retry counter. Regression cases cover both entry points.


Final candidate validation after the entry-point correction: **1,234 tests passed**
in 166.36 seconds, with no removals or skips to obtain green. Ruff, strict MkDocs,
changed-document relative links and whitespace checks pass. Independent Reviewer
inspection and Compliance review accepted the bounded changes; the final journal
delta passed 25 independent focused tests. Remote checks and the registered human
merge requirement remain integration gates. None of these results establishes
mission SUCCESS or resolves the resource/routing and live E2E gaps above.

### Background continuation checkpoint — 2026-09-11

- FACT: #198 is merged at `09f72365a8360fa31b17552ff941154b7c943c3c`,
  containing source commit `2924a2f`. Post-merge CI `34515898356`, CodeQL
  `34515898379` and Docs `34515898359` passed, including the CI history audit.
  Main still pointed at that merge during this checkpoint; open CodeQL alerts
  were zero. Dependency PRs #189–#194 remain open.
- FACT: the built-in session driver now runs within the existing persistent
  daemon, without holding the generic driver's project lock across session work.
  Polling is read-only. Canonical reconciliation advances target/plan bindings
  while preserving authority and budget. A restart between session and campaign
  persistence verifies the retained lineage, and a concurrent exact merge remains
  work for the next tick instead of causing a terminal failure.
- FACT: one immutable registration binds the driver configuration and existing
  campaign retry budget to the session. Fresh assessment calls persist consumption
  across errors, restart and retry reset. An unknown interrupted outcome blocks
  another call. Planning adapters now use the timeout recorded in canonical
  budget evidence instead of silently using the constructor default.
- FACT: separate forked daemon processes exercised WAIT, exit, real temporary Git
  integration, restart, reconciliation and canonical closure; a competing daemon
  was excluded. Tests also cover corruption, changed bindings, transient failure,
  bounded retry and the distinct NO_JUSTIFIED_WORK terminal result.
- FACT: the full candidate suite passed **1,269 tests in 168.03 seconds** on
  Python 3.14.6. No tests were removed or skipped to obtain green. Ruff, strict
  MkDocs and whitespace validation passed. The public-history audit passed over
  517 commits and 1,169 unique blobs. Independent Reviewer and Compliance Officer
  inspection accepted this bounded change; Compliance ran 38 focused tests.
- UNKNOWN: effective live provider/model routing, subordinate eligibility versus
  unnecessary powerful-model use, and end-to-end budget behavior with a real
  engineering provider remain unproved. Process tests use ephemeral authority
  fixtures and prohibit provider invocation; they are not live-provider E2E.

This checkpoint does not establish mission SUCCESS. Owner-controlled publication
and issuance, qualified real engineering E2E with failure/recovery/resume,
dependency/issue reconciliation and the final independent audit of the complete
system remain required. No real authority generation, credential or key was
installed or activated by this change. The existing human merge gate remains.

### Post-merge recovery findings — 2026-09-11

#199 merged at `01a5689ac55a7425a274e77be0f71b43ad514d1b`. Post-merge CI
`34584530947`, CodeQL `34584530940` and Docs `34584530935` passed; open CodeQL
alerts were zero. A subsequent review reported two valid recovery findings:

- Lock contention during a session operation was caught as generic runtime
  failure and converted to a terminal campaign result. LockError now reaches
  the existing runner's bounded retry/backoff path. Attempts still consume its
  retry budget; releasing the lock permits recovery without changing authority.
- A relative Architect configuration path depended on the daemon's working
  directory. Registration now fixes its absolute location, and persisted specs
  reject relative values. The existing root, symlink and authority validation
  still applies when loading configuration.

The corrected candidate passed **1,273 tests in 173.18 seconds**, including real
session/project lock contention and changed-working-directory regressions. Ruff,
whitespace checks and the public-history audit (519 commits, 1,187 unique blobs)
passed. Independent Reviewer and Compliance Officer reviews accepted the delta;
Compliance ran 20 focused tests. No tests were removed or skipped.

A read-only inspection of the real project authority found generation 4 with
constitution, policy, activation, rollback, registration and provider intelligence
components, but no Objective acceptance component. The current external controller
prepares six legacy components; an unsigned proposal alone cannot close that gap.
An operational owner-controlled publication path is still required before a
qualified real session can demonstrate authenticated Objective closure. This
inspection did not sign, publish, activate or install any authority or credential.
The separate documentation issuance endpoint is not automatically a prerequisite
for every planning flow; its necessity must follow the actual E2E scenario.

Live routing/model identity, effective budgets, complete failure/recovery/resume,
pending dependency/issue reconciliation and final independent system audit remain
open. This checkpoint is not mission SUCCESS.
