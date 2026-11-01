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
