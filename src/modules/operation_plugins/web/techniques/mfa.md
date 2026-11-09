---
schema_version: 1
title: MFA
purpose: Test whether required second-factor and recovery transitions bind to the correct actor, session
  and action.
applicability:
- A mapped authentication or sensitive-action flow requires MFA, enrollment or factor recovery.
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
  when: A mapped authentication or sensitive-action flow requires MFA, enrollment or factor recovery.
    The assigned work needs method-specific prerequisite checks.
  request: MFA reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern mfa and the assigned work is designing future discriminating tests.
  request: MFA hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit mfa hypothesis and its prerequisites are available for controlled verification.
  request: MFA vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant mfa boundary has been verified and an authorized minimal demonstration with test
    data is required.
  request: MFA execution
  signals_any: []
- phases:
  - analysis
  when: Recorded mfa observations or comparison outcomes require interpretation of policy, controls and
    remaining uncertainty.
  request: MFA analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Multifactor_Authentication_Cheat_Sheet.md
  title: Multifactor Authentication Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.789184+00:00'
  selection_reason: factor lifecycle, recovery and bypass risks.
source_coverage: 'Selected retrieved references supply mechanism/control background: factor lifecycle,
  recovery and bypass risks. Limitations: Development-oriented guidance supplies mechanism/control background;
  application policy, live exploitability, negative controls and minimal-impact procedure remain original
  methodology requiring target-specific verification. Original controlled procedures and interpretation
  are knowledge-allowed synthesis, not individually source-verified. No generic reference establishes
  target applicability or exploitation.'
---

# MFA

## Applicability and prerequisites

A mapped authentication or sensitive-action flow requires MFA, enrollment or factor recovery.

Use disposable accounts with controlled factors and recovery access. Establish which paths require
MFA, allowed recovery alternatives, freshness and one-use rules. Set strict failed-attempt and
notification caps; do not trigger push fatigue.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

The complete required factor sequence reaches an agreed protected resource; a primary-factor-only
session cannot reach that resource under the established policy.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use disposable accounts with controlled factors and recovery access. Establish which paths
   require MFA, allowed recovery alternatives, freshness and one-use rules. Set strict
   failed-attempt and notification caps; do not trigger push fatigue.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. From recorded flows distinguish pre-MFA session, factor verification and fully authenticated
   state. Design future skipped-step, cross-session, consumed-code and recovery comparisons,
   accounting for deliberately trusted devices and factor-specific replay windows.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Create a fresh primary-factor-only session and query the agreed protected resource directly using
   that session alone.
2. Compare complete MFA with omission of the factor transition or one invalid proof. Verify server
   identity and access after the attempt rather than relying on a client redirect.
3. With two controlled sessions/accounts, vary only proof ownership or session binding. Test
   consumed-proof reuse against the factor's stated policy; do not assume all TOTP implementations
   reject every same-window reuse.
4. Review the observed recovery, disablement and factor-replacement operations using the same
   account. Check their declared authentication requirements and final factor state, without
   guessing undocumented bypass endpoints.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Demonstrate access to one synthetic protected marker or one reversible factor change on the test
   account. Restore factors, invalidate backup codes and test sessions as supported, and verify
   recovery access remains usable.
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

A remembered-device grant, passkey user verification, upstream MFA or intentional recovery path may
satisfy policy legitimately. Enrollment success does not establish that a protected login was
bypassed.

## Evidence requirements

Retain factor policy and proof lifecycle, clean session provenance, pre/post-MFA protected-resource
results, cross-session controls and restored factor state.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

These comparisons test orchestration, not cryptographic strength of authenticators. Factor-specific
behavior and recovery policy must be established rather than assumed.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
