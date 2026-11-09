---
schema_version: 1
title: Session lifecycle, logout, and revocation
purpose: Compare session and token validity across logout, expiry, credential events and explicit revocation.
applicability:
- A mapped authenticated session or access/refresh token has a lifecycle transition under review.
- The relevant operation, identities, data and side effects are within the resolved assessment scope.
phases:
- reconnaissance
- hypothesis_generation
- vulnerability_verification
- execution
- analysis
active: true
triggers:
- phases:
  - reconnaissance
  when: A mapped authenticated session or access/refresh token has a lifecycle transition under review.
    The assigned work needs method-specific prerequisite checks.
  request: Session lifecycle, logout, and revocation reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern session lifecycle, logout, and revocation and the assigned work
    is designing future discriminating tests.
  request: Session lifecycle, logout, and revocation hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit session lifecycle, logout, and revocation hypothesis and its prerequisites are available
    for controlled verification.
  request: Session lifecycle, logout, and revocation vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant session lifecycle, logout, and revocation boundary has been verified and an authorized
    minimal demonstration with test data is required.
  request: Session lifecycle, logout, and revocation execution
  signals_any: []
- phases:
  - analysis
  when: Recorded session lifecycle, logout, and revocation observations or comparison outcomes require
    interpretation of policy, controls and remaining uncertainty.
  request: Session lifecycle, logout, and revocation analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Session_Management_Cheat_Sheet.md
  title: Session Management Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.805862+00:00'
  selection_reason: expiration, server invalidation and token lifecycle.
source_coverage: 'Selected retrieved references supply mechanism/control background: expiration, server
  invalidation and token lifecycle. Limitations: Development-oriented guidance supplies mechanism/control
  background; application policy, live exploitability, negative controls and minimal-impact procedure
  remain original methodology requiring target-specific verification. Original controlled procedures and
  interpretation are knowledge-allowed synthesis, not individually source-verified. No generic reference
  establishes target applicability or exploitation.'
---

# Session lifecycle, logout, and revocation

## Applicability and prerequisites

A mapped authenticated session or access/refresh token has a lifecycle transition under review.

Use disposable sessions, a private identity-returning resource and established
logout/expiry/revocation policy. Identify browser cookie, access token, refresh token and federated
session separately; their validity windows need not be identical.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

The active session reads the private synthetic marker; an invalid token does not. Record issuance,
expected expiry and the exact scope of the revocation event.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use disposable sessions, a private identity-returning resource and established
   logout/expiry/revocation policy. Identify browser cookie, access token, refresh token and
   federated session separately; their validity windows need not be identical.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. From recorded lifecycle events design future old-session replay, refresh and new-login
   comparisons. Distinguish browser cookie removal from server-side invalidation and explicit
   access-token grace periods from unspecified behavior.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Save a test session in an isolated client and verify the protected resource with it before the
   event.
2. Perform one normal logout, account-level revocation or other assigned event through the
   legitimate account flow. Preserve the old credential only in the controlled test client.
3. Replay the old session directly against the protected resource and, separately, test old
   refresh-token use where relevant. Do not allow a new login, SSO redirect or rotated credential to
   silently reauthenticate the replay.
4. Compare an invalid token and a newly authenticated session. For expiry, wait until the documented
   boundary plus permitted skew and compare idle versus absolute expiry without unintentionally
   refreshing activity.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Demonstrate one policy-prohibited old-session or refresh reuse to read a synthetic marker. Revoke
   all disposable sessions and verify final invalidation; avoid long-lived tokens that cannot be
   safely revoked.
3. Verify restoration through the relevant authoritative channel. If cleanup cannot be confirmed,
   stop further changes and preserve that limitation.

## Analysis

1. Compare the observed result with the established policy and the local controls above. A
   successful request, available artifact, changed response or tool execution establishes
   availability only; confirm the precise protected data or effect needed for the claim.
2. Classify the tested mechanism as supported, refuted for the tested case, or inconclusive. Keep
   the tested actor, object, state and channel explicit, and preserve competing explanations and
   untested variants.

### False positives and competing explanations

A cached page can appear logged in without a server request. Stateless access tokens can remain
valid for an explicitly accepted short lifetime after logout while refresh is revoked; evaluate that
policy rather than assuming universal immediate revocation.

## Evidence requirements

Retain credential type and age, lifecycle policy and event scope, old-versus-new session separation,
actual network protected-resource results and refresh outcomes.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

One session family or device does not cover all token audiences or federation layers. Unknown
propagation delay or expiry policy limits the conclusion.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
