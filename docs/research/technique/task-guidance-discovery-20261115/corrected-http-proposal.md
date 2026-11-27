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
   - An unchanged response to an invalid value is inconclusive: invalid values may fall back to the authenticated principal while valid values select another object. Test known valid selector values under unchanged authentication before deciding whether the header selects an object.

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
   - Record status and response shape, but do not require them to match baseline or treat them as proof of authorization. Determine whether protected object data was returned, even inside an error response or a different response structure.
   - The response contains explicit evidence attributable to the unauthorized principal, e.g., user name, account number, or record identifier matching H_variant.
4. Controls hold.
   - The invalid-value control is recorded and interpreted according to established behavior; fallback to the authenticated principal does not establish absence of selector behavior. A valid but unauthorized object value is the policy-denied control.
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
