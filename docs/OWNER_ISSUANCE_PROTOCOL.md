# Owner issuance client contract

This describes the data-only client implementing the process separation
required by [ADR-0005](adr/ADR-0005-canonical-provider-eligibility-authority.md).
It does not activate an owner service or establish a new trust root.

`resolve_provider(..., issuance_endpoint=absolute_socket_path)` connects to an
already running local Unix-domain stream endpoint. A missing endpoint, an
unsupported platform or a transport failure produces `PROVIDER_INELIGIBLE`.
The legacy `issuance_attestor` callback parameter is rejected without calling
it. The runtime never starts a signer, loads a signing key or executes code
supplied by the caller in an owner process.

## Wire format

Each request and response is one frame: a four-byte unsigned big-endian byte
length, followed by that many bytes of UTF-8 JSON. Length is at most 65,536
bytes and a response cannot be empty. Connection, request write and response
read share a ten-second deadline. Truncated, oversized, malformed and deeply
nested invalid responses fail closed.

The request contains:

```json
{
  "schema_version": "1.0",
  "operation": "attest-provider-binding",
  "subject": {"...": "the complete governed binding subject"}
}
```

The subject is the existing authenticated binding subject, including its
binding-subject hash, owner decision identity, project/provider/profile,
revision scope, lifetime and applicable runtime constraints. The response is
the existing owner envelope, not an executable, callback or new authority
record. The client checks its payload hash and signature against the exact
expected subject through the existing owner authority verifier before issuing
a binding. Endpoint identity or a successful socket exchange is never sufficient.

## Operator obligations and deployment status

The endpoint must run under an independently controlled identity with exclusive
access to owner signing material. It must load canonical owner state and
independently validate authorization and current invocation facts. Request
fields are untrusted observations; accepting caller-provided `True` values as
proof would violate the contract. The endpoint must not load caller code, expose
arbitrary signing, create a parallel policy engine or use test trust material.
Configuration of the endpoint must come from owner-controlled integration.

No production endpoint is implemented or enabled by this change. A deployment
must demonstrate these obligations before provider issuance is activated. The
CLI and session wiring remains pending. Existing authenticated historical
bindings retain their bounded verification rules; this client does not turn
them into permission for new work.

Tests run an independent local fixture server with an ephemeral key. They
exercise framing, exact-subject verification, malformed responses, unavailable
transport, deadlines and a daemon client without runtime process creation.
These tests establish client behavior, not an operational owner service.
