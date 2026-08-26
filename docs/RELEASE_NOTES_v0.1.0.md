# AGF-Orchestrator v0.1.0 — Initial Open Source Release

AGF-Orchestrator is an open-source, provider-neutral governance and orchestration runtime for autonomous software-development agents.

This initial public release establishes the pre-1.0 foundation for governing long-running coding-agent workflows under explicit authority, policy, evidence, review, Compliance, recovery, and human-control boundaries.

## Highlights

- provider-neutral execution adapters, including Codex-compatible workflows;
- explicit separation of authority from execution;
- deterministic planning and bounded allowed-path execution;
- independent review and Compliance gates;
- persistent project/session state with evidence and provenance;
- governed external actions such as pushes and draft PR creation;
- fail-closed handling for stale, ambiguous, replayed, mismatched, or missing evidence;
- recovery and reconciliation that preserve lineage rather than fabricating success or retroactive authorization;
- reproducible no-credentials local demo in `docs/GETTING_STARTED.md`;
- automated regression, Ruff, and full-history public-release auditing.

## Status

Experimental, pre-1.0 software. Interfaces and persistence formats may evolve. This release does not claim production readiness or significant external adoption yet.
