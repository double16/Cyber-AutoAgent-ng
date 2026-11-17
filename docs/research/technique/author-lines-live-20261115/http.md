# Reusable Header-Selector Authorization Test

## Purpose
Determine if an application selects an object based on a client-supplied HTTP header and whether the selection is enforced by server-side authorization. The test establishes a baseline, performs a controlled variation, and compares outcomes under explicit policy and controls.

## Prerequisites
- Identify an endpoint that returns data scoped to a principal, e.g., /api/account or /profile.
- Identify a candidate header that could influence selection, e.g., X-User-Id, X-Account-Id, X-Request-Id, X-Forwarded-User.
- Establish the authorization policy for the endpoint: which principals are allowed to access which objects. Policy is established via documentation, configuration, or independently known authorized/denied baselines, not assumed.
- Have two test principals A and B with known, distinct entitlements. Credentials must be valid and within their allowed window.
- Ensure the test environment permits controlled request replay without affecting production data.

## Controls
1. Baseline authorized request
   - Send request as Principal A with the original header value or with no header override.
   - Record status code, response body hash, object identifier returned, and timestamp.
   - This establishes legitimate behavior for A.

2. Same-principal variation control
   - Repeat the request as Principal A but with a trivially different header value that should not change authorization, e.g., change a non-functional header.
   - Expect identical authorization outcome. Confirms the test harness is stable.

3. Cross-principal negative control
   - Send request as Principal A with header value that references Principal B's object, but using A's credentials.
   - This is the test variation. Do not assume violation yet.

4. Positive control for enforcement
   - Send request as Principal B with header value referencing B's object using B's credentials.
   - Expect success. Confirms the object exists and is accessible to its owner.

## Procedure
1. Baseline
   - Record request R0: credentials for A, original header H0, endpoint E.
   - Record response R0_resp: status, body excerpt, object id.

2. Header influence check
   - Send R1: credentials for A, header H1 = value associated with B, endpoint E.
   - Record response R1_resp.

3. Compare
   - If R1_resp returns data for B's object with status indicating success, compare to policy.

## Interpretation - When a Violation Is Established
An authorization violation is established only if all of the following hold:

- Policy established: The authorization policy states Principal A must NOT access B's object. Evidence of policy is documented or from an independent denied baseline where A accessing B's object is rejected when header is not present.
- Baseline confirms: R0_resp shows A can access A's own object.
- Positive control confirms: B can access B's object with B's credentials.
- Test variation shows: A with B's header value receives B's object data with success status, and the response is not a generic error or redirect.
- Controls exclude alternatives: Same-principal variation control shows no change due to test harness noise; header is not being stripped or normalized by a proxy.

If any prerequisite is missing, the outcome is a future check, not a violation.

## Evidence Requirements
- Full request and response pairs for R0, R1, positive control, and same-principal variation, including headers, status code, response body hash or relevant excerpt, and timestamp.
- Record of how authorization policy was established: documentation reference, configuration excerpt, or baseline denied test.
- Evidence that credentials were valid and within their freshness window at test time.
- Note of any intermediate controls, e.g., WAF, proxy, that could alter headers.

## Limitations
- Header selection alone does not prove violation without established entitlement restriction.
- Valid reusable credentials can legitimately succeed repeatedly within their window; require revocation/expiry event to claim misuse.
- The test does not prove impact; further checks needed for confidentiality or integrity impact.

## Optional Tool Choices
- HTTP client supporting header replay and logging, e.g., curl, httpie, or browser dev tools.
- Scripting for repeatable comparison, e.g., Python 3.13+ with http.client.

```python
# Illustrative comparison skeleton, not executed
def compare(responses):
    # responses is a dict with keys 'baseline','test','positive','control'
    # Return True only if policy, baseline, positive control, and test variation all align
    pass
```
