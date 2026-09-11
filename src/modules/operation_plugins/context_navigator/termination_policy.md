## Module completion criteria

## Recommended Minimum Phase Contract

Use these recommendations as the default phase decomposition. They are advisory guidance, not a mandatory phase count
or fixed plan schema. Adjacent recommendations may be merged only when the resulting phase explicitly preserves every
included capability, evidence requirement, and coverage outcome. Omit a recommendation only when it is demonstrably
inapplicable and document the reason.

1. **Access Context Characterization** — Document the granted channel, operating system, identity, privilege, host role,
   network position, and access boundaries before deeper enumeration.
2. **Environment and Trust-Boundary Mapping** — Map applicable interfaces, routes, applications, services, security
   controls, trust relationships, and business dependencies using low-impact evidence.
3. **Asset and Data-Location Mapping** — Catalog critical assets and sensitive data locations by metadata only.
4. **Follow-on Gap Identification** — Record uncertainty and material gaps, and create bounded follow-on questions
   without lateral movement.

Classify the phase against its own criteria and the following operation-level outcomes:

- The system role, granted identity and privilege, and access context are documented.
- Applicable system, identity, network, application, data-location, security-control, trust, and business layers are
  mapped, or each material gap has an artifact-backed scope, permission, or availability explanation.
- Critical assets and data locations are cataloged without accessing sensitive content.
- Contextual results are observations; any security finding is separately verified with reproducible evidence.
- Recommended follow-on work identifies important unanswered questions without performing lateral movement.

Use `done` only when the phase criteria and applicable mapping outcomes are evidence-backed. Use `partial_failure` when
useful context exists but material mapping or evidence is incomplete. Use `blocked` only when a concrete access,
permission, authorization, or environmental dependency prevents the phase work. Budget consumption alone is never a
completion requirement.
