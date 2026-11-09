---
schema_version: 1
title: GraphQL resolver and operation boundaries
purpose: Distinguish graphql resolver and operation boundaries from legitimate behavior using bounded,
  policy-aware comparisons.
applicability:
- An assigned request uses a GraphQL operation or an existing schema/operation observation supports a
  resolver hypothesis.
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
  when: An assigned request uses a GraphQL operation or an existing schema/operation observation supports
    a resolver hypothesis. while checking prerequisites in an assigned interaction.
  request: GraphQL resolver and operation boundaries reconnaissance
  signals_any:
  - http.graphql
- phases:
  - hypothesis_generation
  when: An assigned request uses a GraphQL operation or an existing schema/operation observation supports
    a resolver hypothesis. while designing future tests from recorded observations.
  request: GraphQL resolver and operation boundaries hypothesis generation
  signals_any:
  - http.graphql
- phases:
  - vulnerability_verification
  when: An assigned request uses a GraphQL operation or an existing schema/operation observation supports
    a resolver hypothesis. while checking a specific assigned boundary hypothesis.
  request: GraphQL resolver and operation boundaries vulnerability verification
  signals_any:
  - http.graphql
- phases:
  - execution
  when: An assigned request uses a GraphQL operation or an existing schema/operation observation supports
    a resolver hypothesis. after a boundary is verified and a minimal demonstration is authorized.
  request: GraphQL resolver and operation boundaries execution
  signals_any:
  - http.graphql
- phases:
  - analysis
  when: An assigned request uses a GraphQL operation or an existing schema/operation observation supports
    a resolver hypothesis. while interpreting recorded results and competing explanations.
  request: GraphQL resolver and operation boundaries analysis
  signals_any:
  - http.graphql
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/GraphQL_Cheat_Sheet.md
  title: GraphQL Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.539248+00:00'
  selection_reason: resolver authorization, query depth and batching.
source_coverage: 'Independently selected retrieved references supply mechanism/control background. resolver
  authorization, query depth and batching. Limits: Development-oriented guidance supplies mechanism/control
  background; application policy, live exploitability, negative controls and minimal-impact procedure
  remain original methodology requiring target-specific verification. Numbered procedures and controls
  are original knowledge-allowed synthesis, not individually source-verified. No completed target execution
  or affected-version claim is supplied.'
---

# GraphQL resolver and operation boundaries

## Applicability and prerequisites

An assigned request uses a GraphQL operation or an existing schema/operation observation supports a resolver hypothesis. Use only assigned observed operations or a legitimately available schema; this method does not perform general schema/endpoint discovery. Establish object, field, role and tenant entitlements with two owned accounts/fixtures as needed. Agree strict operation-count, depth and result-size bounds before complexity/batching checks. GraphQL-aware replay and semantic JSON comparison or equivalents suffice.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. Inspect the assigned operation name, variables, selection set, fragments, aliases and query/mutation/subscription type. Record actual data and errors separately; HTTP200 can contain a denied operation or partial data.
2. Identify where the same owned object/field can be reached through an observed node, edge or mutation resolver. A visible schema, introspection capability or exploration UI is context, not itself unauthorized data access.

## Hypothesis generation

1. From recorded operations design future comparisons for object/field/function authorization, input property binding and tenant context. Specify an allowed owner/role baseline and a denied-policy actor/object/field variation, changing one dimension at a time.
2. For aliases/batching or shallow depth checks define the documented limit/invariant, a tiny just-under/just-over comparison within an agreed envelope and a repeated single-operation control. Do not propose high-depth recursion, large page sizes or brute-force batches.
3. Specify how partial data, resolver errors and client caches will be interpreted. Authentication failure at HTTP transport may differ from field-level denial. No operations, introspection or mutation occur during hypothesis generation.

## Vulnerability verification

1. Replay the accepted small operation for the entitled owned actor and confirm semantic data, object ID and field values. Compare a valid absent-object/denied operation and inspect both `data` and `errors`; do not classify by HTTP status alone.
2. Change one owned-object identifier or role/tenant context under an established deny policy, preserving operation and variables otherwise. Compare a separately owned actor's allowed access and an unrelated nonexistent object. Returned synthetic protected fields must match the target fixture, not a reflected ID or null placeholder.
3. For field or function boundaries, add one protected field or use one observed alternate resolver with the same object/actor. Compare an allowed field/path and a denied/inert path. Verify actual response data or durable mutation state, including partial-success responses.
4. For mutation input binding, vary one server-controlled property on an owned object and read back state independently; compare a permitted property and an ignored/invalid property. Schema acceptance alone is not unauthorized mutation.
5. Where explicitly authorized, submit only a tiny alias/batch/depth variation around an agreed rule and compare repeated single operations. Count actual operations/effects, not just HTTP requests; stop immediately at the size/time/cost envelope or instability. Multiple legal operations in one request are not inherently a defect.
6. Repeat the original baseline. For subscriptions verify protected event delivery and authorization in its actual transport, without assuming initial connection acceptance grants every topic. Keep broad resource exhaustion and unrelated resolver testing outside the assigned branch.

## Execution

1. Demonstrate one policy violation using a single synthetic field, owned object read or reversible mutation. Prefer the smallest selection set and one resolver path; do not export the schema, dump records or fan out costly queries.
2. Restore mutation state, unsubscribe/close test streams, remove only test-created objects and verify the original operation. Confirm no queued mutation or subscription continues processing.

## Analysis

1. Separate exposed schema, successful validation, resolver execution, partial data and unauthorized object/property/function effects. GraphQL flexibility and introspection are features unless a concrete policy violation is demonstrated.
2. Explain intentional public fields, shared-object entitlements, null/error masking, resolver-specific authorization, client/DataLoader caches and normal alias support. A limit absence alone is not resource harm or abuse; report only a bounded demonstrated invariant violation.

## Evidence requirements

Retain complete operation/variables, actor/tenant labels, entitlement policy, semantic `data`/`errors` comparisons, owned fixture markers, negative controls, mutation read-back and subscription events. Record exact bounded alias/depth/operation counts when tested.

## Limitations

No full schema discovery, exhaustive resolver coverage, deep query bomb or broad credential guessing is supplied. Missing roles, schema reachability or stable event observations leaves relevant branches untested. General injection/CVE claims require their own method and prerequisites.

## Trigger examples and non-examples

Matching: An assigned request uses a GraphQL operation or an existing schema/operation observation supports a resolver hypothesis. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
