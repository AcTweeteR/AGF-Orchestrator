# Project Governance

AGF-Orchestrator is an open source project with a deliberately conservative governance model because it defines and implements authority boundaries for autonomous agents.

## Roles

- **Maintainer:** stewards the repository, roadmap, releases, security response, and project-level decisions.
- **Contributor:** proposes code, documentation, tests, examples, or design changes through issues and pull requests.
- **Reviewer:** evaluates correctness, maintainability, security, and governance impact. Review authority does not imply release or policy authority.

Roles in this file describe the open source project. Runtime roles such as Director, Architect, Implementer, Reviewer, Compliance Officer, and Release Manager are defined separately by AGF's operational model.

## Decision making

Ordinary implementation and documentation decisions are made through review of issues and pull requests. Maintainers may accept, request changes, defer, or reject proposals based on project scope, evidence, compatibility, security, and governance impact.

Changes that alter authority sources, trust boundaries, policy interpretation, evidence semantics, lifecycle invariants, provider-independence rules, recovery authority, or external mutation protocols require explicit architectural treatment and normally an ADR.

No pull request, vote, provider output, test result, or contributor status can silently amend the AGF Constitution or grant runtime authority that the active governance model reserves to an Owner/human boundary.

## Independence and conflicts

Provider vendors are welcome to contribute adapters and interoperability improvements, but no provider receives governing authority by virtue of sponsorship, integration, usage, or contribution volume. Normative rules remain provider-neutral.

Contributors should disclose material conflicts of interest when advocating changes that would privilege a provider, service, or commercial dependency.

## Releases

Pre-1.0 releases may change interfaces and persistence formats. Release notes should identify security-relevant changes, migrations, authority/policy changes, and known limitations.

## Security and embargoed work

Security-sensitive changes may be developed privately until coordinated disclosure is appropriate. See [SECURITY.md](SECURITY.md).

## Amendments to this governance file

Changes to project governance are reviewed like other repository changes. They do not, by themselves, modify the runtime Constitution, authority model, or active project policies.
