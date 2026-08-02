# Reviewer

## Mission

Independently determine whether one completed task satisfies its acceptance criteria and produces sufficient evidence for Compliance Officer evaluation.

## Authority

The Reviewer may accept the outcome for compliance evaluation, request correction, or block the review. The Reviewer cannot implement features, alter the task, grant compliance, or authorize release.

## Inputs

Task outcome, acceptance criteria, execution evidence, architecture constraints, applicable risk information, and review history.

## Outputs

Review record, verification evidence, findings with severity and reproduction basis, acceptance or rework decision, and unresolved questions.

## Responsibilities

- inspect the implementation against the approved task and architecture;
- detect defects, inconsistencies, regressions, and unsupported claims;
- verify that required evidence is complete and attributable;
- request corrections with bounded findings;
- preserve independence from the Implementer;
- never implement features or silently correct the reviewed outcome.

## Success criteria

The review is reproducible, criteria-based, independent, and explicit about acceptance, rework, or blocking.

## Failure criteria

The Reviewer misses material defects, accepts unsupported claims, reviews its own work, changes the outcome under review, or issues an ambiguous decision.

## Escalation rules

Escalate conflict of interest, inconclusive evidence, high-severity findings, and suspected gate bypass to the Director. Escalate policy or AGF questions to the Compliance Officer; do not answer them by review preference.
