---
schema_version: 1
title: Unsafe deserialization and object reconstruction
purpose: Distinguish unsafe deserialization and object reconstruction from legitimate behavior using bounded,
  policy-aware comparisons.
applicability:
- An assigned request, cookie, message or import contains a serialized-object candidate or type-bearing
  reconstruction input.
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
  when: An assigned request, cookie, message or import contains a serialized-object candidate or type-bearing
    reconstruction input. while checking prerequisites in an assigned interaction.
  request: Unsafe deserialization and object reconstruction reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: An assigned request, cookie, message or import contains a serialized-object candidate or type-bearing
    reconstruction input. while designing future tests from recorded observations.
  request: Unsafe deserialization and object reconstruction hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An assigned request, cookie, message or import contains a serialized-object candidate or type-bearing
    reconstruction input. while checking a specific assigned boundary hypothesis.
  request: Unsafe deserialization and object reconstruction vulnerability verification
  signals_any: []
- phases:
  - execution
  when: An assigned request, cookie, message or import contains a serialized-object candidate or type-bearing
    reconstruction input. after a boundary is verified and a minimal demonstration is authorized.
  request: Unsafe deserialization and object reconstruction execution
  signals_any: []
- phases:
  - analysis
  when: An assigned request, cookie, message or import contains a serialized-object candidate or type-bearing
    reconstruction input. while interpreting recorded results and competing explanations.
  request: Unsafe deserialization and object reconstruction analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Deserialization_Cheat_Sheet.md
  title: Deserialization Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.511563+00:00'
  selection_reason: untrusted serialized-object processing.
source_coverage: 'Independently selected retrieved references supply mechanism/control background. untrusted
  serialized-object processing. Limits: Development-oriented guidance supplies mechanism/control background;
  application policy, live exploitability, negative controls and minimal-impact procedure remain original
  methodology requiring target-specific verification. Numbered procedures and controls are original knowledge-allowed
  synthesis, not individually source-verified. No completed target execution or affected-version claim
  is supplied.'
---

# Unsafe deserialization and object reconstruction

## Applicability and prerequisites

An assigned request, cookie, message or import contains a serialized-object candidate or type-bearing reconstruction input. Establish the accepted format, integrity policy, expected types and trusted-field/capability restrictions. Use an owned test object and an owner-approved nonsecret side-effect marker if object callbacks are to be assessed. Encoding and local construction tools plus network replay are sufficient; never deserialize untrusted candidate bytes in a privileged local interpreter just to inspect them.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. Inspect the assigned transport for encoding, content-type, framing and type metadata using a bounded inert parser/hex view or safe local decoder. Magic bytes, base64 prefixes, dots or class names are clues, not proof of dangerous reconstruction.
2. Distinguish ordinary JSON data binding, signed/encrypted serialized state, polymorphic type reconstruction and native object formats. Identify whether a user may legitimately alter ordinary fields and which types/fields/callback effects must remain server-controlled.

## Hypothesis generation

1. From recorded content design future baseline round-trip, well-formed benign mutation and corrupted/inert controls. Separate format parsing, integrity enforcement, type admission and callback/capability behavior; do not infer a gadget chain from a serialization format.
2. For a type-boundary hypothesis identify a supported harmless alternate type or owner-provided test hook and the prohibited effect it would expose. Unknown classes, filters or gadget availability remain prerequisites, not assumed exploitability.
3. For trusted fields plan an owned object and explicit policy comparison. A client-editable preference changing after reserialization may be intentional. Do not submit objects, invoke callbacks or execute a deserializer during hypothesis generation.

## Vulnerability verification

1. Replay a valid small serialized fixture through the assigned application interface and verify its benign effect. Compare a truncated/corrupted version and an unchanged encoded copy to establish actual parsing and encoding behavior, without loading it as a local executable object.
2. Change one ordinary permitted scalar in a well-formed fixture and compare the application's expected result. This confirms reconstruction/data use only. For integrity-protected formats, compare an invalid integrity tag/signature; inability to produce an authorized valid tag makes deeper field/type tests inconclusive.
3. If a restricted-field hypothesis is assigned, mutate one policy-protected field on an owned fixture and verify durable server-side state or identity effect, comparing an allowed field and a nonexistent field. A reflected property or parse success is insufficient; ordinary mass assignment may better describe the narrower mechanism.
4. If authorized harmless type/callback fixtures exist, vary only the type or reconstruction hook while preserving the valid envelope. Compare the allowed type, invalid/nonexistent type and inert/no-hook object. Require an attributable approved marker generated during server reconstruction, not merely a class name in an error or a URL echoed from input.
5. Repeat the original benign baseline and controls. Do not use command-executing gadget chains, file reads, recursive graphs, huge collections or callbacks to unapproved destinations. Without a discriminating safe callback fixture, record accepted types or parser behavior rather than RCE.

## Execution

1. Demonstrate the verified trusted-field/type/callback boundary on one owned object using a nonsecret approved marker or reversible scalar. State whether the effect was a field overwrite, unauthorized type construction or a specific reconstruction callback; do not equate all with arbitrary code execution.
2. Restore the object's original fields via the normal application interface, remove test imports/jobs/callback routes and verify the benign fixture again. Do not leave malformed persistent state that later background tasks may deserialize.

## Analysis

1. Separate serialization identification, successful parsing, integrity failure, unauthorized object/field control and demonstrated capability. A signed blob's opacity does not prove safety; editable JSON alone does not imply unsafe native deserialization.
2. Explain intentional polymorphism, permitted schema fields, validation errors, rejected unknown types, cached state and independent authorization controls. A library/version or known gadget class alone proves neither availability in the target's classpath nor exploitability.

## Evidence requirements

Retain small fixture hashes and sensitive exact bytes, encoding/envelope transforms, expected type/field policy, valid/corrupt/inert comparisons, observed server state or approved callback marker and repeats. Record missing signing, class or hook prerequisites explicitly.

## Limitations

No universal gadget generation, RCE chain, denial-of-service graph or local target inspection is supplied. External-only evidence often cannot establish filter configuration or all available types. Missing safe observability means an inconclusive capability assessment, not a secure verdict.

## Trigger examples and non-examples

Matching: An assigned request, cookie, message or import contains a serialized-object candidate or type-bearing reconstruction input. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
