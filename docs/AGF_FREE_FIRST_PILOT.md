# AGF free-first pilot

Status: working owner-side pilot; not the final governed AGF campaign runtime.

## Components

- ChatGPT Chat (active sessions): launches or inspects via Remote Desktop Commander.
- AGF owner pilot: persistent free-model coding tasks, file updates, tests, retries and reports.
- Ollama: tested free inference on Mac using qwen3.5:9b-q4_K_M.
- OmniRoute 3.8.52: installed, user launchd service, localhost:20128; provider connection still pending.
- Codex GPT: unchanged and not consumed by these missions.
- User launchd worker: polls queued/resumable missions every 60 seconds.

## Commands

Source: tools/agf_chat.py; installed wrapper: ~/.local/bin/agf-chat

Create a tasks JSON file containing a list of objects with:
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

Once an eligible Ollama provider connection has been configured in OmniRoute,
start can include --omniroute-model ollama/qwen3.5:9b-q4_K_M.
The pilot will try that explicit gateway route and fall back to local Ollama.
The actual OmniRoute route is not yet verified, as the connection is absent.
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
- Tests approved after each task; 345 measured Ollama tokens, zero Codex GPT calls.
- Another queued real coding task completed through the launchd worker.
- Eight focused automated tests passed: retry, route fallback, persistence,
  path escape, allowed model and effective model provenance.
- Core AGF governance and Codex native login were not changed.

## Limitations

This is a bounded owner-side pilot. It has not been integrated into AGF's
governed Director or AGF Desktop's SwiftUI UI. It does not independently
authorize pushes, merges, releases or protected external operations.
Remote free providers and an actual OmniRoute completion remain to validate.
ChatGPT Chat cannot receive unsolicited messages and continue reasoning
without new model executions. These are explicit pending tasks, not successes.
