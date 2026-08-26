# ADR-0005: Canonical provider-eligibility authority

- Status: Accepted
- Date: 2026-08-26
- Scope: E12-T14-PRE / PR #173
- Authority: existing owner-controlled Provider Intelligence authority

## Context

AGF needs one provider-neutral eligibility boundary for Architect/capability
selection, code intelligence, documentation, knowledge providers and future
capabilities. Before E12-T14-PRE, a consumer could be tempted to derive
eligibility from caller-supplied profiles, local representations, selection
results or deterministic hashes. Those values can be useful observations, but
they are not owner authority.

Selection is not authority. `CapabilitySelector` may choose deterministically
among candidates, but it cannot mint eligibility, override policy, or create a
new trust root.

## Decision

`ProviderEligibilityAuthority` is the canonical projection of the existing
owner-authenticated `ProviderIntelligenceState` for provider eligibility. It:

- loads and verifies owner-authenticated state from the canonical AGF state
  root and current authority generation;
- binds project, decision domain, provider, candidate profile, requirements,
  revision scope and target revision;
- applies owner-bound provider security posture and provider-specific facts;
- uses one verified owner-state snapshot where selection requires it;
- produces deterministic decisions from owner-controlled evidence; and
- rejects duck-typed, caller-supplied, staging or alternate production
  authorities.

The authority is a projection and verification boundary, not a second policy
engine, provider registry, scheduler, credential store or signing hierarchy.

## Owner authority and runtime authorization

Owner authority determines whether a provider may be eligible according to
canonical owner-controlled state. Current runtime authorization determines
whether that provider may be invoked now. A new invocation requires both:

```text
canonical owner eligibility
AND
all applicable current runtime constraints
```

Runtime constraints are restrictive only. `True` satisfies a restriction but
never creates owner authority; `False` denies; `None` or a missing value fails
closed when the gate applies. This includes availability, authentication,
policy authorization, privacy, network access and other applicable runtime
gates. Applicability comes from the owner-bound provider posture, not from
caller input.

Provider security requirements, candidate identity and priority, fallback
posture, and Architect-domain denials cannot be weakened by a caller or by
candidate-scoped facts.

## Durable provider provenance

`ProviderBinding` is durable authenticated provenance, not current runtime
authorization. A current binding records the exact governed resolution
subject and may prove that the governed path issued that historical binding
under the recorded owner decision and runtime conditions.

Current authenticated bindings use an owner-verifiable Ed25519 attestation
over the security-relevant subject, including the project, provider, profile,
decision/domain, requirements, revision/target, owner state and generation,
runtime authorization facts at issuance, lifetime and exact binding payload.
Public deterministic hashes provide integrity only. A hash of public fields,
decision data or JSON never proves governed issuance.

There is no independent signing authority, HMAC root, authority database,
provider registry or caller-controlled signer in this boundary. The existing
owner authority trust chain remains the root of trust.

## Historical evidence, restart and compatibility

Historical provenance validity is distinct from current invocation permission.
After restart, persisted authenticated bindings and documentation evidence can
be reverified from the canonical authority and owner envelope without process-
local object identity. A persisted binding and its historical attestation
never authorize new provider work; new work requires a fresh
`resolve_provider()` and fresh applicable runtime gates.

Authenticated schema-v3 artifacts from the immediately preceding
representation may use bounded compatibility verification. Safe historical
representation differences, such as the former profile-hash namespace or
`None` for a genuinely inapplicable conditional gate, are interpreted using
the authenticated historical subject and current owner-bound posture.
Compatibility never accepts a denial, waives always-applicable gates such as
`available` and `policy_authorized`, extends a lifetime, uses an arbitrary
rollout timestamp, or upgrades unauthenticated v1/v2 artifacts into
authenticated authority. Unknown or future formats fail closed.

## Fail-closed and consistency rules

Malformed, ambiguous, unknown, stale, expired, revoked or unauthorized state
and evidence fail closed. This includes invalid signatures, malformed scoped
evidence, missing required runtime gates, wrong project/provider/profile/domain
or revision, stale authority generations, incompatible schemas and
secret-bearing malformed evidence. Expected owner-attestor transport or I/O
outages become typed unavailable/ineligible results; this does not turn
unexpected programming errors into authorization.

Decisions are domain-bound. Revision-bound work requires the exact target
revision; revisionless documentation/library resolution deliberately uses the
documentation capability domain. Project/provider/profile/revision replay is
rejected, and decision lifetime is bounded by the source state lifetime.

## Consequences for integrations

T14 and later integrations consume this authority rather than creating a
parallel eligibility mechanism. Documentation adapters such as Context7 are
advisory evidence sources: they cannot authorize themselves, lower policy or
risk, expand `allowed_paths`, authorize delivery or merge, satisfy
`HUMAN_REQUIRED`, or create owner authority. The same boundary applies to
future provider integrations.

## Alternatives rejected

1. **Caller-provided gate booleans as authority.** Caller-controlled facts
   cannot create owner authorization and are restrictive observations only.
2. **`CapabilitySelector` as authority.** Ranking and fallback selection are
   deterministic decision logic, not trust.
3. **Self-authenticating public hashes.** Integrity is not authenticated
   issuance and is reproducible by a caller.
4. **Process-local runtime markers as durable authority.** They are mutable,
   transferable and lost on restart; they are not a durable trust primitive.
5. **A separate T14 documentation eligibility database.** It would create a
   parallel authority source and split policy semantics.
6. **Alternate or staging stores as production authority.** Caller-selected
   roots could replay copied or revoked state outside the canonical control
   plane.
7. **Historical provenance as future runtime authorization.** It would bypass
   current policy and invocation restrictions.

## Consequences

Benefits include one reusable provider-neutral authority boundary,
owner-governed trust, deterministic replay, durable authenticated evidence,
restart safety and explicit fail-closed behavior without duplicated authority
architecture.

Costs include stricter dependence on canonical owner state, explicit runtime
gate requirements, attestation lifecycle and bounded schema compatibility. An
arbitrary alternate authority root cannot be used in production, and
historical artifacts cannot be treated as permissions for future work.
