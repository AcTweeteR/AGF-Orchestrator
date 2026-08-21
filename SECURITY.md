# Security Policy

AGF-Orchestrator is executable governance infrastructure for autonomous software-development agents. Security issues can therefore affect both conventional software safety and the integrity of authority, evidence, policy, provenance, recovery, and external actions.

## Supported versions

AGF-Orchestrator is currently pre-1.0. Security fixes are made on the current `main` line. Older commits and unreleased development branches should not be assumed to receive fixes.

## Reporting a vulnerability

Please report security-sensitive issues privately to the repository maintainer through GitHub's private vulnerability reporting/security advisory mechanism when available. If that mechanism is not available, contact the maintainer privately through the GitHub account that owns this repository before publishing exploit details.

Do **not** open a public issue containing working exploit details for vulnerabilities that could:

- bypass an authority or policy gate;
- forge, replay, substitute, or invalidate evidence or provenance;
- escape allowed-path or repository boundaries;
- cause unauthorized Git/GitHub or other external mutations;
- weaken signature, identity, lineage, or target binding;
- reactivate terminal/stale work or bypass fail-closed recovery;
- disclose credentials, tokens, keys, environment files, or protected state;
- allow a provider or worker to grant itself additional authority.

A useful report includes the affected version/commit, reproduction steps, expected versus observed behavior, impact, and any proposed mitigation. Please minimize access to real credentials or third-party data while reproducing an issue.

## Security model

AGF-Orchestrator assumes providers and generated outputs can be wrong, incomplete, stale, or adversarial. Trust is therefore derived from explicit authority and verifiable evidence rather than provider assertions.

Important security properties include:

- least privilege and bounded execution scope;
- separation of authority, implementation, review, Compliance, and release concerns;
- fail-closed handling of missing, stale, ambiguous, or mismatched evidence;
- cryptographic or content-hash binding where required by the active protocol;
- anti-replay and exact target/session/project binding;
- explicit policy checks before privileged external actions;
- no retroactive fabrication of authorization when reconciling external results;
- recovery that preserves provenance and terminal-state semantics.

See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md), [docs/DECISION_MODEL.md](docs/DECISION_MODEL.md), [docs/REVIEW_PIPELINE.md](docs/REVIEW_PIPELINE.md), and [docs/HUMAN_INTERVENTION.md](docs/HUMAN_INTERVENTION.md).

## Secrets

Never commit real credentials, API keys, private keys, access tokens, cookies, `.env` files, or copied production state. The runtime may load an explicitly permitted dotenv file, but secret material must remain outside version control and outside managed-project evidence unless a protocol explicitly defines a safe redacted representation.

## Disclosure

After a fix is available, maintainers may publish a security advisory describing impact, affected versions, remediation, and credit. Coordinated disclosure is preferred for issues that cross authority or external-action boundaries.
