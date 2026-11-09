---
schema_version: 1
title: Rate limiting and sensitive-flow abuse
purpose: Test the stated abuse-control boundary for a sensitive flow with a strictly bounded request sequence.
applicability:
- A mapped login, recovery, signup, messaging or other sensitive action has an abuse or rate policy to
  assess.
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
  when: A mapped login, recovery, signup, messaging or other sensitive action has an abuse or rate policy
    to assess. The assigned work needs method-specific prerequisite checks.
  request: Rate limiting and sensitive-flow abuse reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern rate limiting and sensitive-flow abuse and the assigned work is
    designing future discriminating tests.
  request: Rate limiting and sensitive-flow abuse hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit rate limiting and sensitive-flow abuse hypothesis and its prerequisites are available
    for controlled verification.
  request: Rate limiting and sensitive-flow abuse vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant rate limiting and sensitive-flow abuse boundary has been verified and an authorized
    minimal demonstration with test data is required.
  request: Rate limiting and sensitive-flow abuse execution
  signals_any: []
- phases:
  - analysis
  when: Recorded rate limiting and sensitive-flow abuse observations or comparison outcomes require interpretation
    of policy, controls and remaining uncertainty.
  request: Rate limiting and sensitive-flow abuse analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Denial_of_Service_Cheat_Sheet.md
  title: Denial of Service Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.904026+00:00'
  selection_reason: rate limits and resource protection.
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Authentication_Cheat_Sheet.md
  title: Authentication Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.626925+00:00'
  selection_reason: rate limits and resource protection.
- url: https://raw.githubusercontent.com/OWASP/wstg/v4.2/document/4-Web_Application_Security_Testing/10-Business_Logic_Testing/05-Test_Number_of_Times_a_Function_Can_Be_Used_Limits.md
  title: wstg-function-limits
  retrieved_at: '2026-11-07T10:33:40.380624+00:00'
  selection_reason: WSTG-BUSL-05 describes per-business-policy function use limits and repeated-use misuse
    cases.
source_coverage: 'Selected retrieved references supply mechanism/control background: rate limits and resource
  protection. rate limits and resource protection. WSTG-BUSL-05 describes per-business-policy function
  use limits and repeated-use misuse cases. Limitations: Development-oriented guidance supplies mechanism/control
  background; application policy, live exploitability, negative controls and minimal-impact procedure
  remain original methodology requiring target-specific verification. Original controlled procedures and
  interpretation are knowledge-allowed synthesis, not individually source-verified. No generic reference
  establishes target applicability or exploitation.'
---

# Rate limiting and sensitive-flow abuse

## Applicability and prerequisites

A mapped login, recovery, signup, messaging or other sensitive action has an abuse or rate policy to
assess.

Establish the expected limit key, threshold/window, excluded actors, allowable side effects and a
fixed probe cap. Use test accounts and controlled notification destinations; if the limit cannot be
reached safely within the cap, retain that as a coverage gap.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

One normal request completes the intended action. Record actor, network context, cookies, action
count, timestamps and any advertised quota/reset metadata.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Establish the expected limit key, threshold/window, excluded actors, allowable side effects and a
   fixed probe cap. Use test accounts and controlled notification destinations; if the limit cannot
   be reached safely within the cap, retain that as a coverage gap.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. From recorded behavior identify whether the candidate control is per account, session, recipient,
   tenant, address or global. Design future within-limit, at-limit and recovery comparisons without
   assuming a missing 429 means no effective abuse control.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Run the permitted bounded sequence using one fixed test identity and flow. Count completed
   effects as well as HTTP responses; challenges or delayed processing may enforce the boundary
   without 429.
2. Only when explicitly authorized and still within the total cap, change one suspected limit-key
   dimension, such as a second test session for the same account. Do not rotate infrastructure or
   identities in bulk.
3. Compare the actual number of protected effects against the established policy and inspect
   challenge, delay, denial and downstream delivery behavior.
4. After the stated reset interval, repeat one normal request and compare an unrelated permitted
   control. Stop on lockouts, unexpected delivery, cost or service degradation; no threshold beyond
   the agreed cap is inferred.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Show only the minimum extra synthetic effect beyond the established boundary. Cancel queued test
   actions, release test-account lockouts through the normal process and stop notification
   generation.
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

A limiter may use rolling windows, risk challenges or downstream quotas rather than fixed status
codes. Alternate identity contexts may legitimately have independent quotas.

## Evidence requirements

Retain the policy and limit key, exact capped request/effect counts and times, enforcement
observations, reset and key-dimension controls and side-effect cleanup.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

No visible throttling below an unknown threshold is inconclusive. These tests do not measure maximum
throughput, conduct brute force or establish resistance to distributed abuse.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
