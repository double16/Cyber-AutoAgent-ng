---
schema_version: 1
title: Policy-Based Connectivity Comparison with Identity Binding
purpose: Provide a reusable methodology for comparing observed connectivity against
  an explicit policy baseline while binding results to identities, with controls and
  evidence requirements to distinguish legitimate behavior from boundary violations.
applicability:
- network access review
- firewall policy validation
- zero trust connectivity auditing
phases:
- planning
- baseline
- comparison
- validation
triggers:
- phases:
  - planning
  when: A policy change or audit scope is defined requiring evidence that connectivity
    matches authorized identity-bound rules.
  request: Describe a reusable policy-based connectivity comparison with identity
    binding, controls and evidence requirements.
  signals_any: []
sources:
- url: https://man.openbsd.org/pf.conf
  title: OpenBSD pf.conf manual
  retrieved_at: ''
  selection_reason: OpenBSD pf.conf manual is an authoritative reference for pf packet
    filter semantics. The retained excerpts provide supported technical facts that
    rule evaluation is ordered with 'the last matching rule decides what action is
    taken' and that 'the first time a packet matches a pass rule, a state entry is
    created'. These facts can support original procedural synthesis for policy-based
    connectivity comparison, e.g., establishing rule precedence and state creation
    as observable baseline behavior in a controlled test.
active: true
source_coverage: 'Considered 1 fetched documents; selected 1. {"requirements": [{"id":
  "method", "description": "Describe a reusable policy-based connectivity comparison
  with identity binding, controls and evidence requirements. No target execution or
  particular command syntax is requested.", "kind": "authored_deliverable", "grounding":
  "knowledge_allowed", "origins": []}]}

  Required-source obligations ready: True; references selected: 1. Retained 1 sources.
  Latest source assessment: The current document is an authoritative primary source
  for OpenBSD pf configuration semantics. The retained excerpts supply at least one
  relevant supported technical fact about rule matching order and state entry creation
  that can be used as a building block for a reusable policy-based connectivity comparison
  methodology. The source does not cover identity binding, controls design, or evidence
  requirements requested, and the excerpt is bounded with unknown omitted text, so
  those mechanisms remain unsupported by this source and must be derived from model
  knowledge or additional sources.

  Knowledge-allowed content is not source-verified. Reference readiness does not establish
  document completeness or correctness.

  {"coverage": [{"requirement_id": "method", "source_urls": [], "explanation": "Model
  knowledge and reasoning are allowed; this requirement is not source-verified.",
  "authority_sufficient": null, "claim_support": [], "authoring_obligations": [],
  "synthesis": "", "gaps": [], "future_validation": []}]}'
revision_id: 277423b0-594f-59d8-aea6-9d2c963b19a7
lineage: generated:7c822ccd-aa7e-42ac-8e50-1d576ccdd666
origin: generated
source_sha256: dfef765bf0a929543d3f89690aa433603545d128d92943b50198b132d03259cc
inactive_reason: ''
sentiment_count: 0
sentiment_mean: 0
feedback_reasons: []
---

# Policy-Based Connectivity Comparison with Identity Binding, Controls and Evidence Requirements<|message|>## Purpose and Scope  This technique describes how to build a reusable, policy-based comparison between an authoritative connectivity policy and observed connectivity, with identity binding to ensure that allow/deny decisions are evaluated per principal, not per IP alone. It provides prerequisites, controls, evidence requirements, and interpretation rules to distinguish legitimate behavior from boundary violations.  No target execution is performed; all steps are future procedure imperatives.  ## Prerequisites  1. **Policy artifact.** Obtain a machine-readable or human-readable policy specification that defines:  * Allowed source identities, e.g., user, service account, workload identity, device certificate principal.  * Destination identities or service names, ports, and protocols.  * Direction, conditions, and time windows.  * Explicit deny exceptions.  2. **Identity binding mechanism.** Identify how network enforcement binds traffic to identity: e.g., TLS client identity, mutual TLS SPIFFE ID, VPN user certificate, 802.1X, or identity-aware proxy headers. Document the trust boundary where binding is performed and how it is attested.  3. **Observation capability.** Define how connectivity observations are collected without altering enforcement: packet metadata logs, flow records, firewall rule hit counters, or identity-aware proxy audit logs.  4. **Baseline establishment.** Create a baseline of expected connectivity for each identity under normal operations, derived from policy and configuration.  ## Methodology  ### 1. Establish Policy and Identity Mapping  * Translate policy into a comparison table: source identity → destination identity/service → protocol/port → action → valid time window.  * Record how each identity is mapped to network identifiers at enforcement points. Note limitations: IP reuse, NAT, and header spoofing risks.  * Document the enforcement point where identity binding is verified, e.g., ingress proxy validates mTLS and then applies policy.  ### 2. Define Controls  * **Control separation:** Policy author, observation collector, and comparator are distinct roles.  * **Reproducibility:** Use versioned policy snapshots and timestamped observation windows.  * **Tamper resistance:** Collect logs from immutable storage with integrity hashes.  * **Identity attestation:** Require cryptographic proof of identity at the enforcement point, not inferred from IP.  ### 3. Collect Evidence  For each observation window collect:  * Timestamped flow or access log entry with source identity claim, destination identity/service, protocol/port, action taken.  * Identity attestation artifact: certificate serial, SPIFFE ID, JWT subject, or proxy audit record.  * Enforcement point identifier and policy version in effect.  * Rule matching evidence: rule identifier or policy decision record showing which rule matched.  Evidence requirements:  * Completeness: all flows for scoped identities within window.  * Correlation: identity claim must be linkable to attestation artifact.  * Freshness: collection time within acceptable skew of enforcement.  ### 4. Perform Comparison  1. Normalize observations to policy schema: map observed identities, services, ports to policy entries.  2. For each observed flow:  * Check identity binding present and valid per attestation.  * Check policy entry exists for source→destination→protocol/port at time of flow.  * Compare action taken vs policy action.  3. Classify outcomes:  * **Allowed and expected:** identity bound, policy permits, action matches.  * **Allowed but unexpected:** identity bound, policy permits, but not in baseline. Flag for review.  * **Denied as policy:** identity bound, policy denies, action denies.  * **Policy violation:** identity bound, policy denies but action allows, or identity unbound and action allows.  * **Insufficient evidence:** missing identity attestation or incomplete log.  ### 5. Interpretation Rules  * Legitimate behavior requires both policy match and valid identity binding at the enforcement point.  * Boundary violation requires demonstrated mismatch between policy decision and enforcement action with valid identity binding.  * Do not infer violation from IP alone when identity binding is required by policy.  * Unknown policy gaps are recorded as limitations, not violations.  ### 6. Reporting and Future Validation  * Produce a comparison report per identity with: policy reference, observed flows, classification, evidence pointers.  * List prerequisites not yet established, e.g., missing attestation logs.  * Define future validation steps: re-collect with longer window, add identity logging, or update policy artifact.  ## Limitations  * Source coverage: retained OpenBSD pf.conf excerpts confirm rule matching order and state entry creation semantics, but do not cover identity binding, controls design, or evidence requirements. Those mechanisms are derived from model knowledge.  * Identity binding quality depends on enforcement point trust. Without cryptographic attestation, comparison degrades to IP-based approximation.  * NAT and multi-hop paths can break source identity mapping; treat as future check.  ## Optional Tool Choices  * Policy modeling: Open Policy Agent, Cedar, or spreadsheet with version control.  * Log collection: eBPF flow exporter, firewall logs, identity proxy audit logs.  * Comparison: Python 3.13+ script that loads policy JSON and log JSON and emits classification table.  Example sketch:  ```python import json, sys  def load_policy(path):  with open(path) as f:  return json.load(f)  def load_logs(path):  with open(path) as f:  return json.load(f)  def classify(policy, logs):  results = []  for e in logs:  key = (e['src_identity'], e['dst_service'], e['port'], e['proto'])  entry = policy.get(key)  if not entry:  results.append({**e, 'class':'unexpected'})  continue  if e['action'] != entry['action']:  results.append({**e, 'class':'policy_violation'})  else:  results.append({**e, 'class':'allowed_expected'})  return results ```  This is illustrative only; adapt to actual schemas and attestation checks.  ## Triggers  * Policy change review, compliance audit, or incident hypothesis requiring identity-bound connectivity evidence.  * New identity-aware enforcement deployment needing baseline validation.
