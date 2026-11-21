---
schema_version: 1
title: Cross-site request forgery
purpose: Distinguish cross-site request forgery from legitimate behavior using bounded, policy-aware comparisons.
applicability:
- An assigned browser workflow changes state using credentials the browser may attach automatically.
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
  when: An assigned browser workflow changes state using credentials the browser may attach automatically.
    while checking prerequisites in an assigned interaction.
  request: Cross-site request forgery reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: An assigned browser workflow changes state using credentials the browser may attach automatically.
    while designing future tests from recorded observations.
  request: Cross-site request forgery hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An assigned browser workflow changes state using credentials the browser may attach automatically.
    while checking a specific assigned boundary hypothesis.
  request: Cross-site request forgery vulnerability verification
  signals_any: []
- phases:
  - execution
  when: An assigned browser workflow changes state using credentials the browser may attach automatically.
    after a boundary is verified and a minimal demonstration is authorized.
  request: Cross-site request forgery execution
  signals_any: []
- phases:
  - analysis
  when: An assigned browser workflow changes state using credentials the browser may attach automatically.
    while interpreting recorded results and competing explanations.
  request: Cross-site request forgery analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.md
  title: Cross-Site Request Forgery Prevention Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.461856+00:00'
  selection_reason: browser credential attachment and request-origin controls.
source_coverage: 'Independently selected retrieved references supply mechanism/control background. browser
  credential attachment and request-origin controls. Limits: Development-oriented guidance supplies mechanism/control
  background; application policy, live exploitability, negative controls and minimal-impact procedure
  remain original methodology requiring target-specific verification. Numbered procedures and controls
  are original knowledge-allowed synthesis, not individually source-verified. No completed target execution
  or affected-version claim is supplied.'
---

# Cross-site request forgery

## Applicability and prerequisites

An assigned browser workflow changes state using credentials the browser may attach automatically. Establish the action's intended user intent/authorization policy and use an owned account with a harmless reversible setting. Know target and attacker-page origins/sites and whether the action relies on cookies or an explicitly supplied bearer credential. Use a real browser and capture capability; nonbrowser replay can probe token handling but cannot by itself prove browser exploitability.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. Inspect the assigned state-changing request for method, content type, credential transport, anti-CSRF value, Origin/Referer checks and relevant cookie attributes. An absent token is not proof; other controls or explicit user credentials may prevent forgery.
2. Identify a browser-reproducible cross-origin trigger and actual state read-back. Cross-origin and cross-site differ; record the relationship relevant to SameSite cookies rather than equating any second origin with a cross-site attacker.

## Hypothesis generation

1. Design a future normal in-site action and an attacker-page action that changes only one reversible test field. Specify how credentials would be attached without the attacker learning them, and how actual server state will be read independently.
2. Plan omitted/invalid/other-owned-session token comparisons and origin/referrer controls without assuming each missing input must be exploitable. For JSON/custom headers, include the browser preflight and whether an attacker can send an equivalent simple request.
3. For login CSRF, design an owned attacker-account login from a clean victim test browser and a final identity check; do not require an existing victim session for that branch. No live actions or forged pages are exercised during hypothesis design.

## Vulnerability verification

1. Perform the legitimate action in an owned browser session, verify the reversible field through a fresh read and restore it. Capture credentials actually used, token/session binding and request context.
2. With a nonbrowser client if useful, vary only token presence/value or Origin/Referer to identify a candidate acceptance condition. Use correct-session and unrelated-session controls. Label this server-side acceptance only until reproduced through the browser.
3. Serve a minimal attacker page from the approved controlled origin using a form, navigation or fetch appropriate to the actual method/content type. Do not spoof browser-forbidden headers or insert a secret token the attacker would not know. Observe preflight, redirects and actual cookie attachment.
4. Trigger the harmless test action once in the owned browser. Verify committed server state through a separate normal read, not just a success page or outbound request. Compare an invalid-token/incompatible-origin control and logged-out or credentialless behavior as relevant.
5. For login CSRF confirm whether the test browser acquired the attacker's owned identity and whether the application's login-intent policy forbids that flow. Account switching initiated normally is not the same test. Repeat a normal action and restore state after each successful variant.

## Execution

1. Demonstrate one unauthorized cross-site change to a harmless owned setting, or one owned-account login-intent effect, under realistic browser credential and request restrictions. Avoid email/password changes, purchases or notifications to others.
2. Restore the setting and original login context, remove the attacker page and end temporary sessions. Confirm that no queued request, test form or browser tab will repeat the mutation.

## Analysis

1. Separate a missing defense indicator, nonbrowser acceptance, browser request dispatch and committed unauthorized state. An attacker need not read the response for CSRF; inability to read it is not a refutation.
2. Explain SameSite/site relationships, preflight enforcement, explicit bearer headers, origin/referrer validation, one-time tokens, user interaction and intentional public actions. CORS governs response access and does not itself establish CSRF protection.

## Evidence requirements

Retain legitimate and forged browser requests, origin/site relationship, actual cookie/credential attachment, token provenance, preflight/redirect behavior, fresh state read-back and negative controls. Keep tokens internal and document cleanup.

## Limitations

No browser-control bypass, XSS-assisted forgery or destructive action is supplied. Missing realistic attacker-page conditions or state verification leaves exploitability inconclusive. Testing one route/method says nothing about alternate workflow routes.

## Trigger examples and non-examples

Matching: An assigned browser workflow changes state using credentials the browser may attach automatically. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
