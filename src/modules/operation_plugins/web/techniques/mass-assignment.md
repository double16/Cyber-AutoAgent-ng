---
schema_version: 1
title: Mass assignment
purpose: Test whether client-supplied properties change server-owned or unauthorized fields through automatic
  binding.
applicability:
- A mapped create or update operation accepts structured fields, including opaque chat/API request objects.
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
  when: A mapped create or update operation accepts structured fields, including opaque chat/API request
    objects. The assigned work needs method-specific prerequisite checks.
  request: Mass assignment reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern mass assignment and the assigned work is designing future discriminating
    tests.
  request: Mass assignment hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit mass assignment hypothesis and its prerequisites are available for controlled verification.
  request: Mass assignment vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant mass assignment boundary has been verified and an authorized minimal demonstration
    with test data is required.
  request: Mass assignment execution
  signals_any: []
- phases:
  - analysis
  when: Recorded mass assignment observations or comparison outcomes require interpretation of policy,
    controls and remaining uncertainty.
  request: Mass assignment analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Mass_Assignment_Cheat_Sheet.md
  title: Mass Assignment Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.587719+00:00'
  selection_reason: binding unexpected fields to server-side properties.
source_coverage: 'Selected retrieved references supply mechanism/control background: binding unexpected
  fields to server-side properties. Limitations: Development-oriented guidance supplies mechanism/control
  background; application policy, live exploitability, negative controls and minimal-impact procedure
  remain original methodology requiring target-specific verification. Original controlled procedures and
  interpretation are knowledge-allowed synthesis, not individually source-verified. No generic reference
  establishes target applicability or exploitation.'
---

# Mass assignment

## Applicability and prerequisites

A mapped create or update operation accepts structured fields, including opaque chat/API request
objects.

Use a disposable object and an actor with permission to update a known ordinary field. Establish
field-level policy from the product contract or an authorized owner; a field missing from the UI is
only a candidate, not proof that it is forbidden.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

A documented permitted field changes and can be read back independently. Record the initial value of
the candidate restricted field and the actor's actual grant to change it.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use a disposable object and an actor with permission to update a known ordinary field. Establish
   field-level policy from the product contract or an authorized owner; a field missing from the UI
   is only a candidate, not proof that it is forbidden.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. Compare recorded request and response schemas and publicly exposed contracts. Design one-property
   future variations for server-owned fields, nested properties and operation-specific inputs. In
   chat APIs, model/tool settings are candidates only when that actor's control is restricted.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Repeat the ordinary update and read back its persisted value, preserving all authorization and
   object context.
2. Add or modify one candidate field with an inert reversible value; keep the ordinary field and
   other inputs fixed. Do not guess dangerous property sets in bulk.
3. Read back the candidate property through an independent authorized view or verify its specific
   downstream effect. Reflection, 2xx, accepted unknown keys or changed model prose alone is
   insufficient.
4. Compare omission of the candidate, a meaningless unknown field and a correctly authorized update
   where available. Distinguish schema rejection, ignored input and a policy-approved change from
   unauthorized binding.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Demonstrate one reversible prohibited field transition on a test object. If privilege or billing
   fields cannot be safely restored, stop at the agreed inert effect instead. Restore original
   values and verify them independently.
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

Flexible metadata can legitimately store arbitrary client fields. A tool/model toggle can be a
documented user feature. Response serialization can echo input without changing persistent state.

## Evidence requirements

Retain field policy, actor entitlement, original and modified bodies, independent read-back or
deterministic effect, omission/unknown-field controls and cleanup.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

Rejecting one property does not establish a general allowlist. Creation, update, nested objects and
alternate serializers can bind differently. Black-box evidence may show the unauthorized transition
without proving the framework's internal binding mechanism.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
