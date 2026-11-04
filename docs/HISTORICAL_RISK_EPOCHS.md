# Owner-signed historical risk epochs

The historical rollback and incident evidence system supports an explicit
**new prospective epoch** when the canonical project or signed target changes.
It does **not** assert the absence of incidents in the intervening period.

## Rationale

The original August 2026 signed baseline belongs to an older commit. It
cannot be used as current risk evidence, nor may its signature or timestamps
be rewritten. Disabling risk checks or treating the missing months as verified
zero would violate AGF's fail-closed policy.

The independent Owner-only command is:

\`\`\`sh
python tools/owner_ed25519_authority.py \
  --project project-efc8e8ef7be7050b \
  --operation-id historical-baseline-epoch-20261104-first-rotation \
  --rollover-prospective-baseline
\`\`\`

This command accepts no arbitrary historical event counts. It verifies the
registered project is ACTIVE on canonical \`main\`, the prior signed baseline
is authentic and in the commit ancestry of the current project, the current
policy is available, and no campaigns are active or waiting.

The old signed baseline, journal, activation and ledgers are **moved intact**
to \`~/.agf-orchestrator/historical-evidence-archives/PROJECT/PREVIOUS_HASH/\`.
The Owner signs a new \`baseline_generation=N+1\` whose
\`predecessor_baseline_hash\` cryptographically links to the archived record.
The current baseline starts at the time of the rotation and explicitly records
\`pre-baseline=UNKNOWN;unobserved-gap=UNKNOWN\`.

The verifier checks all archived predecessor generations, owner signatures,
journal/activation bindings and original ledger hashes before accepting the new
active baseline. The archive cannot substitute for the current risk snapshot.
Missing or tampered archives fail closed.

Once there has been actual monitored coverage **after** the new baseline, the
existing Owner-only renewal command can sign the new incident and rollback
observations:

\`\`\`sh
python tools/owner_ed25519_authority.py \
  --project project-efc8e8ef7be7050b \
  --operation-id historical-renewal-20261104-epoch2-first \
  --renew-prospective-evidence
\`\`\`

This observes the current bounded, project-local append-only event ledgers.
A ledger with zero records is **not evidence about the previous unmonitored
period**; its observation is only prospective from the new baseline.

## Failure handling and limits

* The whole old epoch is preserved by an atomic directory rename.
* The new signed epoch is built outside the active path and only made active
  after the older evidence has been archived.
* A failing archive verification or post-activation check restores the prior
  active epoch. The failed candidate remains out of the active namespace.
* Replays, stale or non-ancestor target SHAs, unauthorized or unknown
  projects, mixed generations, symlinks, unexpected active campaigns and
  unverified signatures are rejected.
* If an OS crash interrupts the two directory renames, the archived
  predecessor and staged successor remain on disk; **do not create another
  baseline** until an Owner reviews and recovers that interrupted operation.
* This is not an automatic provider permission or delivery authorization.
  Capability profiles and Objective plans are still independently bound to
  the exact current commit and authorized through their own controls.

Tests use disposable Git repositories and ephemeral Ed25519 keys; they never
touch the actual Owner private key, archival state, or user project files.
