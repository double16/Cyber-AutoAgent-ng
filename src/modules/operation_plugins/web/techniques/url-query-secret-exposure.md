---
schema_version: 1
title: Sensitive values in URL query strings
purpose: Trace sensitive URL query values into actual browser, request, or logging channels and assess exposure against intended recipients and retention policy.
applicability:
  - An assigned request, redirect, or browser flow places a potentially sensitive value in a URL query string.
phases: [reconnaissance, hypothesis_generation, vulnerability_verification, execution, analysis]
active: true
triggers:
  - phases: [reconnaissance, hypothesis_generation, vulnerability_verification, execution, analysis]
    when: An assigned flow carries a potentially sensitive query value whose recipients, persistence, or propagation need assessment.
    request: Sensitive values in URL query strings
    signals_any: []
sources:
  - url: https://community.owasp.org/vulnerabilities/Information_exposure_through_query_strings_in_url
    title: OWASP Information exposure through query strings in URL
    retrieved_at: '2026-11-12'
    selection_reason: Describes query-value exposure channels including history and logs; its broad Referer examples need policy-specific qualification.
  - url: https://w3c.github.io/webappsec-referrer-policy/
    title: Referrer Policy editor's draft
    retrieved_at: '2026-11-12'
    selection_reason: Describes policy-dependent referrer propagation; this is a work-in-progress editor's draft rather than a Recommendation.
  - url: https://www.rfc-editor.org/rfc/rfc9700.html
    title: RFC 9700 OAuth 2.0 Security Best Current Practice
    retrieved_at: '2026-11-12'
    selection_reason: Discusses OAuth credential leakage through Referer and browser history and the controls affecting credential replay.
source_coverage: References supply exposure-channel and policy background, with RFC 9700 specific to OAuth flows. The editor's draft is not a finalized standard or proof of browser implementation. Browser comparisons, marker handling, and interpretation are original knowledge-allowed methodology. No reference establishes target-specific exposure, credential usability, or unauthorized impact.
---

# Sensitive values in URL query strings

## Applicability and prerequisites

Establish the query value's actual purpose, sensitivity, intended recipients, lifetime, and retention
policy. A token-like parameter name does not identify a secret. Public IDs, authorization codes,
access tokens, OTPs, and session credentials have different audiences and replay constraints.
Use owned accounts, a disposable browser profile, synthetic markers, and controlled destinations.
Access only authorized application/proxy logs; external network assessment does not authorize
reading target-local files or administration systems. Missing log access leaves that channel unknown.
Use browser/network capture, HTTP replay, and permitted log inspection or equivalents; preserve
configured HTTP/HTTPS proxies where supported. Resolve third-party recipient scope before testing.

## Accepted baselines and controls

Capture a normal assigned flow using synthetic test data and record the expected recipient and
successful legitimate result. Use a fresh profile or cleared test context and a marker-free request
as negative control. Give each variation a distinct marker so prior history, logs, and cached pages
cannot be mistaken for a new exposure. Confirm a controlled receiver can record an ordinary request
before interpreting an absent marker as refutation.

## Reconnaissance

1. From assigned captures record the request/redirect URL and which component places the value in
   the query. Separate query, fragment, request body, and header; fragments are not request query
   values even when browser scripts later read them.
2. Identify whether the value occurs in top-level navigation, a subresource, API-only traffic, or
   an intermediate redirect. An API request does not necessarily create browser navigation history.
3. Record browser version, actual Referrer-Policy response/header or element controls, redirect
   chain, destination origins, and relevant application cleanup such as history replacement.
4. Map possible observation channels to an actor: local profile user, approved log reader, same-origin
   receiver, or controlled cross-origin recipient. Distinguish expected server receipt from disclosure
   outside the intended audience. Record missing capabilities and unknown policy explicitly.

## Hypothesis generation

1. Use recorded evidence only; do not submit markers, navigate, inspect new logs, or redeem values.
2. Design one future history, log-retention, or referrer-propagation comparison at a time. Specify
   the excluded actor, destination, sensitive value class, and policy boundary being tested.
3. Plan marker-free controls, receiver availability checks, and actual outbound request capture.
   Separate same-origin from cross-origin navigation and subresources; do not assume a full URL
   travels in Referer. Plan permitted, prohibited, and inconclusive results for each channel.

## Vulnerability verification

1. Reproduce the normal assigned flow with synthetic data or a disposable test credential issued by
   that flow. Do not invent an extra parameter and call its appearance proof that the application
   leaks real credentials. Use distinct markers per test and avoid sending usable secrets externally.
2. For navigation history, inspect whether the value persists in the disposable profile after the
   relevant navigation, cleanup, logout, or account switch. Compare fresh context and marker-free
   controls. Address-bar visibility, a transient rendered page, and durable history are distinct.
3. For authorized logs, locate the correlated request by time/request ID and confirm the exact marker
   in the recorded URL. Identify retention and who can read it. Expected server-side processing is
   not automatically unauthorized disclosure; do not inspect unrelated records.
4. For referrer propagation, navigate or load only approved controlled destinations from the actual
   flow. Capture the outbound request and receiver evidence, including its Referer value and redirect
   chain. Compare permitted same-origin and cross-origin cases without silently weakening policy.
   An origin-only Referer does not disclose the query; no request is inconclusive without connectivity
   control. Record the effective behavior rather than relying on an assumed browser default.
5. Assess confidentiality and credential usability separately. A leaked one-use code can remain
   protected by client binding, PKCE, or expiry; a synthetic marker proves a channel, not account
   takeover. Any credential redemption requires its own authorized controlled test.

## Execution

1. Demonstrate only the verified exposure with one synthetic value and the policy-excluded test
   actor or controlled recipient. Do not collect real users' URLs or broaden recipient testing.
2. Invalidate disposable credentials, remove temporary receiver content, clear test-origin data and
   navigation history in the disposable profile, and delete test log records only if authorized.
   Otherwise record their retention for the owner; do not alter audit history without permission.
3. Confirm normal operation and receiver cleanup. Preserve any unconfirmed cleanup as a limitation.

## Analysis, evidence, and limitations

Retain value classification, intended audience/retention policy, synthetic marker provenance,
request and redirect timeline, browser/policy context, actual history/log/receiver result, controls,
and cleanup. Redact credential values from reports and shared records; preserve exact values only
where needed in sensitive internal evidence.

Classify each channel independently. HTTPS protects transport but does not by itself remove URLs
from every endpoint's local records. Referrer policy and browser behavior can prevent particular
propagation. Missing logs, blocked navigation, absent receiver evidence, and browser-only observations
do not establish all-channel safety. A query value alone neither proves unauthorized exposure nor
refutes a policy violation; establish who received it and why that receipt or retention is prohibited.

## Trigger examples and non-examples

Matching: an assigned redirect carries an authentication value in its query, or an assigned flow
needs evaluation of a sensitive URL's history/log/referrer exposure. Non-matching: a parameter named
key with no sensitive value, a token in an unrelated shared artifact, or a public resource identifier
without a confidentiality question. Choose only the current structured phase; recommendation does
not establish prerequisites or authorize a new recipient.
