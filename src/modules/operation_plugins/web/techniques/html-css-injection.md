---
schema_version: 1
title: HTML and CSS injection
purpose: Distinguish unsafe HTML or CSS interpretation from inert text reflection and assess the bounded browser effect.
applicability:
  - An assigned input is reflected or stored into browser-rendered HTML or CSS.
phases: [reconnaissance, hypothesis_generation, vulnerability_verification, execution, analysis]
active: true
triggers:
  - phases: [reconnaissance, hypothesis_generation, vulnerability_verification, execution, analysis]
    when: An assigned value reaches an HTML, attribute, URL, or CSS parsing context in a browser.
    request: HTML and CSS injection assessment
    signals_any: []
sources:
  - url: https://wstg.owasp.org/stable/4-Web_Application_Security_Testing/11-Client-side_Testing/03-Testing_for_HTML_Injection/
    title: OWASP WSTG Testing for HTML Injection
    retrieved_at: '2026-11-09'
    selection_reason: Provides test objectives for HTML injection distinct from script execution.
  - url: https://wstg.owasp.org/latest/4-Web_Application_Security_Testing/11-Client-side/05-CSS_Injection/
    title: OWASP WSTG CSS Injection
    retrieved_at: '2026-11-09'
    selection_reason: Describes CSS-context injection and potential browser effects.
source_coverage: OWASP WSTG references supply definitions and context. Browser interpretation and policy impact require target-specific verification; examples and controls here are original methodology.
---

# HTML and CSS injection

## Prerequisites

Use an owned browser profile and synthetic input on an assigned route. Identify whether the value is reflected immediately, persisted for later viewers, or placed in a style/property context. Establish which markup and styling behavior the application intentionally permits. Keep any persistent marker private and removable.

## Procedure

1. Record a plain unique text marker through the assigned input and observe its exact DOM location, encoding, and persistence. Compare with an unrelated value and a non-rendered response where available.
2. Test HTML interpretation with a harmless inert element that changes only a local visual marker. Do not include script, event handlers, external resources, or active navigation.
3. Test CSS interpretation separately with a harmless style change on an owned page element. Do not use selectors or resource loads to extract data or make network callbacks.
4. If the browser parses the input as markup or style, verify the effect in the actual application origin. For stored input, use only an owned test account/page and remove it immediately after observation.
5. Only if script execution is separately in scope, use the existing XSS technique and its stricter controls. HTML/CSS interpretation alone does not establish XSS.

## Interpretation

Classify plain text reflection, HTML interpretation, CSS interpretation, and script execution separately. Require an actual prohibited browser-visible effect under the established policy. A value appearing in response bytes, a sanitizer difference, or a changed style does not by itself establish sensitive data access or account compromise. Consider trusted rich-text features, browser extensions, CSP, sanitization, and the difference between a developer tools view and rendered DOM.

## Evidence and cleanup

Retain the input class, redacted request/response, DOM location, browser context, observed local effect, policy, controls, and cleanup result. Remove persistent test content and verify it is no longer rendered. No data extraction, external callback, or real-user interaction is part of this technique.
