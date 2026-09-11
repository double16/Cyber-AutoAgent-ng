## Module completion criteria

## Recommended Minimum Phase Contract

Use these recommendations as the default phase decomposition. They are advisory guidance, not a mandatory phase count
or fixed plan schema. Adjacent recommendations may be merged only when the resulting phase explicitly preserves every
included capability, evidence requirement, and coverage outcome. Omit a recommendation only when it is demonstrably
inapplicable and document the reason. Keep all coverage and gap documentation within these operational phases.

1. **Surface and Trust-Boundary Mapping** — Map services, entry points, technology, authentication, sessions, roles,
   tenants, user journeys, and endpoint parameters with artifact evidence.

### Phase-1 task fan-out

Create separate artifact-producing tasks for at least three applicable workstreams: service and entry-point mapping,
technology and trust-boundary mapping, access-context/session mapping, workflow/high-value-area mapping, and safe
read-only verification. Do not combine these into one broad mapping task or cross the module's non-exploitation boundary.
2. **Access Context Mapping** — Compare applicable access contexts and document authenticated coverage or the absence
   of authorized test credentials.
3. **Workflow and High-Value Area Mapping** — Map prioritized workflows and high-value areas.
4. **Safe Security Verification** — Perform read-only behavioral checks with negative controls and preserve verified
   weakness candidates.
5. **Coverage Gap Documentation** — Document unmapped or unassessed areas without exploitation.

Classify the phase against its own criteria and the following operation-level outcomes:

Distribute these outcomes across the applicable operational phases. They are completion criteria, not a request for a
separate consolidation, summary, or reporting phase.

- Authorized services and entry points, authentication and session behavior, observed roles and tenant boundaries,
  technology, major user journeys, and prioritized endpoints are mapped with artifact evidence.
- The evidence documents at least three endpoints with parameter names and access requirements, or proves that the
  target exposes fewer applicable endpoints.
- Applicable high-value areas receive safe read-only verification or an artifact-backed explanation of why they could
  not be assessed.
- Mapping and uncertain signals remain observations; findings are limited to safely verified weaknesses with negative
  controls and bounded impact.
- No work crosses from verification into exploitation, destructive testing, privilege escalation, or data exfiltration.

Use `done` only when the phase criteria and applicable mapping outcomes are evidence-backed. Use `partial_failure` when
useful mapping exists but material coverage or verification is incomplete. Use `blocked` only for a concrete
authorization, access, target-availability, identity, rate, or capability dependency. Budget consumption alone is never
a completion requirement.
