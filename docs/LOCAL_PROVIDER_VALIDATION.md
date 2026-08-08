# Local provider validation

This document records the bounded validation of the optional Ollama provider.
It is evidence only; it does not change the roadmap, Constitution, merge
policy, or provider authority.

## Candidate

- Model: `qwen3.5:9b-q4_K_M`
- Runtime: Ollama on Apple M1 Pro, 16 GB unified memory
- Ollama inventory detection: PASS
- Reported model size: 6.6 GB
- OpenHands endpoint: `http://127.0.0.1:11434/v1`
- AGF model identifier: `openai/qwen3.5:9b-q4_K_M`

## Bounded results

| Canary | Result | Evidence |
| --- | --- | --- |
| Direct code generation | PARTIAL | 13.0 s; output stopped at the configured 160-token bound and omitted the required complete function/tests. |
| Direct tool call | PASS | 3.3 s; emitted a structured `read_file` call with the requested roadmap path. |
| Direct review | REQUEST_CHANGES | 9.5 s; identified security concerns in the bounded sample. |
| OpenHands integration | FAIL | Agent initialized in 18.5 s but emitted textual `<invoke_tool>` markup instead of an executable structured tool event; the exact canary marker was not produced. |

Gemini 2.5 Flash completed the comparable OpenHands code canary in about
6.7 seconds and returned a complete function. The local model therefore does
not currently meet AGF's provider contract for implementation through
OpenHands.

## Eligibility decision

`qwen3.5:9b-q4_K_M` is detected and directly callable, but it is **not
eligible as the AGF primary provider or fallback**. It remains an optional
diagnostic provider. AGF must fail closed when Ollama or the requested model
is unavailable, and no automatic provider promotion is enabled by this task.

The installed model and existing Gemini/Codex providers remain unchanged.
