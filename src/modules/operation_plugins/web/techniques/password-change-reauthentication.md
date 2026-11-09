---
schema_version: 1
title: Password change and reauthentication
purpose: Test whether password and other sensitive credential changes enforce the required fresh-authentication
  boundary.
applicability:
- A mapped authenticated flow changes a password or another credential under a stated reauthentication
  policy.
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
  when: A mapped authenticated flow changes a password or another credential under a stated reauthentication
    policy. The assigned work needs method-specific prerequisite checks.
  request: Password change and reauthentication reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern password change and reauthentication and the assigned work is designing
    future discriminating tests.
  request: Password change and reauthentication hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit password change and reauthentication hypothesis and its prerequisites are available
    for controlled verification.
  request: Password change and reauthentication vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant password change and reauthentication boundary has been verified and an authorized
    minimal demonstration with test data is required.
  request: Password change and reauthentication execution
  signals_any: []
- phases:
  - analysis
  when: Recorded password change and reauthentication observations or comparison outcomes require interpretation
    of policy, controls and remaining uncertainty.
  request: Password change and reauthentication analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Authentication_Cheat_Sheet.md
  title: Authentication Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.626925+00:00'
  selection_reason: reauthentication for sensitive changes.
source_coverage: 'Selected retrieved references supply mechanism/control background: reauthentication
  for sensitive changes. Limitations: Development-oriented guidance supplies mechanism/control background;
  application policy, live exploitability, negative controls and minimal-impact procedure remain original
  methodology requiring target-specific verification. Original controlled procedures and interpretation
  are knowledge-allowed synthesis, not individually source-verified. No generic reference establishes
  target applicability or exploitation.'
---

# Password change and reauthentication

## Applicability and prerequisites

A mapped authenticated flow changes a password or another credential under a stated reauthentication
policy.

Use a disposable account with recovery access, a valid session and known current password. Establish
which changes require current password, MFA or recent authentication, and the exact scope/lifetime
of any step-up grant.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

A correctly reauthenticated request changes the test password, and the resulting password logs into
the same account in a separate client context.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use a disposable account with recovery access, a valid session and known current password.
   Establish which changes require current password, MFA or recent authentication, and the exact
   scope/lifetime of any step-up grant.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. Use recorded transitions to identify password inputs, step-up tokens and freshness state. Design
   future omission, wrong-current-password, stale-grant and cross-action/cross-account grant
   comparisons; an authenticated session alone may be sufficient for actions outside the stated
   policy.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Start with a normal authenticated session outside any known freshness window and verify its actor
   identity.
2. Attempt one protected change with the reauthentication proof omitted or invalid while preserving
   other valid parameters. Observe the actual credential state through a separate login.
3. If step-up uses a grant, compare its normal consumption with use after expiry, for a different
   sensitive action and for a second controlled account, one scope dimension at a time.
4. Repeat a correctly reauthenticated change and check the old password and sessions against the
   declared lifecycle policy. Distinguish failures from password-strength rules, CSRF and account
   lockout.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Change only the disposable account's credential, restore a strong known test credential and
   verify access. Revoke test sessions/grants and stop before a change that would remove recovery
   access.
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

A trusted device, recent upstream SSO authentication or fresh step-up grant can legitimately satisfy
policy without a visible password prompt. A rejected weak new password is not proof of
reauthentication enforcement.

## Evidence requirements

Retain the sensitive-action policy, session and grant age/scope, omitted/invalid proof controls,
separate-client authentication results and restoration.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

Freshness can depend on risk signals unavailable to external testing. Do not claim that every
password change universally requires the same factor or timeout.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
