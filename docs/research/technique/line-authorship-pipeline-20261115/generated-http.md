---
schema_version: 1
title: Reusable Header-Selector Authorization Test
purpose: Provide a reusable methodology for testing whether an application uses a
  client-supplied HTTP header to select an object or principal, and for correctly
  interpreting when a comparison between baseline and variant responses establishes
  an authorization violation.
applicability:
- Web applications using HTTP headers for object or principal selection
- Access-control testing for IDOR-like header-based selection
phases:
- reconnaissance
- vulnerability analysis
triggers:
- phases:
  - reconnaissance
  - vulnerability analysis
  when: An endpoint returns user-specific data and accepts a client-controlled header
    that could influence object selection.
  request: Describe a reusable header-selector test and explain when comparison establishes
    an authorization violation with controls and evidence requirements.
  signals_any: []
sources:
- url: https://portswigger.net/web-security/access-control/idor
  title: IDOR
  retrieved_at: ''
  selection_reason: The retained excerpts contain the phrase 'bypassing access controls
    to view the records of other customers' and the conditional phrase 'If no other
    controls are in place', which can be used to support a limited reference to the
    IDOR concept framing present in the source.
active: true
source_coverage: 'Considered 1 fetched documents; selected 1. {"requirements": [{"id":
  "method", "description": "Describe a reusable header-selector test and correctly
  explain when the comparison establishes an authorization violation. Include controls
  and evidence requirements. Target execution is not requested.", "kind": "authored_deliverable",
  "grounding": "knowledge_allowed", "origins": []}]}

  Required-source obligations ready: True; references selected: 1. Retained 1 sources.
  Latest source assessment: The PortSwigger IDOR page is an authoritative source on
  access-control IDOR concepts. The retained excerpts supply a supported technical
  fact about IDOR as bypassing access controls to view other customers'' records and
  the conditional nature of the issue when no other controls are in place. This supports
  the policy/prerequisite framing for a header-selector test. The excerpt does not
  provide header-selector test steps, controls or evidence requirements, so those
  aspects must be synthesized from model knowledge and remain as future checks.

  Knowledge-allowed content is not source-verified. Reference readiness does not establish
  document completeness or correctness.

  {"coverage": [{"requirement_id": "method", "source_urls": [], "explanation": "Model
  knowledge and reasoning are allowed; this requirement is not source-verified.",
  "authority_sufficient": null, "claim_support": [], "authoring_obligations": [],
  "synthesis": "", "gaps": [], "future_validation": []}]}'
revision_id: c2906047-0a60-5037-b045-2e0aa268a84c
lineage: generated:3fae433e-3b64-4ee4-9af1-aed888703be1
origin: generated
source_sha256: e4432c2947d61800a5e4014c1dd0fdf3b306832a01b861819dd8f5ccb16fe7fb
inactive_reason: ''
sentiment_count: 0
sentiment_mean: 0
feedback_reasons: []
---

# Reusable Header-Selector Authorization Test

## Purpose
Determine whether an application uses a client-supplied HTTP header to select an object or principal, and decide when a baseline vs variant comparison demonstrates an authorization violation rather than legitimate behavior.

## Prerequisites
1. Establish the access-control policy for the endpoint.
   - Documented policy, configuration, or independently known authorized and denied baselines for the resource.
   - Identify the principal under test and the object identifier that should be enforced server-side.
2. Establish a legitimate baseline.
   - Authenticate as a test principal with known entitlements.
   - Issue a request that is expected to succeed under the policy and record the full response.
   - Record request metadata: method, path, query, body, cookies, and the header under test with its legitimate value.
3. Confirm the header is processed server-side.
   - Change the header value to a clearly invalid token and observe a deterministic change, e.g., error, empty result, or different object.
   - If the response is unchanged, the header is not a selector for this endpoint.

## Controls
1. Policy control.
   - Maintain a written or configured mapping of which principals may access which objects.
   - Use only principals and objects where the expected allow/deny decision is known before testing.
2. Baseline control.
   - Keep the original request unchanged except for the single header value under test.
   - Preserve cookies, session tokens, and other authentication material.
3. Negative control.
   - Use a header value that is known to be invalid or out of range for the test principal.
   - Expected outcome: rejection, error, or empty result consistent with policy.
4. Entitlement control.
   - Use a second test principal with a different entitlement set.
   - Verify that the baseline for principal A differs from the baseline for principal B when policy requires it.

## Test Procedure
1. Select candidate header.
   - Common candidates: X-User-Id, X-Account-Id, X-Requester, X-Tenant-Id, X-Forwarded-User.
   - Confirm via code review, documentation, or request-response observation that the header is read server-side.
2. Capture baseline response.
   - Send request as principal A with legitimate header value H_legit.
   - Record response body hash, status code, object identifier in response, and relevant headers.
3. Variant request.
   - Send identical request as principal A but with header value H_variant belonging to principal B.
   - Do not change authentication cookies or tokens.
4. Compare responses.
   - Compare status code, response body hash, and object identifier.
   - Record timing and error messages.

## Interpretation
An authorization violation is established only when all of the following hold:
1. Policy prerequisite is established.
   - Principal A is not authorized for the object referenced by H_variant per the documented policy.
2. Baseline is legitimate.
   - Baseline request with H_legit returns the object expected for principal A.
3. Variant shows unauthorized disclosure.
   - Variant request is made with the same authentication as baseline, i.e., principal A remains authenticated.
   - The variant response returns a different object identifier and data attributable to principal B, matching H_variant, demonstrating selection based on the header rather than the authenticated principal.
   - Status code and response shape remain consistent with a successful selection, e.g., 200 OK with the same template as baseline, which confirms the server processed the header change rather than rejecting it.
   - The response contains explicit evidence attributable to the unauthorized principal, e.g., user name, account number, or record identifier matching H_variant.
4. Controls hold.
   - Negative control produces rejection or empty result.
   - Entitlement control shows principal B baseline differs from principal A baseline.
If any condition is missing, the observation is inconclusive and requires further validation.

## Evidence Requirements
- Request and response pairs for baseline, variant, negative control, and entitlement control.
- Full HTTP request headers, with the header under test highlighted, and full response body or a stable hash.
- Authentication context used: principal identifier, session token type, and timestamp.
- Policy reference showing principal A is not authorized for object of principal B.
- Screenshots or logs showing object identifiers in responses and the change from baseline object to variant object.

## Limitations and Future Checks
- Header processing may be conditional on other headers, cookies, or backend routing. Test with the exact request chain used in production.
- Some applications log header changes but still enforce server-side checks. Verify enforcement by checking data returned, not just error messages.
- Time-bound tokens or freshness checks may cause intermittent differences. Repeat tests within the valid window.
- The PortSwigger IDOR concept notes bypassing access controls to view records of other customers and the conditional nature when no other controls are in place. This test isolates the header-selector case and requires explicit policy establishment before labeling a violation.

## Optional Tool Choices
- HTTP client capable of replaying requests with header modification.
- Proxy for capturing full request/response pairs.
- Script for hashing response bodies for comparison.

Example comparison in Python 3.13+:
```python
import hashlib
def hash_body(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()
baseline_hash = hash_body(baseline_body)
variant_hash = hash_body(variant_body)
content_changed = baseline_hash != variant_hash
```
The script illustrates comparison; it does not prove a violation without the policy and controls above.
