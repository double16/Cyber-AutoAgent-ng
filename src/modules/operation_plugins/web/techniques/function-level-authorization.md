---
schema_version: 1
title: Function-level authorization
purpose: Compare role entitlements for a specific server-side function independently of the user interface.
applicability:
- A mapped function has a documented or otherwise established restricted role, such as a management action.
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
  when: A mapped function has a documented or otherwise established restricted role, such as a management
    action. The assigned work needs method-specific prerequisite checks.
  request: Function-level authorization reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern function-level authorization and the assigned work is designing
    future discriminating tests.
  request: Function-level authorization hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit function-level authorization hypothesis and its prerequisites are available for controlled
    verification.
  request: Function-level authorization vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant function-level authorization boundary has been verified and an authorized minimal
    demonstration with test data is required.
  request: Function-level authorization execution
  signals_any: []
- phases:
  - analysis
  when: Recorded function-level authorization observations or comparison outcomes require interpretation
    of policy, controls and remaining uncertainty.
  request: Function-level authorization analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Authorization_Cheat_Sheet.md
  title: Authorization Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.539161+00:00'
  selection_reason: function entitlement and deny-by-default policy.
source_coverage: 'Selected retrieved references supply mechanism/control background: function entitlement
  and deny-by-default policy. Limitations: Development-oriented guidance supplies mechanism/control background;
  application policy, live exploitability, negative controls and minimal-impact procedure remain original
  methodology requiring target-specific verification. Original controlled procedures and interpretation
  are knowledge-allowed synthesis, not individually source-verified. No generic reference establishes
  target applicability or exploitation.'
---

# Function-level authorization

## Applicability and prerequisites

A mapped function has a documented or otherwise established restricted role, such as a management
action.

Identify an authorized privileged test actor P, a lower-privileged test actor L, the exact function
and any required object entitlement. Use a reversible test action and confirm the policy excludes L;
hidden UI controls are not that policy.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

P successfully performs the function on disposable test data. L successfully performs an ordinary
function so an expired or broken L session is not mistaken for enforcement.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Identify an authorized privileged test actor P, a lower-privileged test actor L, the exact
   function and any required object entitlement. Use a reversible test action and confirm the policy
   excludes L; hidden UI controls are not that policy.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. From recorded requests, separate role restriction from object restriction and design future P/L
   comparisons of the same function. Mark alternate verbs or API variants as independent candidates
   only where their semantics are understood.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Establish P's legitimate request and server-side result, including required workflow state and
   anti-CSRF values.
2. Replay the same function as L using only L's valid credentials and fresh L-bound request tokens.
   Keep the target object and meaningful parameters equivalent.
3. Observe whether the restricted server-side action actually completes. A 200 response, admin page
   shell or list of route names does not prove privileged execution.
4. Compare L's allowed function and a denied or inert function invocation. Test one observed
   alternate verb or API variant at a time, and avoid conflating a method mismatch with a role
   check.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Use L to perform one reversible restricted change on disposable test data only after the role
   boundary is established. Revert with P and verify the final state.
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

An actor can possess an inherited, delegated or temporary grant not visible in its displayed role
name. A queued action may later fail authorization, and an admin UI can be publicly served while
APIs remain protected.

## Evidence requirements

Retain the role entitlement basis, P and L context isolation, exact function semantics, comparable
requests, durable action outcome and reversal.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

External comparisons cannot reconstruct every server-side role rule. A failed alternate method tests
only that route and verb, not all implementations of the function.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
