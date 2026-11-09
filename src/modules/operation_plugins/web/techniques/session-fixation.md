---
schema_version: 1
title: Session fixation
purpose: Test whether a previously known session identifier remains usable by another client after authentication
  or privilege elevation.
applicability:
- A mapped flow carries a session identifier across login or another privilege transition.
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
  when: A mapped flow carries a session identifier across login or another privilege transition. The assigned
    work needs method-specific prerequisite checks.
  request: Session fixation reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern session fixation and the assigned work is designing future discriminating
    tests.
  request: Session fixation hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit session fixation hypothesis and its prerequisites are available for controlled verification.
  request: Session fixation vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant session fixation boundary has been verified and an authorized minimal demonstration
    with test data is required.
  request: Session fixation execution
  signals_any: []
- phases:
  - analysis
  when: Recorded session fixation observations or comparison outcomes require interpretation of policy,
    controls and remaining uncertainty.
  request: Session fixation analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Session_Management_Cheat_Sheet.md
  title: Session Management Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.805862+00:00'
  selection_reason: session rotation across privilege changes.
source_coverage: 'Selected retrieved references supply mechanism/control background: session rotation
  across privilege changes. Limitations: Development-oriented guidance supplies mechanism/control background;
  application policy, live exploitability, negative controls and minimal-impact procedure remain original
  methodology requiring target-specific verification. Original controlled procedures and interpretation
  are knowledge-allowed synthesis, not individually source-verified. No generic reference establishes
  target applicability or exploitation.'
---

# Session fixation

## Applicability and prerequisites

A mapped flow carries a session identifier across login or another privilege transition.

Use two isolated clients and one disposable account. Establish that an attacker could know or supply
the pre-authentication identifier through the accepted exchange mechanism. A mere unchanged cookie
name is insufficient; track actual values and authority.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

Client V logs in with its ordinary pre-authentication state and can read a synthetic private marker.
Client A's unrelated anonymous identifier cannot read that marker.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use two isolated clients and one disposable account. Establish that an attacker could know or
   supply the pre-authentication identifier through the accepted exchange mechanism. A mere
   unchanged cookie name is insufficient; track actual values and authority.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. Inspect recorded identifiers before and after the transition, including paired cookies and
   URL/header alternatives. Design a future A-known identifier test and subsequent independent A
   replay; unchanged visible state may coexist with server-side rotation or additional binding.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Obtain a normal pre-authentication session identifier known to A. Determine whether V can accept
   that identifier through a realistic in-scope mechanism rather than assuming arbitrary cookie
   injection.
2. Authenticate V using the agreed test account while it carries the A-known pre-authentication
   state. Record all new identifiers and any invalidation response.
3. From A's independent client, replay only the identifier/state A could have known before
   authentication. Attempt the same protected marker read without copying newly issued credentials
   from V.
4. Compare a fresh random identifier, V's legitimate new session and A's old identifier after
   logout. Verify the resulting identity and authorization; persistence of the original identifier
   alone does not prove hijacking.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Read the synthetic private marker once from A using only preknown state. Log out V and A, revoke
   the test sessions and verify the preknown state no longer grants access.
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

An anonymous preference cookie may remain unchanged while an authentication cookie rotates. Device
binding, an additional secret or server-side identity state can prevent the second client from
inheriting authentication.

## Evidence requirements

Retain the realistic fixation mechanism, preknown versus newly issued state distinction, isolated
clients, authentication transition and A's protected-resource result.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

A laboratory cookie substitution demonstrates only conditional susceptibility unless a feasible way
to set or share that identifier is established. Do not conflate identifier reuse with authorization
transfer.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
