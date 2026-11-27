---
schema_version: 1
title: Reflected, stored and DOM XSS with browser message trust
purpose: Distinguish reflected, stored and DOM XSS and browser message trust failures using source-to-sink
  tracing, explicit hypotheses and controlled browser execution evidence.
applicability:
- An assigned interaction reflects/stores untrusted content, routes client-controlled data into DOM behavior,
  or exchanges browser messages.
- Client-side query or fragment parsing, including URLSearchParams and SPA routing, may carry untrusted
  data to HTML, JavaScript, attribute or script-URL sinks.
phases: &id001
- reconnaissance
- hypothesis_generation
- vulnerability_verification
- execution
- analysis
triggers:
- phases:
  - reconnaissance
  when: An assigned interaction reflects/stores untrusted content, routes client-controlled data into
    DOM behavior, or exchanges browser messages while checking prerequisites in an assigned interaction.
  request: Reflected, stored and DOM XSS with browser message trust reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: An assigned interaction reflects/stores untrusted content, routes client-controlled data into
    DOM behavior, or exchanges browser messages while designing future tests from recorded observations.
  request: Reflected, stored and DOM XSS with browser message trust hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An assigned interaction reflects/stores untrusted content, routes client-controlled data into
    DOM behavior, or exchanges browser messages while checking a specific assigned boundary hypothesis.
  request: Reflected, stored and DOM XSS with browser message trust vulnerability verification
  signals_any: []
- phases:
  - execution
  when: An assigned interaction reflects/stores untrusted content, routes client-controlled data into
    DOM behavior, or exchanges browser messages after a boundary is verified and a minimal demonstration
    is authorized.
  request: Reflected, stored and DOM XSS with browser message trust execution
  signals_any: []
- phases:
  - analysis
  when: An assigned interaction reflects/stores untrusted content, routes client-controlled data into
    DOM behavior, or exchanges browser messages while interpreting recorded results and competing explanations.
  request: Reflected, stored and DOM XSS with browser message trust analysis
  signals_any: []
- phases: *id001
  when: An assigned page reads query parameters or fragments through URLSearchParams, a router or other
    client-side code and may propagate them to a DOM or code-execution sink.
  request: DOM XSS source-to-sink tracing and SPA query-parameter hypothesis design, decoding, sink-specific
    controls and browser execution evidence
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
- url: https://portswigger.net/web-security/cross-site-scripting/dom-based
  title: DOM-based XSS
  retrieved_at: '2026-11-27'
  selection_reason: Rendered DOM inspection and debugger tracing for HTML and JavaScript sinks; sink-specific
    execution behavior.
- url: https://developer.mozilla.org/en-US/docs/Web/API/URLSearchParams
  title: URLSearchParams
  retrieved_at: '2026-11-27'
  selection_reason: Query parsing percent-decodes values and converts plus signs to spaces; serialization
    is distinct.
- url: https://developer.mozilla.org/en-US/docs/Web/API/Element/innerHTML
  title: 'Element: innerHTML property'
  retrieved_at: '2026-11-27'
  selection_reason: HTML parsing and non-execution of injected script elements; other executable constructs
    remain possible.
- url: https://developer.mozilla.org/en-US/docs/Web/API/Trusted_Types_API
  title: Trusted Types API
  retrieved_at: '2026-11-27'
  selection_reason: Typed sink enforcement, policy creation restrictions and application-defined transformations
    including default policies.
- url: https://cheatsheetseries.owasp.org/cheatsheets/DOM_based_XSS_Prevention_Cheat_Sheet.html
  title: DOM based XSS Prevention Cheat Sheet
  retrieved_at: '2026-11-27'
  selection_reason: Client-side processing versus reflected/stored delivery and context-specific attribute
    and code semantics.
active: true
source_coverage: Retained original OWASP references provide reflected/stored XSS and browser messaging
  background. Independently selected references retrieved on 2026-11-27 add DOM tracing, URLSearchParams
  parsing, innerHTML semantics, attribute context and Trusted Types background. PortSwigger URL-encoding
  observations require qualification by the actual parser and later decoding. API documentation does not
  establish target reachability, sanitizer correctness or exploitability. Procedures, controls and interpretation
  are original knowledge-allowed synthesis, not individually source-verified. No source-required version,
  quotation or target-execution obligation is asserted.
---

# Reflected, stored and DOM XSS with browser message trust

## Applicability and prerequisites

An assigned interaction reflects/stores untrusted content, routes client-controlled data into DOM behavior, or exchanges browser messages. Use owned browser sessions and test content with authorization to render a harmless marker. Establish the origin and permissions affected, relevant rendering context and expected trust for message senders/receivers. Browser execution/DOM inspection and raw-response capture or equivalents are needed; raw HTTP alone cannot confirm browser script execution. Avoid real-user interaction and credential/data exfiltration.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. Trace the assigned value from request, stored field, URL fragment, storage or message event to raw HTML, attribute, script, URL or DOM sink. Note encoding/decoding stages and whether the client or server introduces markup; sink names alone do not establish exploitability.
2. For DOM paths record the source read, parameter/key, trigger (initial load, navigation, event or asynchronous update), transformations, sink argument, containing element and origin/frame. Search delivered scripts and use debugger breakpoints where available; HTML sinks may be visible in the rendered DOM, while JavaScript evaluation need not leave a DOM value. Static sink names and source searches are leads until connected by a reachable data flow. Inspect only source available through authorized access.
3. For query parsing distinguish raw URL text, `location.search`, the value returned by `URLSearchParams.get`, and subsequent application decoding. Parsing percent-decodes values and interprets `+` as a space; do not assume percent-encoding is sanitization. Record duplicate-key selection and router normalization when relevant. Compare serialized and parsed values without silently decoding evidence again.
4. Identify the actual operation: parsing an HTML string, assigning an attribute/property, evaluating code, or loading a script URL. Direct assignment to an ordinary attribute does not parse quote delimiters into new attributes; event handlers, script URLs and HTML-valued attributes have their own semantics. A template literal alone does not evaluate interpolated data as code. Framework name alone does not establish automatic escaping or a bypass.
5. For messages identify the expected sender origin/window, recipient origin and data schema. A wildcard targetOrigin or missing-looking origin check is a lead; actual receipt and a prohibited action/data disclosure must still be demonstrated.

## Hypothesis generation

1. From recorded observations design distinct reflected, stored and DOM paths, preserving their source and sink contexts. Propose a harmless browser marker such as setting a dedicated test attribute and a context-escaped inert counterpart; no test content is sent in this context.
2. For stored content specify the future write, later independent owned-viewer render and cleanup, distinguishing storage from execution. For DOM cases specify the source mutation, observed or suspected sink and exact future trigger. A DOM-rendered value is not required for a JavaScript-evaluating sink.
3. For inbound messages plan allowed-origin/source/schema controls and one controlled untrusted-origin sender; for outbound messages plan a controlled receiver and expected nonsecret test datum. Identify the forbidden trust effect instead of assuming every cross-origin message is wrong.

### DOM hypothesis design

1. Use recorded observations to select a plausible path; do not emit every sink class merely because it exists. Unknown implementation conditions become future checks. SPA architecture is optional, and reflected/stored delivery can coexist with unsafe client-side processing. A static server response or marker absence from raw HTML is not a prerequisite.
2. Record each proposed hypothesis as JSON using the existing operation output schema when one is supplied. The illustrative fields below describe methodology only; they are not a new tool contract or completed evidence:

```json
{
  "hypothesis": "A query-derived value may reach an HTML-parsing sink",
  "observations": ["Recorded client code reads a query value"],
  "prerequisites": [{"condition": "Value reaches a reachable HTML sink", "status": "unknown"}],
  "assumptions": ["The suspected rendering path handles the selected value"],
  "unknowns": ["Transformations, sink context, trigger and effective browser policy"],
  "future_checks": ["Trace parsed value to the sink argument at its actual trigger"],
  "baseline": "Benign literal with no execution marker",
  "variation": "One context-appropriate harmless execution probe through the same source",
  "control": "Inert counterpart through the unchanged application path",
  "support": "Reproducible application-driven marker execution in the recorded origin/frame",
  "refutation": "Traced path terminates as ordinary text with no later executable use",
  "inconclusive": "Unreachable trigger, incomplete trace or unexplained marker absence",
  "evidence": ["Input, transformations, sink argument, trigger, policy and browser effect"]
}
```

3. For an HTML-parser hypothesis plan literal mapping, an inert markup check, then a sink-compatible execution check only when verification is authorized. New markup such as a bold element establishes HTML interpretation, not XSS. Script elements inserted through `innerHTML` do not execute; their failure does not exclude other executable constructs.
4. For an attribute hypothesis name the element, attribute and assignment operation. Ordinary fixed-name data attributes do not gain a quote-breakout path from direct assignment. For an executable event-handler, HTML-valued attribute or script-URL path, specify the relevant trigger and parsing/URL rules; a changed link alone is not execution.
5. For a code-evaluation hypothesis identify the actual evaluating operation and inspect its argument. A string-based timer differs from a function callback; interpolation into an ordinary string differs from evaluation. Plan an execution effect that requires no visible DOM reflection at the sink.

## Vulnerability verification

1. Render the benign literal in an isolated owned browser and capture raw response plus final DOM. Verify the test marker is absent, recording CSP enforcement, sandbox context, browser version and any application scripts that could generate the same marker.
2. For reflected XSS submit the smallest context-appropriate harmless script-capable input and its escaped/inert form. Require actual browser execution in the relevant origin, independently observable through the dedicated marker; reflection, HTML injection or a console parsing error alone is insufficient.
3. For stored XSS create one private test record, then load it in a separate owned viewer session without replaying the input. Correlate the stored value to execution, compare an inert stored value and identify which actor/origin receives the effect. Self-only execution may have a narrower impact than cross-user execution.
4. For DOM XSS follow the detailed comparison below. Keep identity, route, browser policy and surrounding state stable where possible; retain response changes rather than requiring a static response. Client-side execution does not establish server-side injection.
5. For inbound messaging, send one valid-shaped test message from an approved controlled untrusted origin/window. Compare the intended trusted sender, a wrong schema and an unrelated window where source binding is required. Demonstrate a forbidden DOM action, privilege action or data effect; message receipt alone is not a violation.
6. For outbound messaging, use only an owned controlled receiver and nonsecret fixture. Verify actual delivery of data to a receiver origin prohibited by policy, including any navigation condition. Wildcard syntax alone cannot prove disclosure. Repeat a benign render/message after each branch.

### Controlled DOM verification

1. In a fresh owned context load a benign literal and confirm the execution marker is absent. Retain the raw response, rendered DOM where relevant, browser version, exact URL, frame/origin and required trigger actions. Record a build identifier when observable; otherwise retain delivered script URLs/content fingerprints and mark the build unknown. Missing build metadata does not authorize local target filesystem inspection or prevent a controlled comparison. Record authenticated identity labels without exporting secrets.
2. Vary one assigned source. Trace the raw value, parsed value, intervening transforms and argument immediately before the actual sink. Use breakpoints or equivalent observation without replacing the application's source value or sink behavior. Observe initial load and the specific navigation/event/asynchronous trigger relevant to the hypothesis; do not invent unobserved routes.
3. For HTML-parsing paths first distinguish text from HTML interpretation with inert content. For JavaScript evaluators inspect the argument and use an appropriate inert expression/data control; for executable attributes and script URLs establish their actual trigger. Then submit one minimal sink-compatible execution probe through the application input, within scope. Its only effect should set a fresh dedicated test attribute or equivalent nonsecret local marker. An illustrative JavaScript effect is `document.documentElement.setAttribute("data-domx-proof", "fresh-test-nonce")`; this is the effect to embed in a context-appropriate probe, not an instruction to execute it manually as proof. Choose a nonce not present in input-independent page state and retain how the marker was read.
4. Compare a benign baseline, the execution candidate and an inert counterpart through the same unchanged application path. Use equivalent fresh owned contexts or a recorded reset of test-owned input and marker state between runs; confirm marker absence before each run while preserving comparable authentication and required application state. Preserve encoding, parser context and trigger differences. A separate page modified to use `textContent` is a useful local illustration, not a target negative control. Do not disable CSP, replace sanitizers or alter target scripts to obtain confirmation.
5. Require the marker to be caused by JavaScript executing through the submitted value's reachable application path. An attribute visible in a DOM snapshot is insufficient: markup parsing or existing application behavior could reproduce it. Retain execution evidence such as an execution breakpoint/call stack connected to the submitted source, together with a fresh effect whose provenance excludes mere HTML parsing. Observation instrumentation must not create the effect. Console evaluation, automation evaluation or direct DevTools DOM edits can inspect state but their own execution effects cannot prove XSS. Correlate source, sink argument, trigger and effect; confirm the baseline and inert control lack that effect. Record the actual origin/frame and required user interaction, including whether another owned viewer can receive the input through a realistic delivery path. Self-only manipulation has narrower demonstrated impact.
6. Record enforced CSP separately from report-only messages, and capture actual blocking or execution behavior. Trusted Types API availability, `trusted-types` policy-name restrictions and `require-trusted-types-for` enforcement are distinct. Inspect the actual transformation/default policy when observable; a typed value does not independently establish safe sanitization. Preserve unknowns if policy internals are unavailable.
7. Repeat the benign render after the comparison and clean up test-owned URL, storage, records and listeners through normal interfaces. Do not erase unrelated browser state. Stop after the minimal confirmed effect; leave broader payload families or chaining as separate assigned work.

## Execution

1. Demonstrate the confirmed branch with one local DOM marker or one synthetic nonsecret message/action. Do not read cookies, export tokens, invoke real-user actions or publish persistent payloads to shared audiences.
2. Restore or delete stored content through its normal interface, close test windows/listeners, clear only test-owned browser state and re-render the benign value. Check that asynchronous pages/previews no longer execute the marker.

## Analysis

1. Separate reflection, HTML injection, executable script, origin/privilege context and message trust failure. CSP may block a candidate; distinguish a blocked payload from a demonstrated alternate executable path.
2. Classify each tested path separately: confirmed application-driven execution; HTML injection only; candidate blocked by an observed control; path refuted by a complete trace; or inconclusive. Marker absence, a timeout, an HTTP rejection or a single blocked payload is not a general refutation. A sanitizer call is not proof of effective sanitization; inspect its output and later consumers. A value ending in ordinary text refutes that traced path only if no later executable use is present in the inspected scope. Uninspected branches remain unknown.
3. Explain intentional rich text, sandboxed opaque origins, browser extensions, existing XSS, inherited same-origin trust and authorized message partners. A receiver may legitimately accept public data cross-origin; policy plus forbidden effect is required.

## Evidence requirements

Retain raw input/response, source-to-sink trace including parsed values and transformations, sink argument and operation, exact trigger, browser execution marker, actual origin/frame context, enforced versus report-only CSP and Trusted Types observations, baseline/inert controls, owned author/viewer labels and cleanup result. Separate application execution from instrumentation effects and retain artifact references for each conclusion. Message evidence includes origin/source/schema comparisons and actual prohibited effect or delivery.

## Limitations

No exhaustive browser/payload/CSP bypass coverage is claimed. Missing browser execution capability, a reachable sink or an independent viewer leaves the relevant branch incomplete. Source-code access is optional; browser observations may establish only a partial data flow.

## Trigger examples and non-examples

Matching DOM examples include query parsing through URLSearchParams into a suspected HTML sink, a fragment consumed after client-side navigation, or a stored value later passed to a JavaScript sink. A plain query parameter with no observed relevant processing is insufficient by itself. Matching: An assigned interaction reflects/stores untrusted content, routes client-controlled data into DOM behavior, or exchanges browser messages. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
