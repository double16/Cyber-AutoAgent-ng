---
schema_version: 1
title: Cache deception
purpose: Test whether a cache stores a personalized response under a URL classified as publicly cacheable
  and serves it to an excluded reader.
applicability:
- A mapped personalized route and cache layer may interpret path suffixes or normalization differently.
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
  when: A mapped personalized route and cache layer may interpret path suffixes or normalization differently.
    The assigned work needs method-specific prerequisite checks.
  request: Cache deception reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern cache deception and the assigned work is designing future discriminating
    tests.
  request: Cache deception hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit cache deception hypothesis and its prerequisites are available for controlled verification.
  request: Cache deception vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant cache deception boundary has been verified and an authorized minimal demonstration
    with test data is required.
  request: Cache deception execution
  signals_any: []
- phases:
  - analysis
  when: Recorded cache deception observations or comparison outcomes require interpretation of policy,
    controls and remaining uncertainty.
  request: Cache deception analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/HTTP_Headers_Cheat_Sheet.md
  title: HTTP Security Response Headers Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.790016+00:00'
  selection_reason: cache-control semantics; routing/cache mismatch experiments are original synthesis.
- url: https://portswigger.net/web-security/web-cache-deception
  title: Web cache deception | Web Security Academy
  retrieved_at: '2026-11-07T10:32:59.260592+00:00'
  selection_reason: Original PortSwigger academy methodology describes cache/origin path mapping, delimiter
    and normalization discrepancies.
- url: https://www.rfc-editor.org/rfc/rfc9111.txt
  title: cache-rfc9111
  retrieved_at: '2026-11-06T21:29:21.193551+00:00'
  selection_reason: RFC9111 defines storage/reuse restrictions, cache keys, authenticated response treatment
    and cache-control semantics.
source_coverage: 'Selected retrieved references supply mechanism/control background: cache-control semantics;
  routing/cache mismatch experiments are original synthesis. Original PortSwigger academy methodology
  describes cache/origin path mapping, delimiter and normalization discrepancies. RFC9111 defines storage/reuse
  restrictions, cache keys, authenticated response treatment and cache-control semantics. Limitations:
  Development-oriented guidance supplies mechanism/control background; application policy, live exploitability,
  negative controls and minimal-impact procedure remain original methodology requiring target-specific
  verification. Cacheability and origin behavior remain deployment-specific. A cache-like header alone
  does not establish shared delivery of private data. Original controlled procedures and interpretation
  are knowledge-allowed synthesis, not individually source-verified. No generic reference establishes
  target applicability or exploitation.'
---

# Cache deception

## Applicability and prerequisites

A mapped personalized route and cache layer may interpret path suffixes or normalization
differently.

Use a disposable account with a unique synthetic private marker, a separate anonymous or excluded
reader, and an isolated test URL with purge/expiry control. Establish the marker's confidentiality
and avoid creating caches of real private data.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

The account reads the marker from the normal personalized route; the separate reader cannot obtain
it there. Determine whether the cache currently stores or bypasses that route.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use a disposable account with a unique synthetic private marker, a separate anonymous or excluded
   reader, and an isolated test URL with purge/expiry control. Establish the marker's
   confidentiality and avoid creating caches of real private data.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. Review recorded route normalization and static-path cache rules. Design a future harmless
   suffix/normalization variant that might reach the same personalized handler while receiving
   different cache treatment; do not assume a file-looking suffix is cached.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Request the agreed path variant as the test account and determine whether it reaches the same
   personalized content, a static file, an error or a redirect.
2. Check evidence of shared storage for that exact variant while preserving its cache-relevant
   headers and path bytes.
3. Request that variant from the clean excluded reader with no account cookies or authorization. A
   violation requires the private synthetic marker to be served to that reader, not merely a cache
   hit or successful authorized response.
4. Compare the ordinary route, a nonexistent variant and a fresh isolated namespace. Purge and
   repeat in controlled order to rule out local caching, public content and accidental reuse of the
   account's credentials.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Demonstrate disclosure of only the synthetic marker on the isolated variant. Purge every primed
   variant and confirm the excluded reader no longer receives private content.
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

An application can intentionally map suffixes to public content, or a cache can bypass all
authenticated responses. Error pages and redirected destinations may look similar without containing
personalized data.

## Evidence requirements

Retain confidentiality and isolation basis, exact route bytes, authenticated priming and
credential-free reader requests, private marker, cache controls and purge verification.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

A tested path variant does not cover all normalization rules, cache nodes or content types. Do not
infer credential leakage from cacheability headers alone.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
