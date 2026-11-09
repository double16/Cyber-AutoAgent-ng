---
schema_version: 1
title: Network policy comparison
purpose: Describe a reusable policy-based connectivity comparison with identity binding,
  controls and evidence requirements. No target execution or particular command syntax
  is requested.
applicability:
- A declared policy and controlled test subjects are available
phases:
- hypothesis_generation
triggers:
- phases:
  - hypothesis_generation
  when: The declared subject and policy are under review
  request: Describe a reusable policy-based connectivity comparison with identity
    binding, controls and evidence requirements. No target execution or particular
    command syntax is requested.
  signals_any: []
sources:
- url: https://man.openbsd.org/pf.conf
  title: OpenBSD pf.conf manual
  retrieved_at: ''
  selection_reason: Retained source excerpt contributes the stated technical foundation;
    procedure is original synthesis
active: true
source_coverage: 'Considered 1 fetched documents; selected 1. {"requirements": [{"id":
  "method", "description": "Describe a reusable policy-based connectivity comparison
  with identity binding, controls and evidence requirements. No target execution or
  particular command syntax is requested.", "kind": "authored_deliverable", "origins":
  []}]}

  Authority sufficient: True; requirement coverage sufficient: True; research ready:
  True. Public technical source; request completeness is assessed on the draft

  Research readiness is permission to draft, not fulfillment or factual proof. All
  recorded concerns remain for authoring and independent critique.

  {"coverage": [{"requirement_id": "method", "source_urls": ["https://man.openbsd.org/pf.conf"],
  "explanation": "Assess this original procedure against the supplied obligation and
  retained technical facts.", "authority_sufficient": null, "claim_support": [], "authoring_obligations":
  [], "synthesis": "", "gaps": [], "future_validation": []}]}'
---

# Network policy comparison

## Purpose and prerequisites
This procedure describes future controlled testing of a packet-filter rule set. It makes no claim
about a current target. Use declared test peers and a test service only. Establish the intended
allowed and denied source/service pairs from the approved policy, then inspect the effective
configuration, rule ordering and state handling at the enforcement point. If intended policy,
loaded configuration or the relevant enforcement point is unknown, establish it before interpreting
a connection as a violation.

## Procedure
1. Record the service identity, destination address and port, protocol, enforcement interface and
   effective rule-set revision. Select one policy-allowed and one policy-denied peer for that same
   service. Verify the source identity actually visible at the enforcement point, including any NAT
   or proxy mapping; a local peer label alone is insufficient.
2. Verify that the test reaches the intended service through that enforcement point. Record the
   effective source/destination tuple and a service-side observation. Routing failure, another
   service, or uncertain identity binding leaves the comparison inconclusive.
3. Establish an allowed-peer baseline using a fresh connection. Then attempt the same operation
   from the independently verified denied peer, holding service, protocol and operation constant.
   Account for pre-existing connection state according to the controlled test environment; do not
   remove unrelated operational state. Record which rule and state decision applied when available.
4. Repeat the allowed control to check that the service remained available. If policy, identity,
   routing or service availability changed, establish those conditions again before interpreting it.

## Interpretation and evidence
Allowed connectivity is legitimate. A denied peer failing to connect supports the expected outcome
only when the control confirms that the intended service was reachable. Suspect an enforcement
failure only when the independently established denied identity reaches that protected service
under the stated policy, with the identity mapping and path verified. Mere response differences
or successful connectivity from an unspecified peer establish no violation.

Preserve the approved policy reference, effective rule-set revision, state considerations, source
identity mapping, destination/service identity, connection attempts, timestamps and service-side
observations. These are proposed evidence requirements, not claims of completed tests. The source
supplies rule-evaluation and connection-state facts; the comparison is an original synthesis.
