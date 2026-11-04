# Governed Free-First integration — live evidence and remaining gates

Status: implemented and tested as a bounded integration. **Not a claim of
complete autonomous production delivery.** The canonical AGF Director remains
the authority. The owner-side Free-First runner does not become a substitute.

## Defect fixed

A first executable plan already matched the current owner-signed Objective
contract and carried the persisted `objective_binding`, but
`SessionContinuation` always called Architect when there was
no assessment artifact. Without a separate Architect provider this turned
a valid owner-approved work item into `BLOCKED` before any
implementation.

Continuation now recognizes **only** an exact owner-signed, project/session
bound first plan, including the active Objective component hash and canonical
approved plan hash. It may then reach the existing Objective gate, admission,
task dependencies, execution journal and DeliveryPipeline without asking an
LLM to re-approve the owner's exact plan. Invalid, missing, changed or stale
bindings still follow the prior assessment/escalation path.

A second hardening prevents `free-first` live delivery from
invoking a provider or pushing code when no project identity is supplied.
Legacy non-Free-First bootstrap behavior remains unchanged.

## Evidence

- Real local inference: OmniRoute at loopback selected
  `ollama/qwen3.5:9b-q4_K_M`. It changed a disposable Python
  function under an exact allowed path, passed its functional validator,
  received deterministic Review `APPROVE` and Compliance
  `PASS`. No Codex GPT or paid API was used.
- With a disposable owner bootstrap state and project identity, the native
  delivery pipeline created a local commit, pushed only the task branch to
  a disposable bare Git remote and returned
  `local://draft-pr/agf/plan-live-free-first-test/task-001`.
  The caller's `main` and working tree stayed unchanged.
- A fully signed disposable Objective and project authority generated
  using a fresh Ed25519 test key also completed a real Qwen implementation.
  With independent, signed SYNTHETIC QA-ONLY risk-history baseline and
  incident/rollback coverage, its path reached:
  DELIVERY COMPLETED -> Review APPROVE -> Compliance PASS ->
  branch PUSHED -> WAIT external-integration -> simulated owner
  git merge --ff-only to a disposable local bare remote ->
  CONTINUE reconcile -> SUCCESS objective_completed=true.
  No real production incident/rollback history or merge permission is implied.
- With MISSING historical owner coverage, the same signed Objective was
  prevented from publishing: risk snapshot verification failed, with no
  commit or push, and recovery remained HUMAN_REQUIRED.
  The policy verifier was not bypassed.
- A deterministic, network-free regression in tests/test_governed_free_first_e2e.py
  generates a fresh Ed25519 authority and signed synthetic historical
  coverage for every test. It proves success on an isolated Git remote and
  fail-closed behavior without coverage. The live Qwen run was separately
  observed on the Mac.
- Automated tests cover the signed-plan path, tampered Objective binding,
  native Git branch delivery and refusal of missing project identity.
  Existing objective, recovery and continuation suites are retained.
- The historical risk module now reads the canonical pinned owner fingerprint
  from the public-key verifier rather than duplicating a hardcoded literal.
  Production trust is unchanged; test keys can be exercised without
  weakening verification.

## What remains before AGF is finished

1. A real owner-controlled and cryptographically verified risk-history
   baseline and fresh coverage for the actual project. Do **not** manufacture
   zero-incident/zero-rollback evidence from absent records.
2. Repeat the now-verified disposable signed end-to-end path on a real
   owner-authorized target, including authentic prospective risk evidence
   and independently approved integration. No production auto-merge permission
   has been granted; the real-owner deployment is not yet proven.
3. Verified restart/crash/reconciliation on the live target, plus AGF Desktop
   handoff validation and a final independent system audit.
4. A single supported release/deployment path, including migration or safe
   retirement of historical incompatible campaign-daemon records. Never
   reactivate legacy stored campaigns to make a status indicator green.

This work neither activates a production owner key nor expands agent
permissions, model billing, target paths or automatic merge authority.
