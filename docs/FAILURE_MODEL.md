# Failure Model

Failures are recorded as events attached to the task. Recovery must preserve the original evidence and explain the disposition.

| Category | Detection | Escalation | Recovery |
|---|---|---|---|
| Architecture failure | Boundary, assumption, or decision does not satisfy constraints | Architect to Director; Human if risk or authority is material | Replan, revise architecture, and re-review affected decisions |
| Implementation failure | Outcome misses criteria or execution stops unexpectedly | Implementer to Director; Reviewer records defects | Return to Planned or Executing with bounded rework |
| Review failure | Review is incomplete, conflicted, or unsupported | Reviewer to Director; replace or repeat review | Assign an independent Reviewer and preserve the failed record |
| Compliance failure | Required policy control fails or evidence is insufficient | Compliance Officer to Human | Block release; remediate and repeat compliance evaluation |
| Execution failure | Unauthorized action, scope drift, or task blocker | Implementer to Director immediately | Stop, preserve evidence, reassess authorization and plan |
| Environment failure | Required execution or repository context is unavailable or unreliable | Affected role to Director | Pause safely; restore or replace context; verify before resuming |
| External dependency failure | Dependency is unavailable, changed, or untrusted | Director to Human when risk or scope changes | Pin an approved alternative, wait, or replan; do not bypass controls |

All failures fail closed when detection or ownership is uncertain. The Director coordinates recovery but cannot waive a Review, Compliance, or Release gate.
