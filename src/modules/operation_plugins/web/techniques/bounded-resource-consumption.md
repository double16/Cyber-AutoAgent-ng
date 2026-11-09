---
schema_version: 1
title: Bounded resource-consumption testing
purpose: Evaluate resource-limit behavior within a fixed safe envelope without attempting service exhaustion.
applicability:
- A mapped request accepts an input dimension associated with measurable work, such as page size, file
  size, nesting or batch count.
- The relevant operation, identities, data and side effects are within the resolved assessment scope.
phases:
- reconnaissance
- hypothesis_generation
- vulnerability_verification
- analysis
active: true
triggers:
- phases:
  - reconnaissance
  when: A mapped request accepts an input dimension associated with measurable work, such as page size,
    file size, nesting or batch count. The assigned work needs method-specific prerequisite checks.
  request: Bounded resource-consumption testing reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern bounded resource-consumption testing and the assigned work is designing
    future discriminating tests.
  request: Bounded resource-consumption testing hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit bounded resource-consumption testing hypothesis and its prerequisites are available
    for controlled verification.
  request: Bounded resource-consumption testing vulnerability verification
  signals_any: []
- phases:
  - analysis
  when: Recorded bounded resource-consumption testing observations or comparison outcomes require interpretation
    of policy, controls and remaining uncertainty.
  request: Bounded resource-consumption testing analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Denial_of_Service_Cheat_Sheet.md
  title: Denial of Service Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.904026+00:00'
  selection_reason: resource limits and availability boundaries.
- url: https://owasp.org/Top10/2025/A10_2025-Mishandling_of_Exceptional_Conditions/
  title: A10 Mishandling of Exceptional Conditions - OWASP Top 10:2025
  retrieved_at: '2026-11-07T10:34:58.754036+00:00'
  selection_reason: OWASP2025 A10 category describes error handling, fail-open behavior, rollback and
    resource-exhaustion failure cases.
source_coverage: 'Selected retrieved references supply mechanism/control background: resource limits and
  availability boundaries. OWASP2025 A10 category describes error handling, fail-open behavior, rollback
  and resource-exhaustion failure cases. Limitations: Development-oriented guidance supplies mechanism/control
  background; application policy, live exploitability, negative controls and minimal-impact procedure
  remain original methodology requiring target-specific verification. Original controlled procedures and
  interpretation are knowledge-allowed synthesis, not individually source-verified. No generic reference
  establishes target applicability or exploitation.'
---

# Bounded resource-consumption testing

## Applicability and prerequisites

A mapped request accepts an input dimension associated with measurable work, such as page size, file
size, nesting or batch count.

Require explicit bounded-test authorization, numeric request/input/concurrency limits, cost and
side-effect limits, an abort latency/error threshold, recovery checks and isolated test data. If
resource or cost telemetry is unavailable, restrict conclusions to visible behavior and observed
limit enforcement.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

A small ordinary request completes correctly with stable approximate latency and output size; a
separate cheap control remains healthy. Establish the documented maximum or intended work policy
before interpreting permissive input as a defect.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Require explicit bounded-test authorization, numeric request/input/concurrency limits, cost and
   side-effect limits, an abort latency/error threshold, recovery checks and isolated test data. If
   resource or cost telemetry is unavailable, restrict conclusions to visible behavior and observed
   limit enforcement.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. From recorded requests identify one work-amplifying dimension and competing causes such as
   network/output transfer. Design a future small incremental comparison below the agreed ceiling, a
   boundary-rejection test only if safe, and a return-to-baseline check.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Measure the normal request and cheap health control without parallel load or hidden client
   retries. Record input size/count, response size, elapsed time and available test-scope cost/work
   telemetry.
2. Increase only one dimension through a few predetermined values within the hard ceiling; never
   adapt upward merely because the service still responds.
3. Compare accepted work and rejected/limited input against the established policy. Stop immediately
   at the first abort threshold, unexpected cost, queue growth or unrelated-service impact; do not
   seek a crash or maximum capacity.
4. Return to the original small input and health control, cancel queued jobs and verify recovery.
   Distinguish repeatable application-work amplification from network delay, cold caches and
   ordinary permitted expensive work.

The bounded verification is the complete permitted demonstration; no stress or exhaustion execution
phase is advertised. Clean up test jobs/data and confirm normal latency/error behavior before ending
the trial.

## Analysis

1. Compare the observed result with the established policy and the local controls above. A
   successful request, available artifact, changed response or tool execution establishes
   availability only; confirm the precise protected data or effect needed for the claim.
2. Classify the tested mechanism as supported, refuted for the tested case, or inconclusive. Keep
   the tested actor, object, state and channel explicit, and preserve competing explanations and
   untested variants.

### False positives and competing explanations

Large valid requests can intentionally cost more, and client-visible latency does not reveal server
CPU/memory use. Missing a public rate-limit header is not evidence of unlimited backend work.

## Evidence requirements

Retain the hard envelope and abort rules, work policy, actual input/request counts,
baseline/variation/recovery measurements, limit responses and cleanup result.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

No extrapolation to denial of service, maximum capacity or financial impact is justified from a
bounded external trial. Unknown resource telemetry or thresholds remain explicit gaps.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
