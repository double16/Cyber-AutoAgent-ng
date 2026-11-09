---
schema_version: 1
title: Clickjacking and actionable frame embedding
purpose: Distinguish clickjacking and actionable frame embedding from legitimate behavior using bounded,
  policy-aware comparisons.
applicability:
- An assigned sensitive browser page may be embedded by another origin or used in a UI-redress workflow.
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
  when: An assigned sensitive browser page may be embedded by another origin or used in a UI-redress workflow.
    while checking prerequisites in an assigned interaction.
  request: Clickjacking and actionable frame embedding reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: An assigned sensitive browser page may be embedded by another origin or used in a UI-redress workflow.
    while designing future tests from recorded observations.
  request: Clickjacking and actionable frame embedding hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An assigned sensitive browser page may be embedded by another origin or used in a UI-redress workflow.
    while checking a specific assigned boundary hypothesis.
  request: Clickjacking and actionable frame embedding vulnerability verification
  signals_any: []
- phases:
  - execution
  when: An assigned sensitive browser page may be embedded by another origin or used in a UI-redress workflow.
    after a boundary is verified and a minimal demonstration is authorized.
  request: Clickjacking and actionable frame embedding execution
  signals_any: []
- phases:
  - analysis
  when: An assigned sensitive browser page may be embedded by another origin or used in a UI-redress workflow.
    while interpreting recorded results and competing explanations.
  request: Clickjacking and actionable frame embedding analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Clickjacking_Defense_Cheat_Sheet.md
  title: Clickjacking Defense Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.387949+00:00'
  selection_reason: frame policy and UI redress.
source_coverage: 'Independently selected retrieved references supply mechanism/control background. frame
  policy and UI redress. Limits: Development-oriented guidance supplies mechanism/control background;
  application policy, live exploitability, negative controls and minimal-impact procedure remain original
  methodology requiring target-specific verification. Numbered procedures and controls are original knowledge-allowed
  synthesis, not individually source-verified. No completed target execution or affected-version claim
  is supplied.'
---

# Clickjacking and actionable frame embedding

## Applicability and prerequisites

An assigned sensitive browser page may be embedded by another origin or used in a UI-redress workflow. Establish which action is sensitive and which framing origins are permitted. Use an owned authenticated account, a controlled framing page and a harmless reversible action. A real browser, response-header capture and frame inspection are needed; header inspection alone cannot demonstrate actionable UI redress. Do not involve unsuspecting users.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. Inspect the assigned page's enforced frame-ancestors/X-Frame-Options headers and actual frame behavior, including ancestor chains where relevant. Report-only policy and meta-tag strings do not establish browser enforcement.
2. Identify whether the sensitive control can render and remain authenticated inside the candidate frame. Cookie policy, reauthentication, UI layout and intended partner embedding may prevent or legitimately permit use; missing headers alone are not a vulnerability.

## Hypothesis generation

1. From recorded content propose a controlled untrusted-origin frame test with a visible alignment of one harmless control. Define the trusted-frame or top-level baseline, the prohibited framing policy and the server state expected if a click lands.
2. Plan to distinguish blank/blocked framing, unauthenticated content, passive public display and an actionable authenticated control. A public marketing page framed successfully is a non-example unless an independent prohibited effect exists.
3. Specify a reversible owned-account action and cleanup; keep the overlay initially visible so the tester verifies alignment without deception. Do not open frames or click controls during hypothesis generation.

## Vulnerability verification

1. Load the page top-level in an owned browser session and exercise the reversible action normally, then restore it. Record the browser's authentication and reauthentication requirements.
2. Embed the exact page from the approved controlled untrusted origin. Observe rendered content, actual cookies/session and enforced browser policy; compare an intended trusted embedding origin if available. Do not infer success from an HTTP200 fetched outside the browser.
3. With transparent test overlays removed or clearly visible first, align the chosen control and make one tester-controlled click. Verify whether the framed page receives it and commits the harmless state change through an independent normal read.
4. Compare a blocked frame, logged-out frame or frame with the control misaligned/inert as relevant. If reauthentication, confirmation or cookie exclusion prevents the sensitive effect, record that result rather than claiming clickjacking from visual embedding alone.
5. Repeat the normal top-level baseline and restore state. A confirmed actionable UI-redress claim requires a prohibited framing context and demonstrated unintended control activation under realistic browser conditions, not just an absent header.

## Execution

1. Produce a minimal local demonstration with the owned account and one reversible setting, showing framing context and click target visibly. Do not use social engineering, third-party users or irreversible transactions.
2. Restore the setting, remove the controlled framing/overlay page, close test frames and verify top-level behavior. If temporary frame-policy configuration was changed in a test environment, restore it through the owner and record that the result depended on the change.

## Analysis

1. Separate permissive embedding, authenticated framing and demonstrated sensitive action. Legitimate partner framing or a nonsensitive public page may be expected behavior.
2. Explain enforced versus report-only policy, nested ancestor restrictions, browser differences, cookie site rules, frame-busting scripts and confirmation steps. A single unsuccessful overlay does not prove all UI actions are safe; a header name alone does not prove browser protection.

## Evidence requirements

Retain final response headers, browser/version/origin and ancestor context, actual authenticated frame display, controlled alignment/click evidence, state read-back and comparison outcomes. Include exact harmless action and restoration, not just a screenshot of an iframe.

## Limitations

No unsuspecting-user deception, browser-policy bypass or exhaustive UI automation is included. Missing a safe reversible action or browser evidence limits conclusions to framing posture. Newer window-based UI attacks require distinct prerequisites and are not inferred from this frame method.

## Trigger examples and non-examples

Matching: An assigned sensitive browser page may be embedded by another origin or used in a UI-redress workflow. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
