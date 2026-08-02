# Vision

AGF-Orchestrator coordinates autonomous software-development agents while keeping governance explicit, reviewable, and accountable.

AGF defines the rules. AGF-Orchestrator applies those rules to an operational flow: a request becomes a bounded task, agents produce work and evidence, independent gates evaluate it, and release occurs only when the required authority has accepted the result.

## Principles

1. **Policy before action.** Work is bounded by applicable AGF rules before execution begins.
2. **Authority is explicit.** Every consequential decision has an accountable owner.
3. **Evidence is first-class.** Claims about work, review, compliance, and release are backed by traceable evidence.
4. **Separation of duties.** Creation, evaluation, compliance determination, and release authority remain distinct.
5. **Minimum necessary intervention.** Humans intervene only at defined risk, ambiguity, or authority boundaries.
6. **Provider neutrality.** Agent providers are interchangeable adapters and cannot redefine governance.
7. **Fail closed.** Missing evidence, failed gates, or ambiguous authority stop progression.

## Scope and non-goals

The model covers orchestration roles, task state, workflow stages, decision ownership, review, compliance, failure recovery, human intervention, adapters, and release boundaries. It does not prescribe a programming language, deployment platform, agent provider, repository host, or implementation technique.
