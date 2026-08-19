# Persistent Campaign Runner

The persistent campaign runner is the durable execution boundary for campaigns
that must outlive a provider invocation or an interactive Director process.
Providers perform bounded cognitive or technical work; the runner owns the
campaign state, external wait, retry budget, wake condition, and terminal
decision.

## Lifecycle

```text
LOAD → RECOVER → ASSESS → PLAN → SELECT PROVIDER → EXECUTE
     → REVIEW → COMPLIANCE → DELIVERY → RECONCILE → REASSESS
```

The only terminal campaign states are `COMPLETE`, `HUMAN_REQUIRED`,
`BLOCKED_NON_RETRYABLE`, and `CANCELLED`. External conditions are resumable
states: `WAITING_CI`, `WAITING_REVIEW`, `WAITING_GITHUB`, `WAITING_ARTIFACT`,
`WAITING_DEPLOYMENT`, `WAITING_PROVIDER`, `WAITING_EXTERNAL`, and
`RETRY_BACKOFF`.

Every wait persists its reason, resource, expected condition, timestamps,
retry count and budget, next check, target/lineage binding, session identity,
and an append-only bounded event journal. A wake is idempotent: duplicate wake
events cannot invoke work twice for the same persisted transition. A restart
reopens the same campaign record and continues from its durable wait; it does
not create a new campaign or session.

The runner uses bounded exponential backoff and performs direct external
probes while waiting. It does not invoke a provider merely to ask whether CI
has finished. A retry budget exhaustion is `BLOCKED_NON_RETRYABLE`, never an
infinite loop. Human-required and completed campaigns are terminal and never
re-enter provider or external work.

The implementation is in `agf_orchestrator.campaign_runner` and reuses the
existing project lock and atomic state-storage boundaries. It does not grant
providers authority over scope, policy, secrets, delivery, or external
financial effects.

## Independent macOS process

`agf-orchestrator campaign-runner run --state-dir <state-root>` is the
independent process boundary. It persists driver specifications, holds a
single-instance lock, writes a heartbeat/status record, directly probes
external conditions, and invokes a provider/work adapter only after a wake.
It survives provider and terminal-session exit because it is not a child of
the provider invocation. A user `launchd` agent can be rendered with
`campaign-runner install-launchd`; loading that agent is an explicit OS
operation. `campaign-runner status` reports `RUNNER_ACTIVE`, PID/instance,
active and waiting campaigns, next wake, last wake and last action.
