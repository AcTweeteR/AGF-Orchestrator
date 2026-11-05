# Verified zero-cost local Architect (OmniRoute → Ollama)

AGF's constitutional planning authority does **not** move to the model. A local
model is an untrusted advisory planner. Its output passes the existing exact
Architect schema, scope checks, independent review, compliance, and signed
Owner gates. There is no change to delivery or merge authority.

## Contract

- Provider ID: \`provider-omniroute-local\`.
- Interface: \`omniroute-local\`.
- Only effective model: \`ollama/qwen3.5:9b-q4_K_M\`.
- Gateway: fixed localhost OmniRoute at \`127.0.0.1:20128\`, with optional
  owner-configured \`AGF_OMNIROUTE_KEY\`.
- Cost: a verified zero-paid-model route only. No Codex, cloud or paid fallback.
- Provider identity is bound to the SHA-256 of the local adapter, strict compact-scope contract, and model gateway implementation.
- Explicit Owner candidate renewal is necessary for changed commit, policy,
  generation, provider source or after the signed 24-hour eligibility expiry.

The model selects a **single existing non-protected tracked file** and returns
a compact advisory intent. AGF verifies request hash, canonical repository
identity, approved inventory and Owner criteria, expands the output into the
strict Architect response, marks the task conservatively MEDIUM risk, and
preserves independent delivery controls.

A task requiring multiple bound Owner task IDs cannot use this local
single-file Architect. The result is a typed failure, never invented task
coverage. No-work is not asserted from a model guess.

An objective that explicitly names one `file:relative/path` or
`path:relative/path` constrains the proposal to **exactly that tracked,
non-protected path**. Absent, multiple or sensitive directives fail closed;
Qwen cannot substitute another similarly named file. This was detected on
Kodi when the prior model chose `docs/testing/README.md` instead of the
specified `file:README.md`.

## Model performance

OmniRoute's OpenAI-compatible proxy requires
\`reasoning_effort: "none"\` for these Ollama models. The non-standard
\`think: false\` field alone was ignored by the proxy. With thinking left on,
a 240-token response was consumed entirely as hidden reasoning and yielded
empty content. The canonical no-thinking field was measured at 1.8 s, with
visible JSON and no reasoning tokens, compared with direct Ollama at 2.3 s.

A real disposable Architect request generated and passed the strict schema
and existing AGF Architect validator in 4.6 s. It proposed only \`README.md\`
and one task. No target source file was modified by this Architect test.

## Owner-controlled activation

1. Verify the current main checkout and active Owner policy/generation.
2. From the main checkout, run
   \`agf-governed provider-intelligence bootstrap-architect --project PROJECT_ID
   --interface omniroute-local --renew --candidate-output PATH --json\`.
   PATH must remain within the project's
   \`~/.agf-orchestrator/capability-intelligence/PROJECT_ID/\` namespace.
3. Inspect the observed model, target, all capability outcomes and gate values.
   The unsigned candidate has **no authority**.
4. Run the external Owner controller's \`--renew-provider-candidate PATH\`
   only after validating observed facts; runtime never signs itself.
5. \`agf-governed provider-intelligence inspect --project PROJECT_ID --json\`
   must pass with the current target and policy. Missing or stale evidence
   remains ineligible.

Provider eligibility alone does not establish historical rollback/incident
coverage. Existing owner-signed historical baselines can still be bound to an
older SHA, and neither this adapter nor a successful canary can renew them.
Real target delivery stays fail-closed until independent evidence is fresh.

## Tests

The new tests deny unapproved models, credential exposure, paid fallback,
tampered request hashes, unauthorized paths, identity substitution, mixed
interface evidence, missing Owner validation coverage and unsigned activation.
All existing Provider Intelligence and Architect tests remain in the suite.

## Migrating an existing signed Codex profile

An existing `architect.json` may be bound to `provider-codex`. The external
Owner controller supports exactly one explicitly authorized identity rotation:
`provider-codex` → `provider-omniroute-local`, with strictly higher version,
fresh observed capability evidence, identical Constitution and project, stable
requirements, current implementation-source hash, and all eligibility gates
true. The runtime alone cannot activate this transition.

**Important:** If the target SHA differs from the old signed candidate, the
Owner controller still requires a verified chain of external advancements or
a canonical delivery receipt. Git ancestry or a merged GitHub PR alone is
insufficient. An incomplete chain remains `HUMAN_REQUIRED`; never rewrite the
old `architect.json` or manufacture the missing historical evidence.

## Large indexed repositories — Kodi acceptance case (2026-11-05)

The independently verified Kodi checkout had **3,310 tracked paths**, which
generated a canonical Architect instruction of **449,851 characters**. The
previous adapter rejected it at its 64k model-input gate *before* applying
the already existing source-bound compact-representation function.

The adapter now enforces two distinct limits:

- **Host-side canonical evidence:** up to **1,000,000 characters**, checked
  against the exact repository SHA and canonical request hash.
- **Actual model input:** up to **64,000 characters**, enforced *after*
  compacting the validated host evidence into a bounded single-file shortlist.

The full repository inventory is **never forwarded to the model**. Tampered
hashes, unknown paths, oversized instructions and noncompact huge inputs
fail closed. Owner criteria, risk gates and response validation are unchanged.

Live isolated Kodi validation with local Qwen reduced 449,851 characters
to 6,783, returned a schema-valid one-file plan restricted to `README.md`,
and recorded 0 € monetary model usage. Kodi's checkout remained clean.
After deployment, the external Owner controller must renew the signed
provider profile because this adapter's implementation-source hash changed.
