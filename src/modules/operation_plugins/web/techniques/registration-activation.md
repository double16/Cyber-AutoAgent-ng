---
schema_version: 1
title: Registration and activation
purpose: Test whether account creation and activation enforce the identity, role and ownership prerequisites
  of protected functions.
applicability:
- A mapped registration or activation flow creates accounts, verifies a channel or grants an initial role.
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
  when: A mapped registration or activation flow creates accounts, verifies a channel or grants an initial
    role. The assigned work needs method-specific prerequisite checks.
  request: Registration and activation reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern registration and activation and the assigned work is designing future
    discriminating tests.
  request: Registration and activation hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit registration and activation hypothesis and its prerequisites are available for controlled
    verification.
  request: Registration and activation vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant registration and activation boundary has been verified and an authorized minimal
    demonstration with test data is required.
  request: Registration and activation execution
  signals_any: []
- phases:
  - analysis
  when: Recorded registration and activation observations or comparison outcomes require interpretation
    of policy, controls and remaining uncertainty.
  request: Registration and activation analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Authentication_Cheat_Sheet.md
  title: Authentication Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.626925+00:00'
  selection_reason: identity proof and account lifecycle.
- url: https://raw.githubusercontent.com/OWASP/wstg/v4.2/document/4-Web_Application_Security_Testing/03-Identity_Management_Testing/02-Test_User_Registration_Process.md
  title: wstg-registration
  retrieved_at: '2026-11-07T10:33:40.380782+00:00'
  selection_reason: WSTG-IDNT-02 conditions identity verification and registration entitlement on business/security
    requirements.
source_coverage: 'Selected retrieved references supply mechanism/control background: identity proof and
  account lifecycle. WSTG-IDNT-02 conditions identity verification and registration entitlement on business/security
  requirements. Limitations: Development-oriented guidance supplies mechanism/control background; application
  policy, live exploitability, negative controls and minimal-impact procedure remain original methodology
  requiring target-specific verification. Original controlled procedures and interpretation are knowledge-allowed
  synthesis, not individually source-verified. No generic reference establishes target applicability or
  exploitation.'
---

# Registration and activation

## Applicability and prerequisites

A mapped registration or activation flow creates accounts, verifies a channel or grants an initial
role.

Use controlled test inboxes, disposable accounts and a protected operation whose activation
requirement is established. Confirm whether unverified guest access is intentional and identify the
expected initial role and account-linking rules.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

An activated test account completes the protected operation, while its pre-activation state lacks
that entitlement. The normal activation message reaches the intended controlled inbox.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use controlled test inboxes, disposable accounts and a protected operation whose activation
   requirement is established. Confirm whether unverified guest access is intentional and identify
   the expected initial role and account-linking rules.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. From recorded registration transitions identify supplied role/state properties, activation token
   binding and duplicate-identity/linking behavior. Design future tests for an unactivated account
   and a second account's token without sending messages to uncontrolled addresses.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Create one disposable account through the permitted flow and verify its actual initial state
   using a server-side protected action.
2. Attempt the activation-required action before activation, varying only the activation transition
   or an observed client-side flag. Test role/property binding separately from activation.
3. With two test accounts, compare normal token redemption to cross-account, expired and
   already-consumed token use where the stated lifecycle permits these tests. Keep browser identity
   and token ownership explicit.
4. Complete normal activation and repeat the protected action. For duplicates or linking, verify
   whether an existing account is actually bound to the wrong controlled identity rather than merely
   producing a generic registration response.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Demonstrate one premature protected action or incorrect binding on disposable accounts, then
   restore role/ownership, invalidate tokens and remove accounts as agreed.
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

Some products intentionally allow limited pre-activation access or public signup. Resending an
activation email and returning a generic duplicate response are not themselves account takeover.

## Evidence requirements

Retain account/channel control, activation policy, state transitions, token ownership and lifecycle
comparisons, protected action outcomes and cleanup.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

A single channel does not cover invitations, federation or administrative provisioning. Do not
assert email ownership from the mere acceptance of an address string.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
