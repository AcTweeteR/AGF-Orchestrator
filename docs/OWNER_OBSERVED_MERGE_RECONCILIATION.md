# Owner attestation of observed GitHub merges

Status: project-scoped, append-only evidence for **provider target reconciliation**.

AGF can distinguish two separate concepts:

* \`ExternalAdvancement\`: prior Owner authorization for an external action.
* \`ObservedMerge\`: **current acknowledgement of an action that already happened**.
  This is not, and must never be represented as, historical permission to merge.

This solves the historical gap caused by GitHub merges made outside AGF without
relabeling them as pre-approved. The record contains the exact canonical
GitHub merge SHA, base branch, PR number, GitHub merger, observation time,
project identity, first-parent predecessor and current Owner signature.

The independent Owner controller performs the following validations for each
record:

1. Canonical registered project is ACTIVE, checked out on \`main\`.
2. The repository identity is a GitHub repository, and \`gh pr view\` confirms
   the specified PR was merged into \`main\`, by its repository owner.
3. The returned merge commit is in the verified local canonical history.
   The predecessor is its **first parent**, not an arbitrary ancestor.
4. The Owner signs the facts **now** with a distinct
   \`ACKNOWLEDGE_ALREADY_MERGED\` decision, never the prior authorization decision.
5. The record is stored atomically and append-only under
   \`~/.agf-orchestrator/observed-merges/PROJECT_ID\`.

The provider renewal path accepts this distinct observation **only** if a
complete, unambiguous first-parent chain connects the old signed
\`target_sha\` and the newly verified target. Missing, altered, unsigned,
unrelated or ambiguous records stop the renewal. This route does NOT authorize
delivery, permit merges, alter historical incident/rollback baselines, or
approve models without an independent capability canary and Owner signature.

## Explicit Owner-controlled workflow

After reviewing a known merged PR, issue:

\`\`\`sh
python tools/owner_ed25519_authority.py \
  --project project-efc8e8ef7be7050b \
  --operation-id record-observed-github-merge \
  --acknowledge-observed-pr 228
\`\`\`

The command does not accept arbitrary fabricated GitHub JSON or new target
SHAs. After each real PR has been witnessed, use the existing
\`--renew-provider-candidate\` flow for a newly measured, target-bound,
unexpired provider candidate. No automatic signing or preapproval occurs in
the regular runtime.

## Security and limitations

A past merge's state cannot be transformed into an authorization that existed
before the merge. Historical unknowns remain UNKNOWN. Only actual observed
GitHub PRs and their verifiable first-parent history are recorded. Owner
private keys never pass through model prompts, OmniRoute or test fixtures.
