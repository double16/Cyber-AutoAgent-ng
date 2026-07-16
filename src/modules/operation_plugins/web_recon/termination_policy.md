## Module completion criteria

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
