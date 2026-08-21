# Contributing to AGF-Orchestrator

AGF-Orchestrator accepts contributions to the runtime, tests, documentation, examples, adapters, developer tooling, and governance model.

Because this project governs autonomous agents, changes are reviewed not only for correctness but also for their effect on authority, provenance, policy, evidence, recovery, and external actions.

## Before you start

For substantial work, open an issue describing:

- the problem or use case;
- the proposed scope;
- affected trust or authority boundaries;
- compatibility/persistence impact;
- how the result can be tested.

Small documentation fixes and isolated tests can go directly to a pull request.

## Development setup

Requirements: Python 3.12+ and Git.

```bash
python -m pip install -e .
python -m pip install pytest ruff
pytest
ruff check .
```

Do not put real credentials in the repository or in test fixtures.

## Pull requests

A good pull request should:

1. solve one bounded problem;
2. explain why the change is needed;
3. include or update tests for executable behavior;
4. state any impact on authority, policy, evidence, persistence, external actions, or recovery;
5. update documentation when behavior or contracts change;
6. pass the full relevant test suite and lint checks;
7. avoid unrelated refactors.

Changes that alter an authority source, trust boundary, policy interpretation, lifecycle invariant, evidence semantics, provider independence rule, or external mutation protocol require explicit architectural justification and normally an ADR under `docs/adr/`.

## Governance invariants

Contributions must not silently:

- let a provider grant itself authority;
- weaken fail-closed behavior;
- convert observation into authorization;
- bypass independent review or Compliance where required;
- fabricate delivery, review, receipt, or provenance evidence;
- broaden allowed paths or project scope without authority;
- reactivate terminal/stale work through recovery;
- bypass policy checks for external mutation;
- weaken signature, hash, identity, target, session, or lineage binding;
- make normative governance provider-specific.

If a desired feature appears to require one of these changes, describe the requirement openly rather than coding around the invariant.

## Provider adapters

Provider-specific mechanisms belong behind adapter boundaries. Normative governance documents should describe required capabilities, evidence, and outcomes rather than making one vendor part of the authority model.

New adapters should document discovery/configuration, timeout behavior, failure modes, output/evidence handling, and how they preserve bounded execution.

## Tests

Tests should prefer deterministic local fixtures. External-network, paid-provider, or credential-dependent behavior must be isolated and optional; core CI must not require a maintainer's personal credentials.

For governance-boundary fixes, add regression tests for the failure/attack case as well as the valid path.

## Security issues

Do not open a public issue with exploitable details for authority bypasses, provenance/evidence forgery, secret exposure, or unauthorized external actions. Follow [SECURITY.md](SECURITY.md).

## Conduct and governance

Participation is subject to [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Project decision-making and maintainer authority are described in [GOVERNANCE.md](GOVERNANCE.md).
