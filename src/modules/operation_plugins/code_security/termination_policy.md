## Module completion criteria

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
