---
schema_version: 1
title: Policy-Based Connectivity Comparison with Identity Binding
purpose: Provide a reusable methodology for comparing allowed versus observed network
  connectivity under an explicit policy, with identity binding, controls, and evidence
  requirements to distinguish legitimate behavior from boundary violations.
applicability:
- network policy validation
- firewall rule auditing
- zero trust connectivity testing
- change control verification
phases:
- planning
- baseline
- controlled comparison
- interpretation
triggers:
- phases:
  - planning
  when: A policy change or new service introduces connectivity expectations that need
    verification against existing rules.
  request: Describe a reusable policy-based connectivity comparison with identity
    binding, controls and evidence requirements.
  signals_any: []
sources:
- url: https://man.openbsd.org/pf.conf
  title: OpenBSD pf.conf manual
  retrieved_at: ''
  selection_reason: OpenBSD pf.conf manual provides authoritative technical facts
    about pf rule evaluation order and state creation that can support a policy-based
    connectivity comparison methodology. The retained excerpts state 'the last matching
    rule decides what action is taken' and 'the first time a packet matches a pass
    rule, a state entry is created.', which are factual basis for describing rule
    precedence and stateful tracking as components of a connectivity policy baseline.
active: true
source_coverage: 'Considered 1 fetched documents; selected 1. {"requirements": [{"id":
  "method", "description": "Describe a reusable policy-based connectivity comparison
  with identity binding, controls and evidence requirements. No target execution or
  particular command syntax is requested.", "kind": "authored_deliverable", "grounding":
  "knowledge_allowed", "origins": []}]}

  Required-source obligations ready: True; references selected: 1. Retained 1 sources.
  Latest source assessment: The current document is an official OpenBSD manual page,
  giving it high authority for pf.conf-specific claims. The retained excerpts contribute
  at least one relevant supported technical fact about rule matching order and state
  entry creation that can be used in original procedural synthesis for policy-based
  connectivity comparison. The contribution is limited by the bounded excerpt and
  lack of coverage of identity binding, controls, and evidence requirements. Authority
  for the document''s own relevant contributions is sufficient; coverage for the full
  request is incomplete and would require additional sources.

  Knowledge-allowed content is not source-verified. Reference readiness does not establish
  document completeness or correctness.

  {"coverage": [{"requirement_id": "method", "source_urls": [], "explanation": "Model
  knowledge and reasoning are allowed; this requirement is not source-verified.",
  "authority_sufficient": null, "claim_support": [], "authoring_obligations": [],
  "synthesis": "", "gaps": [], "future_validation": []}]}'
revision_id: 5086df87-b4b5-52d8-a99c-4462cd302818
lineage: generated:e39653c9-3338-4db1-900e-ed7bcbf4204c
origin: generated
source_sha256: 11b3c8a6b3174334fae5bdd5aeea809c7af322312f1ac761350db76fd93e11aa
inactive_reason: ''
sentiment_count: 0
sentiment_mean: 0
feedback_reasons: []
---

# Policy-Based Connectivity Comparison with Identity Binding


## Purpose

Establish a repeatable way to compare an explicit connectivity policy to observed network behavior, binding tests to identities, and collecting evidence sufficient to distinguish legitimate access from policy violations.

## Prerequisites

1. **Policy artifact.** Written allow/deny matrix: source identity, destination identity, service/port, direction, and time constraints. Identity is defined as user, service account, workload, or device, not just IP.

2. **Identity binding mechanism.** Mapping from network identifiers to identities, e.g., certificate SAN, device certificate, mTLS client identity, or authenticated proxy log. Document how binding is enforced.

3. **Baseline inventory.** Current effective ruleset export and state table schema. For pf.conf-style systems, note that the last matching rule decides action and the first time a packet matches a pass rule a state entry is created.

4. **Test harness.** Isolated test identities and test endpoints that can generate controlled traffic without affecting production workloads.

5. **Logging and observability.** Packet filter logs, flow logs, and identity-aware proxy logs with timestamps, 5-tuple, and identity fields.


## Controls

- **Identity isolation:** Use dedicated test identities per test case. Never reuse production credentials.

- **Temporal bounding:** Run tests in a defined window with start/stop markers in logs.

- **Single variable change:** Change only one dimension at a time: source identity, destination, port, or time window.

- **Negative controls:** Include tests that must be denied by policy.

- **Positive controls:** Include tests that must be allowed by policy.


## Procedure

### 1. Establish Policy and Prerequisite Evidence

1. Extract the authoritative policy document and map each rule to: source identity, destination identity, service, action, and justification.

2. Record how identity binding is enforced: e.g., mTLS verification, RADIUS attribute, or device posture check. Capture configuration excerpts and version identifiers.

3. Future check: Verify binding enforcement is active during test window by inspecting authentication logs for the test identity.


### 2. Build Baseline

1. Export effective ruleset and normalize to a canonical table: source, destination, service, action.

2. Capture current state table snapshot before testing. Note rule matching order semantics for the platform.

3. Record baseline flow logs for the test window without test traffic to establish noise floor.


### 3. Design Test Matrix

Create cases covering:

- Allowed identity → allowed destination → allowed service

- Allowed identity → denied destination → allowed service

- Denied identity → allowed destination → allowed service

- Allowed identity → allowed destination → denied service

Each case includes expected outcome per policy.

### 4. Execute Controlled Comparison

1. Initiate traffic from test source identity to test destination using the specified service.

2. Simultaneously capture:

   - Identity binding evidence: authentication token, certificate, or proxy log showing identity assertion.

   - Network evidence: flow record with 5-tuple, timestamp, action allow/deny, rule hit identifier.

   - State evidence: state entry creation or absence, with creation time.

3. Repeat for each matrix case with fresh identity sessions.


### 5. Evidence Requirements

For each test case retain:

- Policy reference ID and version

- Identity binding proof: identity value, binding mechanism, timestamp

- Request metadata: source IP, destination IP, port, protocol, timestamp

- Decision evidence: rule matched, action taken, log excerpt

- State evidence: state entry present/absent, creation time

- Test operator and test window markers


## Interpretation

- **Legitimate behavior:** Observed action matches policy for the bound identity and all evidence fields are present and consistent.

- **Boundary violation hypothesis:** Observed action contradicts policy for the bound identity. Requires both policy mismatch and intact identity binding evidence. Do not label a violation on IP alone without identity binding proof.

- **Inconclusive:** Missing identity binding evidence or log gap. Treat as future check, not a finding.


## Limitations

- Identity binding strength depends on the enforcement point. Network-only tests cannot prove application-layer identity without proxy logs.

- Rule matching order and state creation semantics are platform specific. The retained OpenBSD pf.conf excerpt notes last matching rule decides action and state entry creation on first pass match; other platforms differ.

- Time-based policies require synchronized clocks and sufficient test duration.


## Optional Tool Choices

- Log aggregation: any SIEM or log store supporting correlated identity and flow logs.

- Test generation: scripted HTTP/TCP probes using test identities.

- Evidence packaging: structured JSON per test case with references to policy IDs.
