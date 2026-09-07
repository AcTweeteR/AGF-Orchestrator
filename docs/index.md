# AGF-Orchestrator

**Provider-neutral governance and orchestration for autonomous software-development agents.**

AGF-Orchestrator governs long-running coding-agent workflows under explicit authority, policy, evidence, review, Compliance, recovery, and human-control boundaries.

It is intentionally separate from the agents and model providers that execute work. Providers can propose or perform actions; they do not become the authority that decides whether those actions are permitted.

> **Status:** active, experimental, pre-1.0 software. Interfaces and persistence formats may evolve. Do not grant AGF production authority that you have not explicitly reviewed and bounded.

## Core properties

- **Authority is separate from execution.**
- **Evidence is first-class and replay-resistant.**
- **Missing, stale, ambiguous, mismatched, or tampered evidence fails closed.**
- **Review and Compliance remain independent gates.**
- **External mutations cross explicit policy boundaries.**
- **Observed external results do not become retroactive authorization.**
- **Recovery preserves lineage and provenance.**
- **Provider choice remains replaceable beneath AGF governance.**

## Start here

1. [Getting Started](GETTING_STARTED.md) — reproduce AGF locally without provider credentials.
2. [Architecture](ARCHITECTURE.md) — understand the runtime and trust boundaries.
3. [Constitution](CONSTITUTION.md) — read the normative governance constraints.
4. [Threat Model](THREAT_MODEL.md) — understand the adversarial assumptions.
5. [Autonomous Director](AUTONOMOUS_DIRECTOR.md) — see how long-running autonomous coordination is governed.
6. [Roadmap](ROADMAP.md) and [Autonomous Roadmap](AUTONOMOUS_ROADMAP.md) — follow planned capability growth.

## Governance model

AGF distinguishes three different concepts that must not collapse into one another:

- **Authority:** what may be done.
- **Responsibility:** which role or provider performs the work.
- **Observation:** what evidence says happened.

That distinction allows AGF to reconcile real-world state without fabricating permission, provenance, or successful completion.

## Public project resources

- Source code: [GitHub repository](https://github.com/AcTweeteR/AGF-Orchestrator)
- Releases: [GitHub Releases](https://github.com/AcTweeteR/AGF-Orchestrator/releases)
- Bugs and actionable work: [GitHub Issues](https://github.com/AcTweeteR/AGF-Orchestrator/issues)
- Questions and design discussion: [GitHub Discussions](https://github.com/AcTweeteR/AGF-Orchestrator/discussions)
- Security reporting: see [SECURITY.md](https://github.com/AcTweeteR/AGF-Orchestrator/blob/main/SECURITY.md)
- Contributions: see [CONTRIBUTING.md](https://github.com/AcTweeteR/AGF-Orchestrator/blob/main/CONTRIBUTING.md)

## License

AGF-Orchestrator is licensed under the Apache License 2.0.
