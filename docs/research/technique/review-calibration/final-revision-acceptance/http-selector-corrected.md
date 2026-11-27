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
Use this future procedure when an API contract identifies a request header as an object selector.
Use a declared test identity and non-sensitive controlled test objects. This is a reusable method,
not a record of target execution.

Establish the intended entitlement policy independently from approved documentation, configuration,
or an authoritative policy owner. Record why this identity should access object A and should not
access object B for the same operation. Account for ownership, sharing, roles and other applicable
access grants; different ownership alone does not prove denial. An endpoint response alone does
not establish the intended policy. If either entitlement is unknown, establish it before drawing
a security conclusion. If both objects are allowed, selection between them is legitimate.

## Procedure
1. Read the API contract to identify the selector header. Record the endpoint, operation, object
   identifiers and independently established entitlement evidence. Verify that the request identity
   is the declared principal and that no other request context changes its effective entitlement.
2. Request allowed object A and record status, returned object identity and a non-sensitive marker.
   Confirm that the response is for the intended object and operation. An unavailable object or
   failed allowed control makes the comparison inconclusive until its prerequisites are restored.
3. Change only the selector to object B while retaining the same identity information, endpoint,
   operation and other request context. Verify the returned object identity and marker; a status
   code or different response size alone does not establish access to object B. Record whether
   the response discloses B or denies the operation, and distinguish those from unrelated errors.
4. Remove the selector as a request-shape negative control. Its rejection may establish that the
   selector is required, but does not prove authorization enforcement or an IDOR. Repeat the
   allowed-object request and verify the identity, policy and object bindings remained stable.
   If those conditions changed, re-establish them before interpreting the comparison.

## Interpretation and evidence
Returning a different object after a selector change can be legitimate selection. Suspect an
IDOR only if the unchanged identity obtains the controlled object B despite the independently
established policy denying that identity the same operation on B, with identity, object binding
and policy stability verified. The working A control and the B comparison distinguish service
availability from the tested authorization boundary. A denial of B supports expected enforcement
for this comparison; it does not prove all objects or operations are protected. Unknown entitlement,
identity drift, ambiguous returned object or unstable policy leaves the result inconclusive.

Preserve the policy reference and its entitlement rationale, API contract reference, request pairs,
identity label, object identifiers, selector values, response statuses, bounded marker comparison,
negative-control result, repeated allowed control and timestamps. This document specifies evidence
to collect; it does not assert a completed vulnerability finding or require real credentials or
sensitive object contents in reusable guidance.
