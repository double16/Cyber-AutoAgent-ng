## Module completion criteria

Classify the phase against its own criteria and the following operation-level outcomes:

- Authorized services, applications, endpoints, parameters, authentication, roles, tenants, and important workflows
  are covered or have artifact-backed exclusion, unreachability, or duplication reasons.
- Reported vulnerabilities demonstrate unauthorized behavior or security impact with expected and actual results,
  negative controls, reproduction steps, validation status, and artifact paths.
- Configuration clues and unverified hypotheses remain observations rather than findings.
- High-risk capabilities are validated to the minimum safe impact required by the objective, without destructive action.

Use `done` only when the phase criteria and applicable coverage and validation outcomes are evidence-backed. Use
`partial_failure` when useful evidence exists but material coverage or validation remains unsupported. Use `blocked`
only for a concrete authorization, access, target-availability, rate, identity, or capability dependency. Budget
consumption alone is never a completion requirement.
