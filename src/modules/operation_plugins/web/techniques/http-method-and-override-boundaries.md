---
schema_version: 1
title: HTTP method and override boundaries
purpose: Determine whether an assigned route exposes methods or method overrides that bypass its intended access and state controls.
applicability:
  - An assigned route has an established method policy or an observed method-override mechanism.
phases: [reconnaissance, hypothesis_generation, vulnerability_verification, execution, analysis]
active: true
triggers:
  - phases: [reconnaissance, hypothesis_generation, vulnerability_verification, execution, analysis]
    when: An assigned route's supported methods or override behavior may cross its documented access policy.
    request: HTTP method and override boundary assessment
    signals_any: []
sources:
  - url: https://wstg.owasp.org/stable/4-Web_Application_Security_Testing/02-Configuration_and_Deployment_Management_Testing/06-Test_HTTP_Methods/
    title: OWASP WSTG Test HTTP Methods
    retrieved_at: '2026-11-09'
    selection_reason: Covers supported methods, access control bypass and method override testing.
source_coverage: OWASP WSTG supplies method-testing background. State-change boundaries and controls here are original methodology and require explicit authorization.
---

# HTTP method and override boundaries

## Prerequisites

Use only assigned routes with known method policy, an owned test identity, and reversible synthetic data. Establish whether a request could create, replace, or delete content before sending it. Do not send state-changing methods to an unknown path or real user object.

## Procedure

1. Capture the normal permitted request and an unauthorized or invalid control. Record route, identity, resource, response, and resulting state.
2. Use `OPTIONS` as an advisory signal only. Compare it with a small, explicit set of methods allowed by the route contract; do not enumerate arbitrary verbs.
3. For a suspected write/delete method, use a dedicated disposable object and a harmless unique marker. Compare the authorized method with the same method from an actor lacking permission, then verify state through the normal read interface.
4. If an override header or query/body field was observed, vary only that mechanism while holding method, identity, and object constant. Test only a harmless, reversible operation.
5. Where an edge proxy is in scope, compare its observed method handling with the assigned origin path only when the origin is explicitly authorized. Clean up the object and verify ordinary access is restored.

## Interpretation

An `Allow` header, status code, or accepted method token does not prove an access-control flaw. Confirm an unauthorized state change or policy-forbidden action and attribute it to method handling rather than a parallel route, cached response, proxy normalization, or stale client state. Distinguish intentional REST methods from unexpected method override behavior.

## Evidence and limitations

Retain route policy, baseline and negative requests, method/override variation, observed state before and after, identity, proxy context, and cleanup. This procedure does not fuzz arbitrary methods or test destructive operations.
