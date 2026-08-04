## Module completion criteria

## Recommended Minimum Phase Contract

Use these recommendations as the default phase decomposition. They are advisory guidance, not a mandatory phase count
or fixed plan schema. Adjacent recommendations may be merged only when the resulting phase explicitly preserves every
included capability, evidence requirement, and coverage outcome. Omit a recommendation only when it is demonstrably
inapplicable and document the reason.

1. **Attack Surface Mapping** — Produce and freeze the bounded inventory of authorized services, applications,
   endpoints, parameters, authentication, roles, tenants, and important workflows.
2. **Generate Attack Hypotheses from the Mapped Attack Surface** — Derive testable hypotheses from technology,
   input, trust-boundary, and workflow observations. Do not treat a hypothesis as a finding.
3. **Vulnerability Discovery and Exploitability Testing** — Test prioritized hypotheses and record expected and actual
   behavior, negative controls, reproducibility, and evidence-backed vulnerability candidates.
4. **Exploit Chain Analysis** — Determine whether multiple weaknesses combine into a higher-impact attack path. Record
   prerequisites, transitions, failed links, alternative branches, and evidence for each link. Mark this phase
   `not_applicable` when no evidenced candidates can compose into a meaningful relationship. Analyze existing candidates
   rather than repeating vulnerability discovery or introducing unrelated pivots. Create follow-on execution work only
   for a concrete, evidence-backed chain link that still requires validation.
5. **Finding Validation** — Confirm or reject each vulnerability candidate or attack path using reproducible evidence,
   expected-versus-actual behavior, negative controls, scope, confidence, and artifact paths.
6. **Impact Demonstration** — Safely demonstrate the minimum necessary security consequence required by the objective,
   without destructive action or unnecessary data access.

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
