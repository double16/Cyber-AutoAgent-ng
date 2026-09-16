## Module completion criteria

## Recommended Minimum Phase Contract

Use these recommendations as the default phase decomposition. They are advisory guidance, not a mandatory phase count
or fixed plan schema. Adjacent recommendations may be merged only when the resulting phase explicitly preserves every
included capability, evidence requirement, and coverage outcome. Omit a recommendation only when it is demonstrably
inapplicable and document the reason.

1. **Attack Surface Mapping** — Produce and freeze the bounded inventory of authorized services, applications,
   endpoints, parameters, authentication, roles, tenants, and important workflows.

### Phase-1 task fan-out

Create separate artifact-producing tasks for at least three applicable workstreams: entry-point and technology mapping,
bounded crawl and route discovery, client-side/API extraction, and authentication/workflow mapping. Create one final
inventory-synthesis task only after those mapping tasks are queued; it is the sole task that produces the canonical
inventory manifest.
2. **Authentication Coverage and Authorization Controls** — Preserve an unauthenticated baseline for each applicable
   mapped authentication workflow, then use eligible target-scoped credentials through the credential store for each
   applicable account and role. Authenticate with the observed login, API-key, OAuth2, or MFA flow, bind the task
   authentication context, and document durable evidence for each result. Use credential-ID-backed account, role, or
   tenant comparisons for authorized access-control testing; when an identity, MFA factor, or comparison pair is
   unavailable, record the specific coverage gap. A normal public sign-up flow may create a reusable registered test
   identity unless an operation constraint prohibits that state change.

### Phase-2 task fan-out

Create separate artifact-producing tasks for unauthenticated baseline, authenticated credential coverage, and
authorization comparison. Each task must document a bounded result or an evidence-backed inapplicability/coverage gap;
do not use raw credential values in task descriptions, artifacts, or IDOR specialist calls.

3. **Generate Attack Hypotheses from the Mapped Attack Surface** — Derive detailed, testable attack paths from
   technology, input, trust-boundary, and workflow observations. For each path, record preconditions, the suspected
   mechanism and attacker-controlled flow, safe future test steps, expected positive and negative/control results,
   and required evidence. Research relevant technologies, versions, components, advisories, CVEs, and published PoC
   preconditions when they provide a lead; they are not applicability evidence or findings. Do not treat a hypothesis
   as a finding or consider a prior hypothesis alone complete.
4. **Vulnerability Discovery and Exploitability Testing** — Test prioritized hypotheses and record expected and actual
   behavior, negative controls, reproducibility, and evidence-backed vulnerability candidates.
5. **Finding Validation** — Confirm or reject each vulnerability candidate using reproducible evidence,
expected-versus-actual behavior, negative controls, scope, confidence, and artifact paths. This phase must complete
before any phase that consumes verified findings.
6. **Exploit Chain Analysis** — Determine whether multiple verified weaknesses combine into a higher-impact attack path. Record
   prerequisites, transitions, failed links, alternative branches, and evidence for each link. Mark this phase
   `not_applicable` when no verified candidates can compose into a meaningful relationship. Analyze existing candidates
   rather than repeating vulnerability discovery or introducing unrelated pivots. Create follow-on execution work only
   for a concrete, evidence-backed chain link that still requires validation.
7. **Impact Demonstration** — Safely demonstrate the minimum necessary security consequence required by the objective,
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
