---
schema_version: 1
title: Webhook authenticity and replay
purpose: Test whether a webhook receiver authenticates the actual message and handles duplicate delivery
  according to its event policy.
applicability:
- A mapped webhook receiver accepts an event from an upstream integration that can be exercised in a test
  context.
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
  when: A mapped webhook receiver accepts an event from an upstream integration that can be exercised
    in a test context. The assigned work needs method-specific prerequisite checks.
  request: Webhook authenticity and replay reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern webhook authenticity and replay and the assigned work is designing
    future discriminating tests.
  request: Webhook authenticity and replay hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit webhook authenticity and replay hypothesis and its prerequisites are available for
    controlled verification.
  request: Webhook authenticity and replay vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant webhook authenticity and replay boundary has been verified and an authorized minimal
    demonstration with test data is required.
  request: Webhook authenticity and replay execution
  signals_any: []
- phases:
  - analysis
  when: Recorded webhook authenticity and replay observations or comparison outcomes require interpretation
    of policy, controls and remaining uncertainty.
  request: Webhook authenticity and replay analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/REST_Security_Cheat_Sheet.md
  title: REST Security Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.745034+00:00'
  selection_reason: message integrity and API trust; webhook replay controls are original synthesis.
- url: https://raw.githubusercontent.com/standard-webhooks/standard-webhooks/main/spec/standard-webhooks.md
  title: standard-webhooks
  retrieved_at: '2026-11-07T10:32:59.029451+00:00'
  selection_reason: Maintainer specification describes signed message identity/timestamp/body, timestamp
    checks and delivery reliability.
source_coverage: 'Selected retrieved references supply mechanism/control background: message integrity
  and API trust; webhook replay controls are original synthesis. Maintainer specification describes signed
  message identity/timestamp/body, timestamp checks and delivery reliability. Limitations: Development-oriented
  guidance supplies mechanism/control background; application policy, live exploitability, negative controls
  and minimal-impact procedure remain original methodology requiring target-specific verification. A community
  maintainer specification, not a universal protocol requirement. Legitimate redelivery is expected; replay
  is a defect only when prohibited duplicate effects occur. Original controlled procedures and interpretation
  are knowledge-allowed synthesis, not individually source-verified. No generic reference establishes
  target applicability or exploitation.'
---

# Webhook authenticity and replay

## Applicability and prerequisites

A mapped webhook receiver accepts an event from an upstream integration that can be exercised in a
test context.

Use a legitimate synthetic event, controlled test sender or authorized delivery fixture, a
reversible downstream effect and the integration's signing/canonicalization/timestamp policy. Do not
invent a signing scheme or assume every redelivery must be rejected.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

A correctly authenticated fresh event creates its intended test effect. Record exact body bytes,
event identifier, timestamp, signature format and the account/endpoint context bound by the
integration.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use a legitimate synthetic event, controlled test sender or authorized delivery fixture, a
   reversible downstream effect and the integration's signing/canonicalization/timestamp policy. Do
   not invent a signing scheme or assume every redelivery must be rejected.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. Inspect recorded delivery and retry semantics. Design future missing/invalid-signature, one-byte
   mutation, stale-timestamp and duplicate-event comparisons, separating transport acknowledgement
   from prohibited repeated business effects.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Deliver one legitimate test event and verify its final downstream effect after any asynchronous
   processing.
2. Change one signed body value without recalculating the signature, then separately omit or
   invalidate authentication metadata. Confirm whether the changed event is processed, not just
   whether it receives 2xx.
3. Replay the exact valid delivery within and, where policy specifies, outside the accepted
   timestamp window. Preserve event identity and distinguish authentic redelivery from a new event.
4. Compare valid redelivery behavior to the declared idempotency rule. A receiver may acknowledge
   duplicates or even legitimately repeat a nonexclusive action; only a prohibited duplicate effect
   establishes a replay defect.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Demonstrate one unauthorized synthetic event effect or prohibited duplicate effect on a
   disposable object. Cancel/revert effects and remove queued deliveries; rotate only test secrets
   if changed.
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

Signature verification can occur after a quick acknowledgement. Retries are normal, timestamps may
permit bounded skew, and event IDs can scope idempotency differently by tenant or endpoint.

## Evidence requirements

Retain the actual signing and retry policy, exact message bytes and authentication changes, test
endpoint/account binding, processed-event identities and final effect controls.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

Without a legitimate signed fixture or observable effect, authenticity/replay behavior cannot be
established conclusively. A failed signature using the wrong canonicalization tests the fixture, not
the receiver.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
