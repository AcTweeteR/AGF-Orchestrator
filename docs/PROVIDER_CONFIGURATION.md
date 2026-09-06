# Codex provider configuration and resume

This is operational adapter documentation. Providers supply execution and
observations; AGF retains planning, policy, scope, evidence, review and
completion authority.

## Configuration identity

The Codex adapter preserves the host's `CODEX_HOME` when launching Codex. If it
is unset, Codex uses its default user configuration directory. The adapter
continues to exclude arbitrary environment variables and credential values.
Do not select a configuration home from untrusted repository or provider output.
An explicitly configured home must be absolute. Relative, empty and unexpanded
tilde values fail closed before any executable or help probe, because the child
process runs in a separate worktree. Resolving a relative configuration there
would incorrectly promote repository content to trusted user configuration.

A custom Codex provider is a transport definition: endpoint, wire API and
authentication mechanism. An execution harness or gateway can expose such an
endpoint without becoming an AGF authority. Its provider identifier must be
resolvable wherever a session is resumed.

Command-line `-c model_provider=...` and `-c model_providers...` overrides are
temporary. A session may retain the provider ID after those definitions are
gone. Resuming it directly from another CLI or desktop entry point can then
fail with `Model provider ... not found`. This applies to arbitrary custom
provider IDs, not only FCC.

## FCC incident and recovery

The inspected `fcc-codex` launcher selected `fcc` and supplied its complete
definition through temporary command-line overrides. The user configuration
did not contain that provider, while persisted sessions still referenced it.
Codex 0.149.1 reproduced the error using `app-server` `config/read` with the
provider missing; defining the transport made the same read succeed. No model
request was needed to demonstrate the configuration failure.

For sessions that must also resume outside the launcher, register the same
transport in the owner-controlled user config. For example, after verifying
the installed helper and the actual proxy endpoint:

```toml
[model_providers.fcc]
name = "Free Claude Code"
base_url = "http://127.0.0.1:8082/v1" # Replace with the verified local endpoint.
wire_api = "responses"

[model_providers.fcc.auth]
command = "/absolute/path/to/fcc-codex"
args = ["--print-proxy-auth-token"]
```

This registers a transport; it does not select it as the default provider.
The helper is invoked by Codex when authentication is needed. Do not copy its
output into config, logs or AGF evidence. Keep a backup before editing config.
Alternatively, resume through the same launcher so it reconstructs its
configuration. Avoid silently replacing the persisted provider ID with a
different provider: doing so changes execution identity rather than repairing
the missing definition.

Successful config loading establishes only that Codex can resolve the
transport. Proxy availability, authentication, effective upstream model,
eligible fallback, AGF authorization and real execution remain separate checks.
A stopped proxy should remain an explicit availability failure.

Current [official configuration documentation](https://learn.chatgpt.com/docs/config-file/config-advanced)
describes `CODEX_HOME`, custom providers and command-backed authentication.
It also prohibits provider and authentication overrides from project-local
configuration. Keep these settings in the host-owned configuration boundary.
