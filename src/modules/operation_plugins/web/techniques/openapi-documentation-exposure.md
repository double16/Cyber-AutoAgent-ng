---
schema_version: 1
title: OpenAPI documentation exposure and declared authorization
purpose: Assess assigned API documentation against its intended audience and distinguish specification disclosure from actual operation authorization failures.
applicability:
  - An assigned surface supplies Swagger UI, an OpenAPI specification, or an explicit reference to one.
phases: [reconnaissance, hypothesis_generation, vulnerability_verification, execution, analysis]
active: true
triggers:
  - phases: [reconnaissance, hypothesis_generation, vulnerability_verification, execution, analysis]
    when: An assigned API surface supplies OpenAPI documentation whose audience, sensitive contents, or declared operation authorization needs assessment.
    request: OpenAPI documentation exposure and declared authorization
    signals_any: []
sources:
  - url: https://spec.openapis.org/oas/v3.1.1.html
    title: OpenAPI Specification v3.1.1
    retrieved_at: '2026-11-12'
    selection_reason: Defines specification structure and root/operation security requirement semantics, including overrides and alternatives.
  - url: https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html
    title: OWASP REST Security Cheat Sheet
    retrieved_at: '2026-11-12'
    selection_reason: Supplies authorization, sensitive request data, and management endpoint protection background.
source_coverage: OpenAPI defines declarations, not actual enforcement or documentation confidentiality. OWASP supplies security control background. The numbered comparison procedures, baselines, cleanup, and policy interpretation are original knowledge-allowed methodology, not individually source-verified. Neither reference establishes target applicability or vulnerability.
---

# OpenAPI documentation exposure and declared authorization

## Applicability and prerequisites

Use only an assigned documentation surface and explicitly authorized related operations. Establish
whether the UI and specification are intended for public, customer, internal, or role-restricted use.
Obtain test identities and synthetic objects for any authorization comparison. Identify permitted
hosts, methods, request rates, and state changes; a specification's server list does not extend scope.
Use HTTP capture/replay, browser inspection, and JSON/YAML parsing capabilities or equivalents.
Preserve configured HTTP/HTTPS proxy use where supported. Do not run a specification through a
bulk scanner or automatically execute its operations.

## Accepted baselines and controls

An entitled actor can obtain the expected documentation or synthetic operation result. An excluded
actor uses an isolated cookie jar and no copied privileged tokens. Compare the same representation,
operation, object, and parameters. Include a known denied request and an ordinary reachable resource
where available. A login shell, generic fallback page, or cached public specification is not a
successful protected operation. Missing audience policy or suitable identities leaves the affected
comparison unresolved.

## Reconnaissance

1. Inspect assigned captures for the actual UI configuration or specification reference. Record
   final URL, status, content type, authentication context, and response provenance. Follow only
   authorized references; do not enumerate common documentation paths without an assigned task.
2. Validate the document's declared version and structure rather than identifying it by filename.
   Resolve only needed local references or explicitly authorized remote references, with bounded
   parsing and no arbitrary URL or filesystem fetching. Preserve unresolved references as gaps.
3. Record declared servers, paths, methods, parameters, examples, and security requirements. Treat
   internal-looking names and schema fields as leads; a field named password is not a leaked password.
4. Compute effective security per operation using its declared specification version. For OpenAPI
   3.1, an operation's security value replaces the root value; an empty array removes inherited
   requirements. An empty requirement object permits an anonymous alternative. Requirement objects
   in the array are alternatives; schemes within one object must all be satisfied. Preserve OAuth
   scopes. A securitySchemes definition alone does not apply a requirement to any operation.

## Hypothesis generation

1. Use recorded evidence only; send no new requests or execute documented operations in this phase.
2. Separate hypotheses for restricted documentation disclosure, genuinely sensitive example values,
   and inconsistent authorization on one assigned deployed operation. Establish the expected audience,
   actor, object, and prohibited result for each. Public API descriptions can be intentional.
3. Plan entitled/excluded comparisons, a known denied control, and expected permitted, prohibited,
   and inconclusive outcomes. Treat spec accuracy, token validity, object ownership, and missing
   references as explicit future checks. A declaration without runtime evidence cannot prove bypass.

## Vulnerability verification

1. Capture an entitled baseline for the assigned UI and specification separately. Repeat the same
   authorized read as the excluded actor; retain redirect chains and actual body content. Check
   whether the UI is public while the spec remains protected or vice versa.
2. For a disclosure hypothesis, identify a concrete protected synthetic value or policy-restricted
   content actually returned. Compare to the public/redacted view if available. Do not validate real
   credentials by trying them or infer sensitivity from endpoint names alone.
3. For an operation hypothesis, choose one read-only assigned operation with known test-object
   ownership. Capture its legitimate result, then repeat as the excluded actor without inherited
   privileged context. Preserve the documented method and required inputs; replacing POST with GET
   does not test the documented operation. Do not execute mutations without separate authorization.
4. Inspect actual protected data or an agreed harmless effect, not just status or response length.
   Repeat the excluded comparison with an invalid credential control and then restore the entitled
   baseline. Distinguish stale specs, gateway policy, validation failures, and service errors.

## Execution

1. After verification and authorization, preserve one minimal synthetic example of the restricted
   documentation value or protected operation result delivered to the excluded actor. Avoid bulk
   extraction, unrelated endpoints, and collecting other users' data.
2. Remove any approved temporary test objects, invalidate disposable sessions, and verify normal
   behavior remains available. If restoration fails, stop and preserve the limitation.

## Analysis, evidence, and limitations

Retain the audience policy, exact specification revision/hash, relevant declaration pointers,
isolated actor contexts, request/response comparisons, actual protected value/effect, controls, and
cleanup outcome. Keep secrets in sensitive internal evidence and redact report/shareable records.

Classify documentation exposure and operation enforcement independently. A public specification,
missing security declaration, sensitive-sounding property, or 200 response alone is not a finding.
A correctly restricted operation may coexist with intentionally public documentation; a declaration
may be stricter or looser than the deployed policy. Interpret supported, refuted-for-this-case, and
inconclusive outcomes without claiming complete API coverage.

## Trigger examples and non-examples

Matching: assigned Swagger UI references a spec whose intended audience needs checking, or an
assigned operation has a declared authorization rule requiring comparison. Select the section for
the structured phase. Non-matching: OpenAPI terminology in unrelated prose, a spec URL elsewhere in
a shared bundle, or an unassigned server listed in a specification. Recommendations do not establish
prerequisites, expand scope, or prove findings.
