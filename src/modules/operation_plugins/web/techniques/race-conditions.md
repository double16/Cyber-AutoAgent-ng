---
schema_version: 1
title: Race conditions
purpose: Test whether concurrent operations violate a state invariant that correctly sequenced operations
  preserve.
applicability:
- A mapped state-changing operation consumes a single-use or quantity-limited test resource.
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
  when: A mapped state-changing operation consumes a single-use or quantity-limited test resource. The
    assigned work needs method-specific prerequisite checks.
  request: Race conditions reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern race conditions and the assigned work is designing future discriminating
    tests.
  request: Race conditions hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit race conditions hypothesis and its prerequisites are available for controlled verification.
  request: Race conditions vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant race conditions boundary has been verified and an authorized minimal demonstration
    with test data is required.
  request: Race conditions execution
  signals_any: []
- phases:
  - analysis
  when: Recorded race conditions observations or comparison outcomes require interpretation of policy,
    controls and remaining uncertainty.
  request: Race conditions analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Transaction_Authorization_Cheat_Sheet.md
  title: Transaction Authorization Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.875723+00:00'
  selection_reason: transaction authorization; concurrency tests are original synthesis.
- url: https://portswigger.net/web-security/race-conditions
  title: Race conditions | Web Security Academy
  retrieved_at: '2026-11-07T10:32:59.370977+00:00'
  selection_reason: Original PortSwigger academy methodology describes limit-overrun race windows and
    comparing sequential/concurrent behavior.
source_coverage: 'Selected retrieved references supply mechanism/control background: transaction authorization;
  concurrency tests are original synthesis. Original PortSwigger academy methodology describes limit-overrun
  race windows and comparing sequential/concurrent behavior. Limitations: Development-oriented guidance
  supplies mechanism/control background; application policy, live exploitability, negative controls and
  minimal-impact procedure remain original methodology requiring target-specific verification. Tool recipes
  are optional and uncontrolled high-volume timing attacks are outside scope; derive bounded concurrency
  from explicit operation constraints. Original controlled procedures and interpretation are knowledge-allowed
  synthesis, not individually source-verified. No generic reference establishes target applicability or
  exploitation.'
---

# Race conditions

## Applicability and prerequisites

A mapped state-changing operation consumes a single-use or quantity-limited test resource.

Use isolated disposable objects, an explicit invariant, final-state visibility, reversible effects
and a strict concurrency/request cap. Verify whether operations are supposed to be exclusive or
idempotent; multiple successes can be legitimate.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

One request succeeds as allowed; a second sequential request after completion cannot violate the
same invariant. Record initial and final counts or unique event identities.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use isolated disposable objects, an explicit invariant, final-state visibility, reversible
   effects and a strict concurrency/request cap. Verify whether operations are supposed to be
   exclusive or idempotent; multiple successes can be legitimate.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. From recorded check/commit transitions identify a possible race window. Design a future small
   synchronized pair and sequential controls, distinguishing duplicate acknowledgements from
   duplicated committed effects.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Reset a disposable object and run the sequential pair to establish legitimate single-use or
   quantity behavior.
2. On a new equivalent object, issue only the agreed small concurrent pair with the same relevant
   state and explicit idempotency-key policy. Avoid large bursts or attempts to affect other users.
3. Wait for all queued work to settle and inspect authoritative final counts, balances or event
   identities. Two 200 responses do not prove two effects; one success response does not rule out
   duplicate work.
4. Compare sequential, concurrent and single-request trials on fresh objects, accounting for retries
   and client timeouts. Repeat only within the fixed cap; classify unstable, unobservable or
   naturally nonexclusive behavior as inconclusive.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Demonstrate the smallest extra synthetic resource consumption or state transition on an isolated
   object. Restore quantities or cancel duplicates, verify final state and stop immediately if
   contention affects unrelated traffic.
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

Automatic retries, eventual consistency, legitimate parallel reservations and duplicate
acknowledgements can resemble races. A final invariant violation needs attribution to concurrency
rather than separate permissions or malformed values.

## Evidence requirements

Retain the invariant, initial state, request timing/identities, idempotency policy, sequential
controls, settled final state and cleanup.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

A bounded trial may miss narrow windows and cannot prove absence. Results depend on load, worker
topology and transaction isolation; do not extrapolate a success rate from a handful of trials.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
