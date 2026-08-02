# Recovery Protocol

Recovery returns a task to the earliest role that can safely resolve the failure while preserving history. The Director coordinates recovery; no recovery action erases evidence, silently changes scope, or bypasses a gate.

## Common recovery sequence

1. Stop the affected transition and preserve the last valid context, artifact, and evidence.
2. Record failure ID, category, task state, impact, evidence, suspected cause, owner, and safe disposition options.
3. Route to the role named below; the Director records any state return or scope change.
4. Issue corrected context or work and mark superseded records without deleting them.
5. Repeat every affected quality gate before progression.
6. Close the recovery record only when the original failure, corrective action, and verification evidence are linked.

## Failure-specific recovery

| Failure | First recovery owner | Return point | Required evidence before resume |
|---|---|---|---|
| Failed implementation | Implementer, coordinated by Director | Planned or Executing | Corrected outcome, bounded deviation record, and execution validation |
| Failed review | Implementer for correction; Reviewer for re-evaluation | Executing, then Review | Finding disposition, corrected outcome, and fresh independent review |
| Failed compliance | Compliance Officer and Director | Planned or Compliance | Remediation mapping, new evidence, and repeated compliance evaluation |
| Failed release | Release Manager and Director | Release or earlier gate named by finding | Corrected version/changelog/scope evidence and repeated release checks |
| Conflicting architecture | Architect, then Director | Architecture | Decision record resolving conflict or human decision under escalation protocol |
| Missing evidence | Role that produced the evidence, supervised by gate owner | Source stage | Attributable evidence or recorded inability and resulting blocked disposition |

## Terminal recovery

If safe recovery is impossible, the Director records Blocked, Rejected, or Cancelled with the reason, evidence, owner, and human intervention result when applicable. A terminal recovery outcome is final for that task and cannot be represented as Done. Any successor task references the failed task rather than overwriting it.
