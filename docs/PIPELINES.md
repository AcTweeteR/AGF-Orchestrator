# Pipelines

Pipelines are execution profiles over the single lifecycle in [RUNTIME.md](RUNTIME.md). They do not create alternative workflows or change role authority. Every pipeline uses [HANDOFF_PROTOCOL.md](HANDOFF_PROTOCOL.md), [CONTEXT_PROTOCOL.md](CONTEXT_PROTOCOL.md), [QUALITY_GATES.md](QUALITY_GATES.md), and [RECOVERY_PROTOCOL.md](RECOVERY_PROTOCOL.md).

| Pipeline | Entry condition | Required path | Special control | Terminal outcomes |
|---|---|---|---|---|
| Bug fix | Reproducible defect or credible defect report | Standard lifecycle | Preserve reproduction evidence; regression proof is mandatory | Done, blocked, or rejected |
| Small feature | Bounded feature with local impact | Standard lifecycle | One task or a Planner-approved small task set; no architecture change by implication | Done, blocked, or rejected |
| Large feature | Objective requiring multiple epics or dependencies | Standard lifecycle per task, then coordinated release | Director approves epics and parallelism; Architect records boundaries before dispatch | Done, blocked, or rejected |
| Refactor | Existing behavior must be preserved while structure changes | Standard lifecycle | Baseline behavior and non-regression evidence are mandatory | Done, blocked, or rejected |
| Research | Question whose outcome is knowledge, not publication | Goal → Director → Planner → Architect → Implementer → Reviewer → Compliance Officer → Done | Output must state evidence, uncertainty, alternatives, and a recommended disposition; publication is not implied | Done, blocked, or rejected |
| Documentation | Approved documentation change | Standard lifecycle | Link, terminology, scope, and governance checks are mandatory | Done, blocked, or rejected |
| Release | A compliant outcome is ready for delivery | Director → Compliance Officer → Release Manager → Done | Release Manager verifies readiness, cleanliness, changelog, versioning, and release scope | Done, blocked, or deferred |
| Emergency | Time-sensitive risk requires immediate governed action | Director → Architect or Compliance Officer as applicable → Implementer → Reviewer → Compliance Officer → Release Manager → Done | Director records emergency rationale; skipped work requires an explicit decision and later retrospective evidence | Done, blocked, or rejected |

## Profile rules

The Director selects the profile and records the selection. A profile may add evidence or an approval but cannot remove a mandatory gate unless an AGF decision explicitly authorizes the omission. Large-feature tasks may run in parallel only after the Director records dependency and isolation evidence. Emergency speed changes sequencing, not authority or evidence requirements.

Research and Release use narrower paths because their inputs already represent an evaluated artifact, but they still terminate through the same named accountable roles and recorded completion rules. A pipeline that cannot reach a terminal outcome is blocked and escalated rather than left active without disposition.
