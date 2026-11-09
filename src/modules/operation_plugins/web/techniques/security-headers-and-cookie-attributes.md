---
schema_version: 1
title: Security response headers and cookie attributes
purpose: Compare response security controls and cookie attributes with the application's documented browser threat model.
applicability:
  - An assigned HTTP response sets browser security headers or session-bearing cookies.
phases: [reconnaissance, hypothesis_generation, vulnerability_verification, execution, analysis]
active: true
triggers:
  - phases: [reconnaissance, hypothesis_generation, vulnerability_verification, execution, analysis]
    when: An assigned response includes security headers or cookies whose policy needs assessment.
    request: Security response headers and cookie attributes
    signals_any: []
sources:
  - url: https://cheatsheetseries.owasp.org/cheatsheets/HTTP_Headers_Cheat_Sheet.html
    title: OWASP HTTP Security Response Headers Cheat Sheet
    retrieved_at: '2026-11-09'
    selection_reason: Describes browser security response headers and the security role of Set-Cookie attributes.
  - url: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
    title: OWASP Session Management Cheat Sheet
    retrieved_at: '2026-11-09'
    selection_reason: Describes Secure, HttpOnly and SameSite session cookie attributes.
source_coverage: OWASP cheat sheets supply control background. The application's policy and impact must be established independently; header presence or absence alone is not a vulnerability conclusion.
---

# Security response headers and cookie attributes

## Prerequisites

Use assigned representative responses for public, authenticated, sensitive, redirect, and API content where available. Establish HTTPS policy, intended framing and embedding, script/resource policy, caching needs, and which cookies carry session or sensitive state. A missing header may be intentional for a non-browser response or an endpoint with no relevant browser behavior.

## Procedure

1. Capture response headers from a clean baseline, including redirects and each relevant content type. Record duplicate or conflicting values and distinguish edge responses from origin behavior when that distinction is in scope.
2. For each applicable control, compare observed behavior with explicit policy: transport enforcement, content type handling, framing, referrer handling, and content security policy. Do not treat a scanner grade as impact evidence.
3. Inspect each `Set-Cookie` separately. Identify session-bearing cookies and compare `Secure`, `HttpOnly`, `SameSite`, scope, lifetime, and prefix constraints with the application's transport and cross-site workflow requirements.
4. Where authorized, verify a consequential browser behavior using an owned test session and harmless page or marker. For example, assess whether a sensitive session cookie is sent over an in-scope HTTP path or whether an unintended origin can frame a stateful page. Do not collect real user data.
5. Compare with an unrelated API/static response and a policy-compliant control. Restore any test setting and invalidate test sessions.

## Interpretation

Tie each omission or conflict to a relevant browser decision and a documented requirement. `HttpOnly` is not expected for a cookie intentionally read by application JavaScript; `SameSite` settings depend on cross-site flows; CSP and framing controls are meaningful only on content that browsers render. A header value does not prove the browser enforced it, and a response lacking a header is not independently proof of exploitability.

## Evidence and limitations

Retain the exact redacted response, route/content type, cookie purpose, applicable policy, browser outcome, comparison, and cleanup. This procedure does not infer missing controls from one response or prescribe a universal header set.
