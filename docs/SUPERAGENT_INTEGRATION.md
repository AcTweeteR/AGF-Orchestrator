# Superagent design transfer — AGF owner-side integration

Reference: https://github.com/deepseek-ai/dsh-superagent-prototype at
commit `8c32a9dc5d72c033816c6a8110b37f40cf94fba4`.
Its README identifies it as an experimental research prototype, not a
production-ready product. The repository is MIT-licensed. This integration
**does not vendor or execute DeepSeek's TypeScript code**. It adapts two
architecture principles to AGF's Python owner-side Free-First pilot.

## Applied patterns

1. **Coordinator and Executors are separate.** The scheduled `agf-chat worker`
   dispatcher checks durable mission records and launches at most one independent
   executor by default, then returns without waiting for model generation or
   tests. Explicit `--max-workers` permits 1–4 slots; the default
   protects the Mac's local inference capacity. Each executor retains the
   existing per-mission nonblocking file lock. Paused, blocked, completed, and
   human-required work is never dispatched.

2. **Assignment recovery verifies the work before repeating it.** Each
   Free-First task commit contains immutable mission/task identity and a SHA-256
   digest of the owner-approved task. If execution ended after `git commit`
   but before persisting the mission checkpoint, recovery verifies branch
   identity, the expected parent, the full commit markers, the approved changed
   files, static checks, and final acceptance tests before advancing the task
   exactly once. An interrupted attempt without an authenticated commit,
   or dirty, unrelated, or unauthenticated changes, remains
   `HUMAN_REQUIRED` rather than being treated as authorized.

## Source-of-truth reconciliation

The Mac's installed `~/.local/share/agf-free-first/agf_chat.py` and
`agf_full.py` held owner-side resilience fixes not yet present in the
GitHub branch. Those existing changes were reconciled into this isolated
integration branch, and tests were updated to match the persistent planning
behavior. Do not deploy an older tracked file over the installed version.

## Boundaries intentionally preserved

- DeepSeek Harness, its `danger-full-access` workers and the Feishu bot
  are **not** installed. They would introduce unnecessary runtime privileges
  and a new orchestration stack.
- OmniRoute and local Ollama remain Free-First. No Codex GPT calls, paid
  inference, auto-push, auto-merge or release authority are added.
- The owner-side dispatcher does not replace AGF constitutional Director,
  canonical delivery evidence, Review, Compliance, or protected operations.
- Historic campaign state is not migrated or restarted.
- This is not evidence of end-to-end autonomous product delivery, device QA,
  or final AGF completion.

## Acceptance

The focused tests in `tests/test_agf_full.py` and
`tests/test_agf_superagent_dispatch.py` exercise interrupted commit
recovery, rejection of forged/out-of-scope commits, human escalation for
ambiguous interruptions, nonblocking dispatch, slot enforcement and inactive-state skipping.
The repository test suite plus Ruff and diff checks must pass before publishing
this branch. A disposable real-model integration pilot and constitutional
handoff validation are separate required steps before declaring AGF finished.

## Reference attribution

Design concepts were informed by the MIT-licensed DeepSeek Harness Team
prototype, especially `docs/001-domain.md`,
`docs/003-execution.md` and `docs/004-prompts.md`.
No substantial copied implementation or translated source is included here.
