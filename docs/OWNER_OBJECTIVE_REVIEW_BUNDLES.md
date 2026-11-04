# Owner Objective review bundles (AGF Desktop handoff)

AGF Desktop can request a **fully bounded, unsigned** Owner review document
for an AGF Orchestrator session. A READY session is a planning placeholder, not
Owner authorization to run it.

The separate AGF Orchestrator tool is:

```sh
cd ~/Documents/AGF
.venv/bin/python -m tools.objective_review_bundle draft \
  --session session-xxxxxxxxxxxxxxxxxxxx
```

The JSON contains the actual session, target SHA, allowed_paths, test commands,
planned tasks, a complete Objective draft, and all acceptance/constraint mappings.
The Objective is initially `DRAFT`. Neither the AI nor Desktop signs anything.

The owner reviews and edits the component. To mark the content ready for
independent Owner approval, the owner explicitly changes
`component.objective.status` to `APPROVED`. This is only a proposed
content status; it is NOT an Owner signature or activation. Any edits that
change bound text must also update the criterion mapping hashes; the canonical
preparer refuses stale or inconsistent mappings.

The Desktop bridge passes the edited component via standard input to:

```sh
.venv/bin/python -m tools.objective_review_bundle prepare \
  --session session-xxxxxxxxxxxxxxxxxxxx < reviewed-component.json
```

Preparation validates the **exact** canonical session, original plan lineage,
current signed Owner generation, complete criterion coverage, reviewed
validators, and immutable plan hash using the existing
`tools.prepare_objective_acceptance` implementation. It saves a proposal
privately as

```text
~/.agf-orchestrator/owner-objective-reviews/SESSION/PROPOSAL_HASH.json
```

using exclusive mode `0600`. The content is immutable for any given hash;
conflicting writes are rejected. Preparation returns `authority_effect=NONE`.
No private key is imported, read, or used.

## Independent Owner activation

Review the **entire** saved proposal (including
`component.approved_plan_sha256`, target SHA, scope and validators). Separately
run the installed Owner-only publication commands in Terminal, not via the
Desktop bridge:

```sh
cd ~/Documents/AGF
.venv/bin/python -m tools.owner_objective_publication prepare \
  --project PROJECT --operation-id OPERATION \
  --proposal '/absolute/owner-review/path.json'

.venv/bin/python -m tools.owner_objective_publication verify \
  --project PROJECT --operation-id OPERATION \
  --proposal '/absolute/owner-review/path.json'

# Only after reviewing the exact manifest returned above:
.venv/bin/python -m tools.owner_objective_publication activate \
  --project PROJECT --operation-id OPERATION \
  --proposal '/absolute/owner-review/path.json' \
  --expected-manifest EXACT_REVIEWED_MANIFEST
```

The Owner tool confirms the current predecessor generation and the exact
reviewed content; it cannot silently switch scopes, change a session or
substitute a new Plan. Owner approval is a separate deliberate action, even
when the chat user has broadly authorized software development.

After successful Owner publication, `agf-governed session assess --session
SESSION --json` can use the installed eligible Architect. The signed local
OmniRoute profile still has its own currency, policy, privacy and budget gates.
Execution and delivery retain separate explicit confirmations and review,
Compliance and human merge boundaries.

An active Owner generation may preserve one historical legacy Objective and
also hold an **additive, signed session roster**. Each unrelated session receives
an independently reviewed and signed Objective in a subsequent generation.
The legacy Objective and every previously accepted roster entry remain signed
and immutable; an existing entry can only change under authenticated
same-Objective succession after external advancement. The roster is bounded
to 64 independent sessions plus the legacy entry. No session inherits
another session's approval. Executing, merging and closing a new Objective
still require all normal current risk, provider, Review and Compliance gates.

The test suite uses only disposable Git repositories and ephemeral Owner
Ed25519 keys. Live review generation remains unsigned and does not authorize a
protected or paid action.

## Evidenced initial file scope

The deterministic initial Director is only a placeholder, not an authorized
implementation. A reviewable bounded plan is created when the goal explicitly
names **one existing tracked non-sensitive file**, e.g.
`Review path:README.md and use git diff --check`.

The path is checked against the Git index and real file, without shell
execution. Ambiguous/multiple, untracked, symlinked, absolute, hidden,
sensitive or path-traversal candidates remain **unscoped**. Unscoped tasks
are rejected before the Owner can even prepare an unsigned proposal; they
never become authorized work. The exact scoped paths, validators and review
criteria still require independent explicit Owner signature and activation.
This conservative path-only fallback is not a substitute for actual target
assessment by an eligible Architect for multi-file or newly created changes.
