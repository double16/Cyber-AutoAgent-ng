---
schema_version: 1
title: Cache poisoning
purpose: Test whether attacker-influenced response content is stored and served to another request outside
  its intended cache variation.
applicability:
- A mapped route is cached and an input may affect its response without being represented in the cache
  key.
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
  when: A mapped route is cached and an input may affect its response without being represented in the
    cache key. The assigned work needs method-specific prerequisite checks.
  request: Cache poisoning reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern cache poisoning and the assigned work is designing future discriminating
    tests.
  request: Cache poisoning hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit cache poisoning hypothesis and its prerequisites are available for controlled verification.
  request: Cache poisoning vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant cache poisoning boundary has been verified and an authorized minimal demonstration
    with test data is required.
  request: Cache poisoning execution
  signals_any: []
- phases:
  - analysis
  when: Recorded cache poisoning observations or comparison outcomes require interpretation of policy,
    controls and remaining uncertainty.
  request: Cache poisoning analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/HTTP_Headers_Cheat_Sheet.md
  title: HTTP Security Response Headers Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.790016+00:00'
  selection_reason: cache-control semantics; poisoning experiments are original synthesis.
- url: https://portswigger.net/web-security/web-cache-poisoning
  title: Web cache poisoning | Web Security Academy
  retrieved_at: '2026-11-07T10:32:59.260561+00:00'
  selection_reason: Original PortSwigger academy methodology distinguishes cache keys, unkeyed input,
    harmful response and cached delivery.
- url: https://www.rfc-editor.org/rfc/rfc9111.txt
  title: cache-rfc9111
  retrieved_at: '2026-11-06T21:29:21.193551+00:00'
  selection_reason: RFC9111 defines storage/reuse restrictions, cache keys, authenticated response treatment
    and cache-control semantics.
source_coverage: 'Selected retrieved references supply mechanism/control background: cache-control semantics;
  poisoning experiments are original synthesis. Original PortSwigger academy methodology distinguishes
  cache keys, unkeyed input, harmful response and cached delivery. RFC9111 defines storage/reuse restrictions,
  cache keys, authenticated response treatment and cache-control semantics. Limitations: Development-oriented
  guidance supplies mechanism/control background; application policy, live exploitability, negative controls
  and minimal-impact procedure remain original methodology requiring target-specific verification. Examples
  are illustrative. Shared-cache exploitation can affect other users, so bounded author-owned cache keys
  and harmless markers are original restrictions. Original controlled procedures and interpretation are
  knowledge-allowed synthesis, not individually source-verified. No generic reference establishes target
  applicability or exploitation.'
---

# Cache poisoning

## Applicability and prerequisites

A mapped route is cached and an input may affect its response without being represented in the cache
key.

Use an isolated agreed cache namespace, synthetic inert markers, two independent clients and a
purge/expiry plan. Establish which requests are meant to share content; do not poison common
production URLs or rely on an arbitrary cache-buster that changes the behavior being tested.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

Repeated ordinary requests to the isolated route produce stable content and evidence consistent with
cache reuse. Distinguish origin-generated headers from intermediary cache observations where
possible.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use an isolated agreed cache namespace, synthetic inert markers, two independent clients and a
   purge/expiry plan. Establish which requests are meant to share content; do not poison common
   production URLs or rely on an arbitrary cache-buster that changes the behavior being tested.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. Inspect recorded response influence and cache indicators. Design future priming with one
   candidate unkeyed input followed by a clean reader request; propose a distinct uncached/origin
   comparison and a clean namespace control.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Establish the clean reader response and the isolation/purge mechanism before priming. Record
   cache-relevant request dimensions such as authorization, cookies, query and Vary.
2. Prime the test namespace with one inert response-influencing header or parameter. Confirm that
   the candidate response actually contains the synthetic marker and appears stored.
3. From a second clean client, request the same intended cache key without the influencing input.
   Verify the served marker, age/hit behavior and absence of another credential or request path that
   independently generates it.
4. Compare a fresh namespace, a normal priming request and an origin/expired response where
   available. Repeat after purge to distinguish shared cached influence from mere reflection or
   persistent application state.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Show one inert marker crossing to the clean reader in the isolated cache namespace. Purge or
   allow agreed expiry, then verify the clean baseline from both clients.
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

A marker can reflect in each request independently, originate from stored application data or be
served by a local browser cache. Cache-status headers alone do not prove the requested shared-cache
behavior.

## Evidence requirements

Retain intended sharing policy, namespace isolation, priming input, clean-reader request, marker
persistence and cache/origin/expiry controls plus cleanup.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

Cache keys can differ by point of presence, method, cookie or protocol. A successful isolated
experiment does not establish global reach or script execution.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
