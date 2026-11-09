---
schema_version: 1
title: Error-based information disclosure
purpose: Determine whether controlled application errors disclose information beyond the tested actor's
  permitted audience.
applicability:
- A recorded response exposes detailed errors, stack traces or internal application data.
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
  when: A recorded response exposes detailed errors, stack traces or internal application data. The assigned
    work needs method-specific prerequisite checks.
  request: Error-based information disclosure reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern error-based information disclosure and the assigned work is designing
    future discriminating tests.
  request: Error-based information disclosure hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit error-based information disclosure hypothesis and its prerequisites are available
    for controlled verification.
  request: Error-based information disclosure vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant error-based information disclosure boundary has been verified and an authorized minimal
    demonstration with test data is required.
  request: Error-based information disclosure execution
  signals_any: []
- phases:
  - analysis
  when: Recorded error-based information disclosure observations or comparison outcomes require interpretation
    of policy, controls and remaining uncertainty.
  request: Error-based information disclosure analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Error_Handling_Cheat_Sheet.md
  title: Error Handling Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.907587+00:00'
  selection_reason: safe errors and internal detail exposure.
source_coverage: 'Selected retrieved references supply mechanism/control background: safe errors and internal
  detail exposure. Limitations: Development-oriented guidance supplies mechanism/control background; application
  policy, live exploitability, negative controls and minimal-impact procedure remain original methodology
  requiring target-specific verification. Original controlled procedures and interpretation are knowledge-allowed
  synthesis, not individually source-verified. No generic reference establishes target applicability or
  exploitation.'
---

# Error-based information disclosure

## Applicability and prerequisites

A recorded response exposes detailed errors, stack traces or internal application data.

Use one mapped operation, bounded benign invalid inputs and established sensitivity criteria. Set a
small error-request cap and avoid payloads that induce resource exhaustion, repeated external work
or destructive actions.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

A valid request completes normally; an ordinary invalid request produces the intended public error
behavior without exposing synthetic restricted data.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use one mapped operation, bounded benign invalid inputs and established sensitivity criteria. Set
   a small error-request cap and avoid payloads that induce resource exhaustion, repeated external
   work or destructive actions.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. Inspect recorded error fields for concrete leaked information and plausible benign explanations.
   Design future one-input variations that separate application exceptions from proxy/client errors;
   a technology name alone need not be a consequential disclosure.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Repeat the valid request and the smallest safely invalid version, such as a wrong scalar type,
   preserving actor and endpoint context.
2. Inspect full bounded response content and headers for actual stack paths, queries, account data
   or synthetic secret markers. Identify what the tested actor can infer beyond normal public
   responses.
3. Compare a second inert invalid value and a nonexistent route to establish whether content
   originates from the target application or a generic intermediary error page.
4. Repeat once after normal state is restored. Redact secret values in shareable records and do not
   use any disclosed credential or chain an unverified error into a claim of injection or code
   execution.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Demonstrate one minimal reproducible disclosure containing a synthetic marker or the least
   necessary nonpublic fragment. Restore any test record and stop inducing errors once attribution
   is clear.
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

Public framework/version strings, correlation IDs and intended validation messages can be benign.
Proxy-generated errors may reveal the assessment tool's environment rather than target internals.

## Evidence requirements

Retain sensitivity basis, valid/invalid request pairs, exact bounded leaked fragment, attribution
controls and reproducibility result.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

Disclosure severity depends on content, audience and use; not every verbose error is exploitable.
One error class does not establish behavior of other handlers or production modes.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
