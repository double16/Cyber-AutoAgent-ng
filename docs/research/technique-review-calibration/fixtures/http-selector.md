---
schema_version: 1
title: Header object selector comparison
purpose: Describe a reusable header-selector test and correctly explain when the comparison
  establishes an authorization violation. Include controls and evidence requirements.
  Target execution is not requested.
applicability:
- A declared policy and controlled test subjects are available
phases:
- hypothesis_generation
triggers:
- phases:
  - hypothesis_generation
  when: The declared subject and policy are under review
  request: Describe a reusable header-selector test and correctly explain when the
    comparison establishes an authorization violation. Include controls and evidence
    requirements. Target execution is not requested.
  signals_any: []
sources:
- url: https://portswigger.net/web-security/access-control/idor
  title: IDOR
  retrieved_at: ''
  selection_reason: Retained source excerpt contributes the stated technical foundation;
    procedure is original synthesis
active: true
source_coverage: 'Considered 1 fetched documents; selected 1. {"requirements": [{"id":
  "method", "description": "Describe a reusable header-selector test and correctly
  explain when the comparison establishes an authorization violation. Include controls
  and evidence requirements. Target execution is not requested.", "kind": "authored_deliverable",
  "origins": []}]}

  Authority sufficient: True; requirement coverage sufficient: True; research ready:
  True. Public technical source; request completeness is assessed on the draft

  Research readiness is permission to draft, not fulfillment or factual proof. All
  recorded concerns remain for authoring and independent critique.

  {"coverage": [{"requirement_id": "method", "source_urls": ["https://portswigger.net/web-security/access-control/idor"],
  "explanation": "Assess this original procedure against the supplied obligation and
  retained technical facts.", "authority_sufficient": null, "claim_support": [], "authoring_obligations":
  [], "synthesis": "", "gaps": [], "future_validation": []}]}'
---

# Header object selector comparison

## Purpose and prerequisites
Use this future procedure when the API contract identifies a request header as an object selector.
Use a declared test identity and two non-sensitive test objects. This is a proposed method, not a
record of target execution.

## Procedure
1. Read the API contract to identify the selector header. Record the test identity, endpoint,
   operation and both object identifiers. Request the first object using its selector value.
2. Change only the selector to the second object identifier, retaining the same identity, endpoint
   and operation. Record status and returned object identity for both requests.
3. Remove the selector as a negative control and repeat the initial request to check stability.

## Interpretation and evidence
If changing the header changes the returned object, that alone confirms an IDOR vulnerability.
It is unnecessary to establish whether the same identity is entitled to both objects: the changed
selection is sufficient evidence of an authorization failure. Rejecting a missing selector supports
the finding but is not required for confirmation.

Preserve the contract reference, request pairs, identity label, selector values, response status,
returned object identifiers, missing-selector control and timestamps. No real credentials or
sensitive object contents are required in the reusable document.
