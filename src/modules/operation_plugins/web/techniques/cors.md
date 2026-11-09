---
schema_version: 1
title: CORS
purpose: Test whether browser-enforced cross-origin rules expose a protected response to an origin excluded
  by policy.
applicability:
- A recorded response advertises CORS behavior or a browser client reads an API across origins.
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
  when: A recorded response advertises CORS behavior or a browser client reads an API across origins.
    The assigned work needs method-specific prerequisite checks.
  request: CORS reconnaissance
  signals_any:
  - http.cors
- phases:
  - hypothesis_generation
  when: Recorded observations concern cors and the assigned work is designing future discriminating tests.
  request: CORS hypothesis generation
  signals_any:
  - http.cors
- phases:
  - vulnerability_verification
  when: An explicit cors hypothesis and its prerequisites are available for controlled verification.
  request: CORS vulnerability verification
  signals_any:
  - http.cors
- phases:
  - execution
  when: The relevant cors boundary has been verified and an authorized minimal demonstration with test
    data is required.
  request: CORS execution
  signals_any:
  - http.cors
- phases:
  - analysis
  when: Recorded cors observations or comparison outcomes require interpretation of policy, controls and
    remaining uncertainty.
  request: CORS analysis
  signals_any:
  - http.cors
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/HTML5_Security_Cheat_Sheet.md
  title: HTML5 Security Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.387366+00:00'
  selection_reason: cross-origin data access and origin validation.
source_coverage: 'Selected retrieved references supply mechanism/control background: cross-origin data
  access and origin validation. Limitations: Development-oriented guidance supplies mechanism/control
  background; application policy, live exploitability, negative controls and minimal-impact procedure
  remain original methodology requiring target-specific verification. Original controlled procedures and
  interpretation are knowledge-allowed synthesis, not individually source-verified. No generic reference
  establishes target applicability or exploitation.'
---

# CORS

## Applicability and prerequisites

A recorded response advertises CORS behavior or a browser client reads an API across origins.

Use an authorized test account, a controlled origin distinct by scheme/host/port, a synthetic
confidential response and an established origin policy. Identify credential type and browser
SameSite/third-party-cookie behavior; raw header replay cannot impersonate browser enforcement.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

The intended origin can read the agreed response in a real browser. Confirm the account can access
the synthetic marker directly; test an untrusted origin with the same request semantics.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use an authorized test account, a controlled origin distinct by scheme/host/port, a synthetic
   confidential response and an established origin policy. Identify credential type and browser
   SameSite/third-party-cookie behavior; raw header replay cannot impersonate browser enforcement.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. Inspect recorded allowed-origin and credentials headers, Vary behavior and preflight responses.
   Plan future browser reads from permitted, prohibited and boundary-lookalike origins; do not
   equate wildcard headers with credentialed disclosure.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. From the controlled prohibited origin, issue the agreed browser fetch using the credential mode
   required by the application. Preserve browser console/network evidence of which credentials were
   actually sent.
2. Inspect whether script at that origin can read the protected marker. A successful network request
   or a reflected Origin header alone is not a readable response.
3. Compare the permitted origin and a second untrusted origin. For non-simple requests, test the
   actual preflight and subsequent request together, not OPTIONS headers in isolation.
4. Where relevant, vary one origin boundary such as subdomain, scheme or port while keeping session
   and resource fixed. Verify caches do not substitute another origin's response and distinguish
   public unauthenticated data from protected data.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Read only the synthetic confidential marker into the controlled origin's page; no external
   exfiltration service is needed. Remove the test page and revoke the disposable session.
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

Public endpoints may intentionally permit all origins. Browsers reject wildcard-origin responses
combined with credentialed access; denied cookies or a preflight failure can prevent the proposed
impact despite permissive-looking headers.

## Evidence requirements

Retain the excluded-origin policy, real origin and credential mode, preflight where applicable,
browser-readable marker and allowed/denied origin controls.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

Browser version, cookie policy and network context matter. CORS controls reading responses; they do
not alone prevent CSRF or authorize target-side effects.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
