---
schema_version: 1
title: Reflected, stored and DOM XSS with browser message trust
purpose: Distinguish reflected, stored and dom xss with browser message trust from legitimate behavior
  using bounded, policy-aware comparisons.
applicability:
- An assigned interaction reflects/stores untrusted content, routes client-controlled data into DOM behavior,
  or exchanges browser messages.
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
  when: An assigned interaction reflects/stores untrusted content, routes client-controlled data into
    DOM behavior, or exchanges browser messages. while checking prerequisites in an assigned interaction.
  request: Reflected, stored and DOM XSS with browser message trust reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: An assigned interaction reflects/stores untrusted content, routes client-controlled data into
    DOM behavior, or exchanges browser messages. while designing future tests from recorded observations.
  request: Reflected, stored and DOM XSS with browser message trust hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An assigned interaction reflects/stores untrusted content, routes client-controlled data into
    DOM behavior, or exchanges browser messages. while checking a specific assigned boundary hypothesis.
  request: Reflected, stored and DOM XSS with browser message trust vulnerability verification
  signals_any: []
- phases:
  - execution
  when: An assigned interaction reflects/stores untrusted content, routes client-controlled data into
    DOM behavior, or exchanges browser messages. after a boundary is verified and a minimal demonstration
    is authorized.
  request: Reflected, stored and DOM XSS with browser message trust execution
  signals_any: []
- phases:
  - analysis
  when: An assigned interaction reflects/stores untrusted content, routes client-controlled data into
    DOM behavior, or exchanges browser messages. while interpreting recorded results and competing explanations.
  request: Reflected, stored and DOM XSS with browser message trust analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/DOM_based_XSS_Prevention_Cheat_Sheet.md
  title: DOM based XSS Prevention Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.392188+00:00'
  selection_reason: DOM sinks, reflected/stored execution contexts and cross-origin messaging.
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.md
  title: Cross Site Scripting Prevention Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.388027+00:00'
  selection_reason: DOM sinks, reflected/stored execution contexts and cross-origin messaging.
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/HTML5_Security_Cheat_Sheet.md
  title: HTML5 Security Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.387366+00:00'
  selection_reason: DOM sinks, reflected/stored execution contexts and cross-origin messaging.
source_coverage: 'Independently selected retrieved references supply mechanism/control background. DOM
  sinks, reflected/stored execution contexts and cross-origin messaging. Limits: Development-oriented
  guidance supplies mechanism/control background; application policy, live exploitability, negative controls
  and minimal-impact procedure remain original methodology requiring target-specific verification. DOM
  sinks, reflected/stored execution contexts and cross-origin messaging. Limits: Development-oriented
  guidance supplies mechanism/control background; application policy, live exploitability, negative controls
  and minimal-impact procedure remain original methodology requiring target-specific verification. DOM
  sinks, reflected/stored execution contexts and cross-origin messaging. Limits: Development-oriented
  guidance supplies mechanism/control background; application policy, live exploitability, negative controls
  and minimal-impact procedure remain original methodology requiring target-specific verification. Numbered
  procedures and controls are original knowledge-allowed synthesis, not individually source-verified.
  No completed target execution or affected-version claim is supplied.'
---

# Reflected, stored and DOM XSS with browser message trust

## Applicability and prerequisites

An assigned interaction reflects/stores untrusted content, routes client-controlled data into DOM behavior, or exchanges browser messages. Use owned browser sessions and test content with authorization to render a harmless marker. Establish the origin and permissions affected, relevant rendering context and expected trust for message senders/receivers. Browser execution/DOM inspection and raw-response capture or equivalents are needed; raw HTTP alone cannot confirm browser script execution. Avoid real-user interaction and credential/data exfiltration.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. Trace the assigned value from request, stored field, URL fragment, storage or message event to raw HTML, attribute, script, URL or DOM sink. Note encoding/decoding stages and whether the client or server introduces markup; sink names alone do not establish exploitability.
2. For messages identify the expected sender origin/window, recipient origin and data schema. A wildcard targetOrigin or missing-looking origin check is a lead; actual receipt and a prohibited action/data disclosure must still be demonstrated.

## Hypothesis generation

1. From recorded observations design distinct reflected, stored and DOM paths, preserving their source and sink contexts. Propose a harmless browser marker such as setting a dedicated test attribute and a context-escaped inert counterpart; no test content is sent in this context.
2. For stored content specify the future write, later independent owned-viewer render and cleanup, distinguishing storage from execution. For DOM cases specify the source mutation and the browser-visible sink before/after.
3. For inbound messages plan allowed-origin/source/schema controls and one controlled untrusted-origin sender; for outbound messages plan a controlled receiver and expected nonsecret test datum. Identify the forbidden trust effect instead of assuming every cross-origin message is wrong.

## Vulnerability verification

1. Render the benign literal in an isolated owned browser and capture raw response plus final DOM. Verify the test marker is absent, recording CSP enforcement, sandbox context, browser version and any application scripts that could generate the same marker.
2. For reflected XSS submit the smallest context-appropriate harmless script-capable input and its escaped/inert form. Require actual browser execution in the relevant origin, independently observable through the dedicated marker; reflection, HTML injection or a console parsing error alone is insufficient.
3. For stored XSS create one private test record, then load it in a separate owned viewer session without replaying the input. Correlate the stored value to execution, compare an inert stored value and identify which actor/origin receives the effect. Self-only execution may have a narrower impact than cross-user execution.
4. For DOM XSS change one client-controlled source while holding the server response and identity stable where possible. Trace its arrival at the executable sink and compare safe text/escaped input. Browser-only evaluation is DOM execution, not evidence of server-side injection.
5. For inbound messaging, send one valid-shaped test message from an approved controlled untrusted origin/window. Compare the intended trusted sender, a wrong schema and an unrelated window where source binding is required. Demonstrate a forbidden DOM action, privilege action or data effect; message receipt alone is not a violation.
6. For outbound messaging, use only an owned controlled receiver and nonsecret fixture. Verify actual delivery of data to a receiver origin prohibited by policy, including any navigation condition. Wildcard syntax alone cannot prove disclosure. Repeat a benign render/message after each branch.

## Execution

1. Demonstrate the confirmed branch with one local DOM marker or one synthetic nonsecret message/action. Do not read cookies, export tokens, invoke real-user actions or publish persistent payloads to shared audiences.
2. Restore or delete stored content through its normal interface, close test windows/listeners, clear only test-owned browser state and re-render the benign value. Check that asynchronous pages/previews no longer execute the marker.

## Analysis

1. Separate reflection, HTML injection, executable script, origin/privilege context and message trust failure. CSP may block a candidate; distinguish a blocked payload from a demonstrated alternate executable path.
2. Explain intentional rich text, sandboxed opaque origins, browser extensions, existing XSS, inherited same-origin trust and authorized message partners. A receiver may legitimately accept public data cross-origin; policy plus forbidden effect is required.

## Evidence requirements

Retain raw input/response, source-to-sink trace, browser execution marker, actual origin/frame context, CSP behavior, inert controls, owned author/viewer labels and stored cleanup result. Message evidence includes origin/source/schema comparisons and actual prohibited effect or delivery.

## Limitations

No exhaustive browser/payload/CSP bypass coverage is claimed. Missing browser execution capability, a reachable sink or an independent viewer leaves the relevant branch incomplete. Source-code access is optional; browser observations may establish only a partial data flow.

## Trigger examples and non-examples

Matching: An assigned interaction reflects/stores untrusted content, routes client-controlled data into DOM behavior, or exchanges browser messages. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
