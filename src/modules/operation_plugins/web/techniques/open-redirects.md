---
schema_version: 1
title: Open redirect destination control
purpose: Distinguish open redirect destination control from legitimate behavior using bounded, policy-aware
  comparisons.
applicability:
- An assigned redirect, return URL or browser navigation input selects a destination.
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
  when: An assigned redirect, return URL or browser navigation input selects a destination. while checking
    prerequisites in an assigned interaction.
  request: Open redirect destination control reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: An assigned redirect, return URL or browser navigation input selects a destination. while designing
    future tests from recorded observations.
  request: Open redirect destination control hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An assigned redirect, return URL or browser navigation input selects a destination. while checking
    a specific assigned boundary hypothesis.
  request: Open redirect destination control vulnerability verification
  signals_any: []
- phases:
  - execution
  when: An assigned redirect, return URL or browser navigation input selects a destination. after a boundary
    is verified and a minimal demonstration is authorized.
  request: Open redirect destination control execution
  signals_any: []
- phases:
  - analysis
  when: An assigned redirect, return URL or browser navigation input selects a destination. while interpreting
    recorded results and competing explanations.
  request: Open redirect destination control analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Unvalidated_Redirects_and_Forwards_Cheat_Sheet.md
  title: Unvalidated Redirects and Forwards Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.392222+00:00'
  selection_reason: redirect destination validation.
source_coverage: 'Independently selected retrieved references supply mechanism/control background. redirect
  destination validation. Limits: Development-oriented guidance supplies mechanism/control background;
  application policy, live exploitability, negative controls and minimal-impact procedure remain original
  methodology requiring target-specific verification. Numbered procedures and controls are original knowledge-allowed
  synthesis, not individually source-verified. No completed target execution or affected-version claim
  is supplied.'
---

# Open redirect destination control

## Applicability and prerequisites

An assigned redirect, return URL or browser navigation input selects a destination. Establish the allowed destination policy and whether the feature intentionally supports arbitrary external navigation. Use controlled harmless destination URLs and stop before following to any unapproved location. Capture HTTP redirect chains and browser navigation where relevant; equivalent tooling is acceptable. Authentication codes, tokens and user data must not be sent to the receiver.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. Inspect the assigned return/next/location input and identify server-side Location redirects, client-side navigation or displayed links. A string echoed in a page or error does not establish navigation.
2. Record expected same-origin/path/partner restrictions, canonical scheme/host/port and any confirmation page. Identify whether a security workflow relies on this redirect rather than assuming phishing or token disclosure from an external link.

## Hypothesis generation

1. From recorded navigation design an allowed baseline and one controlled disallowed destination, varying only the destination interpretation. Include a nonexistent/inert input and a similarly shaped allowed destination as controls.
2. Plan a small context-justified set of URL representations such as absolute versus network-path reference, encoded delimiter or userinfo ambiguity. Define the final browser-parsed origin; do not assume a substring or visual hostname is the authority.
3. Specify whether the outcome is an HTTP redirect, a JavaScript navigation or a legitimate external link. No requests or browser navigation occur in hypothesis generation, and an unapproved token-leak chain remains outside this test.

## Vulnerability verification

1. Replay the normal allowed destination and capture the response without automatically following redirects. In browser-dependent flows observe the actual final origin and any user confirmation.
2. Replace only the destination with an approved controlled disallowed origin/path. Check the parsed Location or actual navigation, not the presence of the input in an error body. If arbitrary external redirects are intentionally allowed, establish a different violated security contract before treating the behavior as a flaw.
3. Compare the allowed destination, an invalid destination and the candidate. If a parser-boundary hypothesis exists, test one representation at a time: for example an approved network-path reference versus its absolute form. Keep the callback harmless and avoid unexpected protocols.
4. Resolve any relative Location against the effective response URL and observe each permitted hop. Server-side fetching is an SSRF question, while an anchor merely offered to a user may be a link-validation question. Do not follow to unapproved hosts or allow actual authentication artifacts into the test URL.
5. Re-run the baseline and classify whether the application reproducibly navigates to a destination prohibited by its policy. Demonstrate only that destination-control boundary unless a separately scoped consumer workflow supplies further impact evidence.

## Execution

1. Show one controlled navigation to a harmless test page identifying the destination origin, without collecting tokens, credentials or other users. For confirmation flows record the actual interaction required and whether policy intentionally permits it.
2. Remove test callback content and any stored redirect preference, restore the normal return path and close test browser sessions. Verify ordinary navigation no longer targets the test receiver.

## Analysis

1. Separate input reflection, link creation, redirect emission and actual navigation. A changed status or echoed URL alone confirms none of the latter outcomes.
2. Explain intentional redirectors, allowlisted partners, relative URL resolution, browser normalization, confirmation UI and intermediate trusted hops. A confirmed open redirect does not itself prove account takeover or code/token leakage; those need their own complete controlled flow.

## Evidence requirements

Preserve allowed/invalid/candidate request and response headers, destination policy, resolved scheme-host-port/path, permitted redirect chain or browser navigation, control outcomes and cleanup. Redact sensitive query values and avoid collecting them in callback logs.

## Limitations

No exhaustive URL-parser or OAuth chain testing is provided. Unknown destination policy may limit the result to observed external navigation. Third-party hops and unapproved protocols remain untested.

## Trigger examples and non-examples

Matching: An assigned redirect, return URL or browser navigation input selects a destination. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
