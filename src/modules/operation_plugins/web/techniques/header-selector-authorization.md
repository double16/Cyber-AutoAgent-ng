---
schema_version: 1
title: Header-selector authorization comparison
purpose: Compare client-controlled object-selector headers against established access
  policy using stable identities and controlled evidence.
applicability:
- A mapped read operation may use a client-controlled header to select an account,
  tenant or protected object.
phases:
- hypothesis_generation
- vulnerability_verification
- analysis
active: true
triggers:
- phases:
  - hypothesis_generation
  - vulnerability_verification
  - analysis
  when: A mapped read operation exposes an account, tenant or object-selector header
    and the assigned task needs authorization comparisons or interpretation.
  request: Header-selector authorization comparison with policy, identity and invalid-value
    controls
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/wstg/v4.2/document/4-Web_Application_Security_Testing/05-Authorization_Testing/04-Testing_for_Insecure_Direct_Object_References.md
  title: Testing for Insecure Direct Object References (WSTG-ATHZ-04)
  retrieved_at: '2026-11-15'
  selection_reason: OWASP maintainer guidance supports object-reference variation
    using multiple users, known object ownership and application business rules to
    distinguish unauthorized access.
- url: https://raw.githubusercontent.com/OWASP/wstg/v4.2/document/4-Web_Application_Security_Testing/05-Authorization_Testing/02-Testing_for_Bypassing_Authorization_Schema.md
  title: Testing for Bypassing Authorization Schema (WSTG-ATHZ-02)
  retrieved_at: '2026-11-15'
  selection_reason: OWASP maintainer guidance discusses horizontal/vertical authorization
    comparisons with distinct sessions, and special request headers that can influence
    URL selection.
- url: https://portswigger.net/web-security/access-control/idor
  title: "Insecure direct object references (IDOR) \u2014 Web Security Academy"
  retrieved_at: '2026-11-15'
  selection_reason: The publisher explains user-supplied object selection and a conditional
    account-record access-control failure when other controls are absent. Supports
    the draft attribution and the distinction between selection and an authorization
    failure.
source_coverage: Selected fetched OWASP and PortSwigger guidance supports object-reference
  and cross-identity authorization comparisons. Header-specific fallback controls
  and the procedure are original knowledge-allowed methodology, not individually source-verified.
  References do not establish any target policy, header behavior or completed vulnerability
  evidence.
---

# Header-selector authorization comparison

## Applicability and prerequisites

Use this method when a mapped read operation may consume a client-controlled header to choose an
account, tenant or protected object. Header presence, reflection or a successful response alone
is not evidence of unauthorized access. This method does not authorize endpoint discovery.

Establish the operation's intended access policy independently of the response being tested.
Identify test principals A and B, their permitted object sets, valid authentication contexts,
and controlled objects attributable to each principal. Account ownership does not by itself
establish exclusive access: shared, delegated or tenant-wide access may be legitimate. If policy
is unavailable, preserve that gap and treat authorization conclusions as inconclusive.

Use authorized test data and an appropriate request/replay capability. Equivalent tools may be
used. Preserve configured HTTP/HTTPS proxy use where supported. Keep the actual request target
within scope; changing an identity-like header does not authorize a different host or port.

## Hypothesis generation

Use recorded requests, responses, documentation or code observations only; do not execute new
requests in this phase. Identify the candidate selector, authenticated principal, protected
object, expected permitted and prohibited outcomes, and unresolved policy or identity conditions.
Design the comparisons below as future tests. An invalid selector that produced no change does
not refute selector behavior: the server may fall back for invalid values but use valid values.

## Controls

- Authorized baseline: A accesses an object policy permits A to read.
- Object/entitlement control: B accesses B's controlled object with B's authentication, establishing
  its identity and relevant content. Record any legitimate shared entitlement for A explicitly.
- Policy-denied comparison: keep A's authentication and change only the candidate selector to a
  valid object that established policy denies A. Do not assume every B-owned object is denied A.
- Invalid-value behavior: separately record an invalid or nonexistent selector. Rejection, fallback,
  unchanged output and changed errors describe input handling; none alone proves or disproves
  authorization enforcement or even whether all valid selector values are ignored.
- Stability: repeat or restore the authorized baseline as needed to distinguish session expiry,
  changing object state, cache behavior and nondeterministic output from selector influence.

## Vulnerability verification

1. Capture the ordinary read request and its full response. Record actual caller identity,
   endpoint, method, candidate header, authentication context and policy version. Obtain the
   authorized baseline and object/entitlement control above.
2. Keep A's authentication, endpoint, query, body and other relevant headers unchanged. Replace
   only the candidate header value with the valid policy-denied object's selector. Verify the
   transmitted value; proxies and clients may strip, normalize or override it.
3. Inspect the returned content for protected object identifiers and data attributable to the
   selected object. Compare with both principals' baselines. Status codes, response shape, body
   hashes and error labels are supporting observations, not permission decisions. Different
   response structures can contain the same protected data, and an error body can still disclose
   it. A matching hash alone does not establish ownership or authorization.
4. Record invalid-value behavior without using it as a stopping rule. An unchanged response to an
   invalid value is inconclusive about valid selectors. Continue the valid-object comparison if
   prerequisites and scope allow it; do not infer that the header is ignored.
5. Confirm an apparent policy violation by repeating the discriminating comparison with stable
   caller identity and controlled object state. Preserve competing explanations, including
   legitimate sharing, different authenticated context, public data and stale cache results.

## Interpretation and analysis

- Supported unauthorized disclosure: established policy denies A the selected object, A remains
  the authenticated caller, and the changed-header request returns protected data attributable
  to that object. Relevant controls support the attribution and exclude known competing causes.
- Legitimate behavior: returned data is permitted for the actual principal, including established
  shared access. Header-driven selection is not itself a vulnerability.
- No unauthorized disclosure observed: the tested denied-object comparison returns no protected
  data. Scope this conclusion to the tested operation, principals, objects and state; it does not
  establish security for all selector values or other endpoints.
- Inconclusive: policy, identity, object attribution, stable controls or the decisive observation
  is missing. Record what is unknown and the next discriminating check instead of inventing a
  permission rule. A 200 response or an error label alone does not resolve the conclusion.

Do not require the variant's status code or content structure to match baseline before recognizing
protected data disclosure. Conversely, equal status/shape or any response difference alone does
not establish a violation. Separate observed disclosure from untested write access, privilege
escalation and broader impact. Do not infer an ATT&CK technique from this vulnerability class.

## Evidence and limitations

Retain linked request/response pairs, actual authentication identity, selector values, policy
references, object attribution, relevant timestamps and controls. A hash is useful for integrity
and comparison but must be linked to inspectable content supporting the claim. Preserve exact
secrets only in appropriate sensitive evidence; redact them in reports and shareable exports.

Header handling can depend on proxies, routing, sessions and endpoint-specific behavior. Test
conditions and policy can change. Missing capabilities or unavailable policy are explicit gaps,
not proof of either safety or vulnerability. This reusable procedure describes future work and
interpretation, not completed target execution.

The selected references describe object-access control and comparisons across identities. The
header-specific controls, fallback counterexample and procedure here are original methodological
synthesis; the references do not establish any target's policy or prove that its header is used.
