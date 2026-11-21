---
schema_version: 1
title: Account enumeration
purpose: Test whether externally observable behavior reveals account existence to an actor prohibited
  from learning it.
applicability:
- A mapped login, registration or recovery interaction accepts an account identifier.
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
  when: A mapped login, registration or recovery interaction accepts an account identifier. The assigned
    work needs method-specific prerequisite checks.
  request: Account enumeration reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern account enumeration and the assigned work is designing future discriminating
    tests.
  request: Account enumeration hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit account enumeration hypothesis and its prerequisites are available for controlled
    verification.
  request: Account enumeration vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant account enumeration boundary has been verified and an authorized minimal demonstration
    with test data is required.
  request: Account enumeration execution
  signals_any: []
- phases:
  - analysis
  when: Recorded account enumeration observations or comparison outcomes require interpretation of policy,
    controls and remaining uncertainty.
  request: Account enumeration analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Authentication_Cheat_Sheet.md
  title: Authentication Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.626925+00:00'
  selection_reason: generic errors and response discrepancy.
source_coverage: 'Selected retrieved references supply mechanism/control background: generic errors and
  response discrepancy. Limitations: Development-oriented guidance supplies mechanism/control background;
  application policy, live exploitability, negative controls and minimal-impact procedure remain original
  methodology requiring target-specific verification. Original controlled procedures and interpretation
  are knowledge-allowed synthesis, not individually source-verified. No generic reference establishes
  target applicability or exploitation.'
---

# Account enumeration

## Applicability and prerequisites

A mapped login, registration or recovery interaction accepts an account identifier.

Use a small agreed set of known existing and confirmed nonexistent synthetic identifiers. Establish
whether existence is confidential; public usernames and directory search may intentionally disclose
it. Set a request cap that avoids lockouts or notification abuse.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

Submit the same invalid authenticator or equivalent benign request for both identifier classes under
matching account states and client context.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use a small agreed set of known existing and confirmed nonexistent synthetic identifiers.
   Establish whether existence is confidential; public usernames and directory search may
   intentionally disclose it. Set a request cap that avoids lockouts or notification abuse.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. Compare recorded messages, status, redirect location, headers and timing for possible existence
   discriminators. Design future interleaved trials and held-out synthetic identifiers rather than
   extrapolating from one slow response.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Capture paired existing/nonexistent responses while keeping body shape, invalid password, locale
   and cookies equivalent.
2. Interleave a bounded number of repeats in both orders, tracking rate-limit and challenge state.
   Classify stable content/status differences separately from timing differences.
3. For timing candidates, compare distributions and overlap with repeated same-identifier controls;
   use a small held-out pair to check whether the discriminator predicts existence rather than
   request order.
4. Repeat after normal service behavior is restored. Stop when lockout, email/SMS delivery,
   throttling or service load changes the comparison; preserve the result as inconclusive where
   noise prevents discrimination.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Demonstrate the discriminator on one held-out synthetic pair whose truth is known independently.
   Do not enumerate a user population; stop notifications and remove disposable test accounts if
   agreed.
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

Account state, password hashing work, cache warmth, abuse controls, localization and network noise
can change responses without exposing existence reliably. Public identifier availability can be an
intentional policy.

## Evidence requirements

Retain confidentiality policy, ground-truth identifier classes without real user data, matched
request pairs, order/timing samples, the proposed decision rule and held-out result.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

A bounded timing trial cannot establish population-wide accuracy. Lack of a visible difference does
not rule out side channels in other flows or account states.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
