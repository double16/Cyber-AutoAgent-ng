---
schema_version: 1
title: Excessive data exposure
purpose: Test whether API responses disclose properties the requesting actor is not permitted to receive.
applicability:
- A recorded response includes properties beyond those displayed or needed by a client workflow.
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
  when: A recorded response includes properties beyond those displayed or needed by a client workflow.
    The assigned work needs method-specific prerequisite checks.
  request: Excessive data exposure reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern excessive data exposure and the assigned work is designing future
    discriminating tests.
  request: Excessive data exposure hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit excessive data exposure hypothesis and its prerequisites are available for controlled
    verification.
  request: Excessive data exposure vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant excessive data exposure boundary has been verified and an authorized minimal demonstration
    with test data is required.
  request: Excessive data exposure execution
  signals_any: []
- phases:
  - analysis
  when: Recorded excessive data exposure observations or comparison outcomes require interpretation of
    policy, controls and remaining uncertainty.
  request: Excessive data exposure analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/REST_Security_Cheat_Sheet.md
  title: REST Security Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.745034+00:00'
  selection_reason: response minimization and property authorization.
source_coverage: 'Selected retrieved references supply mechanism/control background: response minimization
  and property authorization. Limitations: Development-oriented guidance supplies mechanism/control background;
  application policy, live exploitability, negative controls and minimal-impact procedure remain original
  methodology requiring target-specific verification. Original controlled procedures and interpretation
  are knowledge-allowed synthesis, not individually source-verified. No generic reference establishes
  target applicability or exploitation.'
---

# Excessive data exposure

## Applicability and prerequisites

A recorded response includes properties beyond those displayed or needed by a client workflow.

Establish property-level confidentiality policy for the actor and audience, not merely UI
visibility. Use synthetic records with distinguishable ordinary and restricted fields, and an
authorized comparison identity where possible.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

The actor can receive the intended ordinary record fields; an authorized higher-trust view can
identify the synthetic restricted value without accessing real confidential data.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Establish property-level confidentiality policy for the actor and audience, not merely UI
   visibility. Use synthetic records with distinguishable ordinary and restricted fields, and an
   authorized comparison identity where possible.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. Inspect recorded response objects, nested relations, list/detail variants and export schemas.
   Design future comparisons of the same property under permitted and prohibited actor contexts;
   record unknown classification as a policy question.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Capture the intended response as the tested actor and identify concrete properties and values,
   not just response size.
2. Compare the same synthetic record through an authorized view to establish the restricted
   property's meaning and provenance.
3. Repeat the response as the lower-trust actor, varying only the supported projection, expansion or
   observed representation option where relevant. Inspect the actual response body rather than what
   the UI renders.
4. Compare a record without the restricted value and an allowed-public property. Rule out sample
   values, redaction placeholders and actor-specific authorization before classifying disclosure.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Retrieve only the one synthetic restricted property needed to demonstrate disclosure. Remove any
   optional expanded test data and verify the intended record state remains intact.
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

Unused UI fields are not automatically confidential; public directory data or intentionally shared
metadata may be legitimate. Opaque IDs and one-way digests need contextual assessment rather than
automatic secret classification.

## Evidence requirements

Retain the disclosure policy, audience and identity, concrete restricted property provenance,
response bytes and permitted/negative comparisons.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

Response minimization is a design recommendation; a vulnerability conclusion requires an actual
unauthorized disclosure. One endpoint does not cover alternate formats, pagination, errors or
exports.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
