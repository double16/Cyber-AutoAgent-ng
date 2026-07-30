## Module completion criteria

## Recommended Minimum Phase Contract

Use these recommendations as the default phase decomposition. They are advisory guidance, not a mandatory phase count
or fixed plan schema. Adjacent recommendations may be merged only when the resulting phase explicitly preserves every
included capability, evidence requirement, and coverage outcome. Omit a recommendation only when it is demonstrably
inapplicable and document the reason.

1. **Repository Attack Surface Characterization** — Identify the authorized repository structure, languages,
   frameworks, dependency sources, entry points, trust boundaries, and high-value security paths.
2. **Generate Security Hypotheses from the Codebase** — Derive testable hypotheses from dependencies, secrets,
   security patterns, data flows, and business logic without treating tool matches as findings.
3. **Vulnerability Analysis** — Apply applicable static, dependency, secret, data-flow, and business-logic analysis;
   preserve unverified theoretical risks as observations.
4. **Data-Flow and Exploit-Path Analysis** — Determine whether multiple weaknesses or code paths combine into a material
   security consequence, recording prerequisites and evidence for each link. Mark this phase `not_applicable` when no
   evidenced candidates or code paths can compose. Analyze existing candidates and data flows rather than repeating
   vulnerability discovery; create follow-on analysis only for a concrete unresolved path link.
5. **Finding Validation** — Validate candidates in surrounding code and deployment context with exact locations and
   reproducible evidence.
6. **Impact Assessment** — Establish security impact and provide practical remediation for each verified vulnerability.
7. **Coverage Closure** — Account for analyzed, excluded, unreachable, duplicated, and unresolved repository areas.

Classify the phase against its own criteria and the following operation-level outcomes:

- The authorized repository, languages, frameworks, dependency sources, and relevant attack surfaces are characterized.
- Applicable dependency, secret, security-pattern, dataflow, and business-logic analysis is evidenced, or an explicit
  artifact-backed reason explains why a layer does not apply or could not be completed.
- Every reported vulnerability has an exact file and line, contextual exploitability analysis, impact, remediation,
  validation status, and supporting artifact.
- Tool matches and theoretical risks that were not contextually verified remain observations rather than findings.

Use `done` only when the phase criteria and applicable outcomes above are evidence-backed. Use `partial_failure` when
useful analysis exists but material coverage, validation, or remediation is missing. Use `blocked` only for a concrete
external dependency, authorization limit, unavailable repository content, or capability that prevents the phase work.
Budget consumption alone is never a completion requirement.
