---
schema_version: 1
title: Password recovery
purpose: Test reset-token ownership, lifecycle and password-reset effects without involving other users'
  accounts.
applicability:
- A mapped password recovery process issues and consumes a reset credential.
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
  when: A mapped password recovery process issues and consumes a reset credential. The assigned work needs
    method-specific prerequisite checks.
  request: Password recovery reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern password recovery and the assigned work is designing future discriminating
    tests.
  request: Password recovery hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit password recovery hypothesis and its prerequisites are available for controlled verification.
  request: Password recovery vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant password recovery boundary has been verified and an authorized minimal demonstration
    with test data is required.
  request: Password recovery execution
  signals_any: []
- phases:
  - analysis
  when: Recorded password recovery observations or comparison outcomes require interpretation of policy,
    controls and remaining uncertainty.
  request: Password recovery analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Forgot_Password_Cheat_Sheet.md
  title: Forgot Password Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.744728+00:00'
  selection_reason: reset token binding, expiry and uniform responses.
source_coverage: 'Selected retrieved references supply mechanism/control background: reset token binding,
  expiry and uniform responses. Limitations: Development-oriented guidance supplies mechanism/control
  background; application policy, live exploitability, negative controls and minimal-impact procedure
  remain original methodology requiring target-specific verification. Original controlled procedures and
  interpretation are knowledge-allowed synthesis, not individually source-verified. No generic reference
  establishes target applicability or exploitation.'
---

# Password recovery

## Applicability and prerequisites

A mapped password recovery process issues and consumes a reset credential.

Use two disposable accounts and controlled delivery channels. Establish intended token lifetime,
one-time-use and account binding, plus reset/session revocation policy. Keep a legitimate recovery
path available before changing credentials.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

A newly issued token resets its own test account and the resulting password authenticates that
account; a random invalid token cannot reset it.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use two disposable accounts and controlled delivery channels. Establish intended token lifetime,
   one-time-use and account binding, plus reset/session revocation policy. Keep a legitimate
   recovery path available before changing credentials.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. From recorded flows identify reset token, account selector, delivery address and
   issuance/consumption steps. Design future cross-account, token-reuse and expiry comparisons;
   treat unknown lifetimes as unknown rather than choosing an arbitrary expiry threshold.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Issue a reset for A through the normal flow and preserve the account/channel association. Confirm
   any generated absolute links point to the intended origin without following untrusted
   destinations.
2. Keep A's valid token fixed and vary only the account selector to B, using a reversible test
   password. Check B's actual authentication outcome, not a success message alone.
3. Compare consumed-token reuse, a random token, and a token after its established expiry.
   Requesting a newer token need not invalidate an older one unless that lifecycle is required; test
   it against the actual policy.
4. After a valid reset, check old password and existing sessions against the stated invalidation
   policy. Avoid conflating a public recovery response with a completed reset or leakage of account
   existence.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Demonstrate one prohibited reset of a controlled account or one prohibited token reuse. Restore
   secure test credentials, revoke issued reset tokens and sessions where supported, and retain
   account access.
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

Delayed delivery, URL scanners consuming links, clock skew and already-authenticated sessions can
confuse lifecycle results. Multiple outstanding valid tokens can be a deliberate policy requiring
contextual assessment.

## Evidence requirements

Retain account/token association, issuance and consumption times, lifecycle policy, invalid and
cross-account controls, authentication read-back and restoration.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

Entropy cannot be established by inspecting a few tokens. Do not perform token brute force or flood
delivery channels as part of these bounded comparisons.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
