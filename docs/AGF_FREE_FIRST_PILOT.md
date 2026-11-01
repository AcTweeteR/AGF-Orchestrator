# AGF free-first pilot

Status: working owner-side pilot; not the final governed AGF campaign runtime.

## Components

- ChatGPT Chat (active sessions): launches or inspects via Remote Desktop Commander.
- AGF owner pilot: persistent free-model coding tasks, file updates, tests, retries and reports.
- Ollama: tested free inference on Mac using qwen3.5:9b-q4_K_M.
- OmniRoute 3.8.52: installed, user launchd service, localhost:20128; local Ollama connected and verified.
- Codex GPT: unchanged and not consumed by these missions.
- User launchd worker: polls queued/resumable missions every 60 seconds.

## Commands

Source: tools/agf_chat.py; installed wrapper: ~/.local/bin/agf-chat

A natural-language mission now works without manually preparing tasks:

    agf-chat start --repository /absolute/git/repo --objective "Fix the parser" \
       --omniroute-model ollama/qwen3.5:9b-q4_K_M --background

The local Qwen model first plans up to three source-file tasks from the Python
repository inventory. Tests are fixed by the user/runtime, never proposed by
the model. This planner is intentionally bounded and is not a complete
replacement for the governed AGF Director.

Alternatively, create a tasks JSON file containing a list of objects with:
- file: repository-relative file path
- instruction: requested change
- tests: approved argv array, e.g. ["python3","-m","unittest","-q","test_calculator"]

Usage:

    agf-chat providers
    agf-chat start --repository /absolute/git/repo --tasks /absolute/tasks.json
    agf-chat list
    agf-chat status MISSION_ID
    agf-chat report MISSION_ID
    agf-chat pause MISSION_ID
    agf-chat resume MISSION_ID
    agf-chat stop MISSION_ID

The launchd worker picks up queued missions. Use --background with start to
run immediately. Mission state is at ~/.local/state/agf-free-first/<id>.json.
No user-provided test command is generated or changed by an LLM.

## OmniRoute

Install prefix: ~/.local/share/agf-omniroute
Dashboard: http://127.0.0.1:20128
Service: com.actweeter.agf-omniroute
Gateway policy: no auto models, no paid fallback; exact permitted model only.

The local Ollama connection is now configured and active in OmniRoute.

Use --omniroute-model ollama/qwen3.5:9b-q4_K_M to request the explicit,
free-only local route. A **real coding mission** passed through this gateway,
failed its first syntax test, automatically corrected on the second attempt,
and completed with 1,157 logged tokens and zero Codex GPT calls.
The pilot falls back to direct Ollama on gateway errors, never a paid API.
Use AGF_OMNIROUTE_KEY only if OmniRoute API authentication is enabled.
Do not place account secrets in source control.

## Install and service maintenance

    python3 tools/agf_install_service.py install
    python3 tools/agf_omniroute_service.py install
    python3 tools/agf_install_service.py status
    python3 tools/agf_omniroute_service.py status

The first install copies the stable runner to
~/.local/share/agf-free-first/agf_chat.py and generates ~/.local/bin/agf-chat.
Run it again after updating the source.

## Verified

- Real two-task coding mission completed with Qwen 3.5 9B.
- Live OmniRoute coding mission completed with retry after syntax failure.
- Natural-language objective planned with local Qwen and completed through the gateway.
- Tests approved after each task; 345 measured Ollama tokens, zero Codex GPT calls.
- Another queued real coding task completed through the launchd worker.
- Eight focused automated tests passed: retry, route fallback, persistence,
  path escape, allowed model and effective model provenance.
- Core AGF governance and Codex native login were not changed.

## Limitations

This is a bounded owner-side pilot. It has not been integrated into AGF's
governed Director or AGF Desktop's SwiftUI UI. It does not independently
authorize pushes, merges, releases or protected external operations.
Remote free providers remain to validate. A real OmniRoute completion has
been verified. Separate AGF Desktop preview can start Free-First missions
through the shared master prompt; this is an owner-side pilot bridge, not a
fully governed Director integration.
ChatGPT Chat cannot receive unsolicited messages and continue reasoning
without new model executions. These are explicit pending tasks, not successes.

## Governed AGF-Orchestrator execution

A native `FreeFirstGatewayAdapter` is now selectable in the existing AGF
Executor and DeliveryPipeline using `--adapter free-first`. The inherited AGF
plan/project/session permissions, worktree isolation, scope checks, validation,
review and delivery policies remain active. The pilot adapter deliberately
accepts **one existing Python source file** in the approved task scope and
routes only the fixed Ollama model via the localhost OmniRoute gateway.

Install a **separate** development CLI (without replacing the existing global
Codex CLI or AGF environment):

    python3 tools/agf_install_governed_cli.py
    agf-governed execute --help

Then use `agf-governed execute` / `deliver` with `--adapter free-first` and the
same registered-project, accepted-plan and explicit execution authorizations
required by AGF's normal commands. The native adapter has executed a real
source-code change through OmniRoute and passed a post-execution check.
This is not evidence that a complete production governed delivery campaign
has passed: full campaign acceptance, review and PR delivery still depend on
AGF's active project and policy. The separate `agf-chat` path remains the
practical natural-language mission interface for now.

## Multi-file project missions (2026-11-01)

The owner-side persistent Director now supports an **isolated multi-file mode**:

    agf-chat start --project-wide \
      --repository /absolute/path/to/repository \
      --objective 'Correct the preview issue while preserving Trakt behavior' \
      --scope addons/service.actweeter \
      --omniroute-model ollama/qwen3.5:9b-q4_K_M

It selects only existing tracked source files (Python, XML, JavaScript,
TypeScript, Swift, JSON, YAML, TOML and Markdown). Path ranking uses the
objective; `--scope` restricts work to a repository-relative path. AGF limits
local model context by selecting source excerpts and requires unambiguous
**exact find/replace** operations. Source paths cannot be invented by the model.
Each task may touch up to four files. At most four planning tasks are generated
per natural-language mission; the CLI also accepts an owner-supplied task JSON
file with up to 30 tasks, each using `files`, `instruction` and fixed `tests`.

**Isolation:** the mission creates a new Git worktree under
`~/.local/share/agf-free-first/worktrees/<mission-id>` and a local branch
`agf/free-first-<mission-id>`. Existing local changes in the caller's checkout
are not copied or overwritten. Changed files receive syntax/static validation.
Intermediate tasks defer full integration tests when later tasks are still
pending; the final integrated task runs the approved full tests. Each accepted
task gets its own local commit, with no automatic push, merge, release or Kodi
installation. `status` and `report` return the worktree path and branch.

A live multi-file mission edited Python and XML, passed the final tests and
committed locally without touching the original repo. A second **natural
language** mission generated two dependent tasks and completed the same full
Python/XML test suite after its first false-blocking validation rule was fixed.
The persistent macOS worker automatically picked up the queued mission, and
OmniRoute/locally served Ollama handled coding with zero Codex GPT calls and
zero paid API calls. A scoped dry inventory of the live Kodi repository found
real trailer/Trakt modules without changing them.

AGF Desktop Free-first Preview now uses `--project-wide` by default and shows
the generated branch. The existing AGF constitutional Director, independent
Review/Compliance, original historic campaigns, external device QA and safe
Beta/Stable publication remain separate governance steps. Passing unit tests
alone does **not** authorize publishing a Kodi release. A broad goal like
"finish every Kodi issue" must be split into milestones with meaningful tests;
these bounded missions are not an unlimited general-purpose autonomous coder.

Historical AGF campaign daemon: its installed older runtime could not parse
seven newer `governed-session/1` specs. Its eight existing driver records and
configuration were backed up under
`~/.agf-orchestrator/backup-campaign-daemon-20261101`, then that legacy
LaunchAgent was disabled to avoid restart storms and unexpectedly resuming
three stored RUNNING campaigns. This does not affect the newer Free-first
`launchd` worker. Do not reenable those old campaigns without migration and
review of their provider usage.

### Native governed adapter update

The native `FreeFirstGatewayAdapter` now supports up to four exact approved
source paths per AGF task, using the same bounded JSON exact-match patch
contract. It no longer assumes every task is a single Python file. AGF's
normal Executor/DeliveryPipeline still owns isolated worktrees, status,
validation commands, review, Compliance, publishing permissions and evidence.
The adapter neither promotes a model to a paid route nor authorizes a release.
The owner-side persistent runner remains the execution system for standalone
ChatGPT Chat/AGF Desktop missions, while the native adapter exposes the same
free local inference to AGF's constitutional workflows.

Final native integration smoke: the governed adapter directly received an
approved two-file task (Python and XML), returned bounded source changes via
OmniRoute and Qwen, and both independent post-execution assertions passed.
Three local-model attempts are permitted for malformed or unauthorized JSON
patches, with no additional files permitted; a failed attempt is rejected
before any source file is changed. The live accepted execution recorded
314 prompt tokens and 610 completion tokens, zero paid API charges.
