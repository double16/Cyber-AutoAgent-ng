## Module completion criteria

## Recommended Minimum Phase Contract

Use these recommendations as the default phase decomposition. They are advisory guidance, not a mandatory phase count
or fixed plan schema. Adjacent recommendations may be merged only when the resulting phase explicitly preserves every
included capability, evidence requirement, and coverage outcome. Omit a recommendation only when it is demonstrably
inapplicable and document the reason. The final flag-confirmation capability remains applicable whenever the objective
requires a flag.

1. **Challenge Surface Mapping** — Characterize the bounded inventory of authorized hosts, endpoints, parameters, roles,
   literal hints, and capability classes. Document duplicates, exclusions, and unreachable paths with artifacts.

### Phase-1 task fan-out

Create separate artifact-producing tasks for at least three applicable workstreams: challenge hints, endpoint and
capability discovery, access-context mapping, and flag-path investigation. Queue one challenge-surface synthesis task
after the mapping tasks. A one-task direct flag path is allowed only when its structured exception records a concrete
reason.
2. **Generate Exploit Hypotheses from the Challenge Surface** — Derive detailed, testable exploit paths from challenge
   hints, exposed capabilities, inputs, and trust boundaries. For each path, record preconditions, the suspected
   mechanism and attacker-controlled flow, safe future test steps, expected positive and negative/control results,
   and required evidence. Research relevant challenge technologies, versions, techniques, advisories, CVEs, and
   published PoC preconditions when they provide a lead; they are not proof of applicability. Do not treat clues or a
   prior hypothesis as findings or completion.
3. **Exploit Testing** — Test prioritized hypotheses with expected and actual results, negative controls, and artifacts.
4. **Exploit Chain Analysis** — Record each prerequisite, transition, required server-side acceptance, failed links,
   and alternative branches. Preserve failed links and alternative branches as evidence-backed observations. Mark this
   phase `not_applicable` for a direct single-step flag path or when no evidenced candidates can compose. This phase
   analyzes existing candidates and relationships; it does not repeat exploit discovery or introduce unrelated pivots.
   Create follow-on execution work only for a concrete, evidence-backed chain link that still requires validation.
5. **Flag Retrieval and Confirmation** — Demonstrate the minimum authorized direct path or end-to-end chain to every
   required flag, capture exact reproducible evidence, and reject placeholders.
Every claimed capability chain has evidence for each link's prerequisite, action, observed transition, and artifact. A
direct single-step flag path does not require artificial chain records.

The operation-level objective is satisfied only when every required flag is captured and supported by:

- an artifact containing the exact flag value or command output that clearly shows it;
- preconditions and exact reproduction request or command;
- expected and actual results; and
- validation that the candidate is not an obvious placeholder and is consistent with available challenge evidence.

Vulnerability and objective validation are independent. A reproducible command injection, file read, authorization
bypass, or other capability remains a verified finding when the extracted flag candidate is rejected. Conversely, a
verified vulnerability is not proof that the flag objective is complete. Objective completion requires a separately
verified objective candidate with confidence of at least 80%; rejected and inconclusive candidates require another
candidate search unless a concrete terminal constraint is evidenced.

For a phase, use `done` only when its own criteria are evidence-backed. A phase may be done before flag capture when its
criteria cover an intermediate capability or discovery outcome; the plan must retain later criteria that lead to flag
capture. Use `partial_failure` when useful evidence exists but the phase criteria cannot be fully supported. Use
`blocked` only for a concrete external authorization, access, target availability, or capability dependency. Do not
declare the overall operation successful without the required evidenced flag, and do not require arbitrary budget use.
