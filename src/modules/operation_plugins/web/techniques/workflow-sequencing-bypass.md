---
schema_version: 1
title: Workflow sequencing bypass
purpose: Test whether the server enforces mandatory state transitions and prerequisites for a business
  workflow.
applicability:
- A mapped multistep operation has a security-relevant sequence such as approval before fulfillment.
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
  when: A mapped multistep operation has a security-relevant sequence such as approval before fulfillment.
    The assigned work needs method-specific prerequisite checks.
  request: Workflow sequencing bypass reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern workflow sequencing bypass and the assigned work is designing future
    discriminating tests.
  request: Workflow sequencing bypass hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit workflow sequencing bypass hypothesis and its prerequisites are available for controlled
    verification.
  request: Workflow sequencing bypass vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant workflow sequencing bypass boundary has been verified and an authorized minimal demonstration
    with test data is required.
  request: Workflow sequencing bypass execution
  signals_any: []
- phases:
  - analysis
  when: Recorded workflow sequencing bypass observations or comparison outcomes require interpretation
    of policy, controls and remaining uncertainty.
  request: Workflow sequencing bypass analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/wstg/v4.2/document/4-Web_Application_Security_Testing/10-Business_Logic_Testing/06-Testing_for_the_Circumvention_of_Work_Flows.md
  title: wstg-workflow
  retrieved_at: '2026-11-07T10:33:40.381114+00:00'
  selection_reason: WSTG-BUSL-06 describes omitted/reordered steps, cancellation and rollback misuse cases.
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/REST_Security_Cheat_Sheet.md
  title: REST Security Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.745034+00:00'
  selection_reason: business abuse and workflow assumptions.
source_coverage: 'Selected retrieved references supply mechanism/control background: WSTG-BUSL-06 describes
  omitted/reordered steps, cancellation and rollback misuse cases. business abuse and workflow assumptions.
  Limitations: Development-oriented guidance supplies mechanism/control background; application policy,
  live exploitability, negative controls and minimal-impact procedure remain original methodology requiring
  target-specific verification. Original controlled procedures and interpretation are knowledge-allowed
  synthesis, not individually source-verified. No generic reference establishes target applicability or
  exploitation.'
---

# Workflow sequencing bypass

## Applicability and prerequisites

A mapped multistep operation has a security-relevant sequence such as approval before fulfillment.

Use disposable workflow objects and an established state-transition contract. Identify which steps
are mandatory, which are optional UI conveniences, the authorized actor for each, and how final
state or side effects can be observed and reversed.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

Complete one legitimate sequence on test data and record each server state and resulting effect. An
object missing the mandatory prerequisite should not receive the protected final effect.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use disposable workflow objects and an established state-transition contract. Identify which
   steps are mandatory, which are optional UI conveniences, the authorized actor for each, and how
   final state or side effects can be observed and reversed.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. From recorded transitions design future skip, repeat, reorder and stale-state submissions. Name
   the exact invariant and expected prohibited effect; do not assume every page visit is a required
   server-side transition.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Create a fresh test object in the known initial state and verify its current status through the
   authoritative read operation.
2. Invoke one observed later transition with one mandatory prior step omitted, keeping actor, object
   and transaction values otherwise valid.
3. Inspect the final server state and actual effect, including queued processing, rather than a
   client success screen. Separately test repetition or a stale transition against the established
   contract.
4. Compare a normally sequenced object and an invalid-state control, then repeat on a fresh object.
   If an alternate allowed path satisfies the same invariant, classify the difference as legitimate
   rather than a bypass.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Demonstrate one reversible forbidden transition using a test object without real fulfillment,
   money movement or external notification. Cancel or restore the object and verify compensating
   actions completed.
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

Idempotent repeats, optional wizard pages, delegated approvals and alternate valid state paths can
be intentional. An accepted asynchronous job may reject the invalid state later.

## Evidence requirements

Retain the transition policy, initial and resulting server states, exact omitted/reordered step,
valid-sequence and invalid-state controls and final effect/cleanup.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

State machines may span actors and services. If final effect or required policy cannot be observed,
a sequence discrepancy remains a candidate rather than a confirmed violation.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
