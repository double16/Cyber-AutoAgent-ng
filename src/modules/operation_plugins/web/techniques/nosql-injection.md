---
schema_version: 1
title: NoSQL operator and query injection
purpose: Distinguish nosql operator and query injection from legitimate behavior using bounded, policy-aware
  comparisons.
applicability:
- An assigned JSON, form or URL value appears to influence a document-store lookup or query expression.
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
  when: An assigned JSON, form or URL value appears to influence a document-store lookup or query expression.
    while checking prerequisites in an assigned interaction.
  request: NoSQL operator and query injection reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: An assigned JSON, form or URL value appears to influence a document-store lookup or query expression.
    while designing future tests from recorded observations.
  request: NoSQL operator and query injection hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An assigned JSON, form or URL value appears to influence a document-store lookup or query expression.
    while checking a specific assigned boundary hypothesis.
  request: NoSQL operator and query injection vulnerability verification
  signals_any: []
- phases:
  - execution
  when: An assigned JSON, form or URL value appears to influence a document-store lookup or query expression.
    after a boundary is verified and a minimal demonstration is authorized.
  request: NoSQL operator and query injection execution
  signals_any: []
- phases:
  - analysis
  when: An assigned JSON, form or URL value appears to influence a document-store lookup or query expression.
    while interpreting recorded results and competing explanations.
  request: NoSQL operator and query injection analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/NoSQL_Security_Cheat_Sheet.md
  title: NoSQL Security Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.118608+00:00'
  selection_reason: operator injection and query construction.
source_coverage: 'Independently selected retrieved references supply mechanism/control background. operator
  injection and query construction. Limits: Development-oriented guidance supplies mechanism/control background;
  application policy, live exploitability, negative controls and minimal-impact procedure remain original
  methodology requiring target-specific verification. Numbered procedures and controls are original knowledge-allowed
  synthesis, not individually source-verified. No completed target execution or affected-version claim
  is supplied.'
---

# NoSQL operator and query injection

## Applicability and prerequisites

An assigned JSON, form or URL value appears to influence a document-store lookup or query expression. Establish the input contract: scalar search value, constrained filter language or deliberately accepted query object. Use two owned records with distinct marker fields and a read-only repeatable endpoint. Know which data each identity is allowed to receive. JSON-aware replay and semantic comparison are sufficient; a database connection is unnecessary.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. Inspect the assigned value's actual wire type, nesting and content type. Record whether bracketed form keys become objects, duplicate keys are accepted or strings are parsed as JSON; those behaviors are prerequisites, not injection proof.
2. Identify a candidate query mechanism and benign alternatives such as documented filter operators, client-side filtering and public search. Database-brand headers or a dollar-prefixed key alone do not establish backend evaluation.

## Hypothesis generation

1. Design separate future tests for type/operator injection and string-expression injection using recorded evidence. Do not assume a MongoDB operator applies to every NoSQL backend.
2. For a scalar contract, plan a valid scalar baseline, an object containing one supported comparator, a complementary comparator and an inert object with an unknown key. Specify the owned-record outcome expected from each and what would refute the mechanism.
3. For intentional query APIs, identify the policy restriction or grammar escape being tested. Merely broadening a permitted public query is not an authorization failure. Do not issue queries while generating hypotheses.

## Vulnerability verification

1. Replay the accepted scalar lookup and a valid nonexistent scalar, confirming returned owned-record IDs and stable authorization. Compare a literal string resembling an operator with a true JSON object, keeping other request fields fixed.
2. If the backend/operator is justified, replace only the scalar with a comparator object. For an equality field, compare a controlled `$eq` value matching the owned fixture with a different nonmatching value; then, where useful, compare a bounded `$ne` selection against those same fixtures. Treat names as examples conditional on actual operator support.
3. Send an inert same-shape object and a wrong-type array control. Determine whether application validation, coercion, a default search or the database operator explains the outcome. An empty object returning all publicly searchable items may be documented behavior.
4. For a supported string-expression context, use a non-executing complementary predicate pair within the established syntax. Do not use server-side JavaScript execution, pathological regular expressions, unbounded scans or unrestricted query objects.
5. Repeat baseline and variation in mixed order. Confirm unintended backend expression control under the established input contract; claim unauthorized access only if a separate policy comparison shows prohibited owned-test data or action, not merely a larger result set.

## Execution

1. Demonstrate the verified operator boundary with one or two owned synthetic records and the smallest response field needed. A limited read suffices; avoid collection enumeration, writes or privilege changes.
2. Restore any test filter settings or fixtures created through normal authorized interfaces. Re-run the original scalar query to confirm the comparator did not change persistent state.

## Analysis

1. Separate schema/type acceptance, expression interpretation and authorization. A JSON parse error or `$` key acceptance alone supports none of the latter conclusions.
2. Explain documented query APIs, loose type coercion, null/missing-field semantics, application-side filtering and caches. A rejected operator does not exclude a string-concatenation path or another field; a permitted operator is not a flaw unless it crosses the specified grammar or access boundary.

## Evidence requirements

Retain raw wire types and encodings, accepted and nonmatching scalar requests, comparator and inert controls, stable owned-record markers, actor entitlements and repeat results. Capture whether the result was a backend response or client-only transformation.

## Limitations

NoSQL languages and null/type semantics vary. Unknown backend support, missing contract or no stable owned fixtures leaves the branch inconclusive. This method does not cover database administration exposure or resource-exhaustion probes.

## Trigger examples and non-examples

Matching: An assigned JSON, form or URL value appears to influence a document-store lookup or query expression. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
