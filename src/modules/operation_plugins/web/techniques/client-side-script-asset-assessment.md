---
schema_version: 1
title: Client-side JavaScript assets and bundled dependencies
purpose: Inventory dependencies and versions in assigned JavaScript assets and assess sensitive embedded content and script-integrity controls without equating clues with vulnerabilities.
applicability:
  - An assigned application loads JavaScript assets or supplies captured bundles whose dependencies, sensitive contents, or execution trust require assessment.
phases: [reconnaissance, hypothesis_generation, vulnerability_verification, execution, analysis]
active: true
triggers:
  - phases: [reconnaissance, hypothesis_generation, vulnerability_verification, execution, analysis]
    when: Assigned JavaScript assets or referencing pages require dependency/version inventory, embedded-content review, or script-integrity assessment.
    request: Client-side JavaScript asset and bundled dependency assessment
    signals_any: []
sources:
  - url: https://www.w3.org/TR/2016/REC-SRI-20160623/
    title: Subresource Integrity W3C Recommendation 23 June 2016
    retrieved_at: '2026-11-12'
    selection_reason: Defines integrity metadata and resource verification; pinned stable baseline avoids treating newer draft controls as universally implemented.
  - url: https://cheatsheetseries.owasp.org/cheatsheets/Third_Party_Javascript_Management_Cheat_Sheet.html
    title: OWASP Third Party Javascript Management Cheat Sheet
    retrieved_at: '2026-11-12'
    selection_reason: Explains third-party script execution trust and integrity or isolation control background.
  - url: https://cheatsheetseries.owasp.org/cheatsheets/Vulnerable_Dependency_Management_Cheat_Sheet.html
    title: OWASP Vulnerable Dependency Management Cheat Sheet
    retrieved_at: '2026-11-12'
    selection_reason: Supplies dependency risk assessment background; individual component advisories and affected versions need separate verification.
source_coverage: References describe integrity, execution trust, and contextual dependency risk. Bundle identification, provenance comparisons, and synthetic-content controls are original knowledge-allowed methodology, not individually source-verified. Source maps and banners are identification leads, not authoritative affectedness evidence. References establish no target vulnerability or component-specific affected version.
---

# Client-side JavaScript assets and bundled dependencies

## Applicability and prerequisites

Use assigned script assets, captures, and referencing pages only. Record which application subject
loads each asset; a different script elsewhere in a shared bundle inventory does not establish a
connection. Resolve permitted hosts, source-map access, browser execution, identities, and state
changes before retrieving new material. A referenced CDN or source-map URL does not extend scope.
Use HTTP capture, bounded offline parsing/search, and a disposable browser or equivalents. Preserve
configured HTTP/HTTPS proxy use where supported. Do not execute downloaded bundles as local scripts
or fetch dependencies merely because a parser discovers a URL. Local files are operation evidence
or tooling, never an alternative route into the assessed target.

## Accepted baselines and controls

Preserve the original captured bytes and acquisition context, and work on a separate decoded or
formatted copy with offsets traceable to the original. Capture a normal page load and the actual
script responses when authorized. A valid response body, content type, stable hash, or successful
parser establishes availability only. Compression, cache variation, missing ETags, and differing
wire lengths do not by themselves make a script unassessable.

Use synthetic content, known package references, and an owned test page where comparisons need
them. Keep normal page behavior as baseline. Missing runtime access or source maps limits precision;
it does not erase a useful static inventory or establish absence of dependencies.

## Reconnaissance

1. Record each assigned asset URL or artifact reference, acquisition time, status, representation
   decoding, body hash, referencing page, script element attributes, and observed loading context.
   Distinguish first-party code, vendored code, third-party origins, workers, modules, and lazy chunks
   only as supported by evidence. Follow explicitly assigned chunks/references within scope; record
   uninspected chunks and avoid claiming whole-application coverage from one bundle.
2. Search the entire available bundle for all identifiable dependencies, not just the first library.
   Inspect banners, license notices, embedded package metadata, module identifiers, recognizable
   library code, and explicitly authorized source maps. Parse safely offline without running bundle
   code. Map formatted or source-map locations back to the actual captured bundle where possible.
3. Create a per-occurrence inventory: component/package and ecosystem, proposed version or range,
   asset/hash, evidence offset or module/source-map location, identification method, confidence,
   conflicting clues, and unresolved conditions. Preserve distinct versions of the same package.
   Deduplicate corroborating clues for one occurrence without merging genuinely separate copies.
4. Corroborate versions using independent clues or a trusted known release when available. A banner
   can be stale, a license list can include build-only packages, and minification/forking can defeat
   signatures. Do not apply the application's version to every dependency or invent exact versions
   from a generic code match. Report unknown versions and conflicting evidence explicitly.
5. Separately inventory potential secrets, internal references, source-map contents, and public
   configuration. Record minimal supporting locations without exposing real secrets in reports.
   A public client identifier, endpoint name, example key, or high-entropy string is only a lead.

## Hypothesis generation

1. Use recorded evidence only; fetch no new asset or advisory, execute no page, and test no credential
   during this phase. Design future checks for unresolved inventory, sensitivity, execution, and
   integrity conditions rather than declaring completed results.
2. Separate hypotheses for embedded sensitive values, a specific component's applicable vulnerability,
   and a script trust/integrity failure. For CVEs, use the existing known-component/CVE applicability
   guidance: establish package identity, advisory conditions, affected code path, and mitigations.
3. Plan normal and controlled comparisons with explicit outcomes. Distinguish bundled presence,
   loaded module, reachable function, and exploitable behavior. Plan execution tracing only if needed
   and authorized. Neither an old-looking version nor a missing integrity attribute proves impact.

## Vulnerability verification

1. Recheck the relevant captured occurrence and referencing page against the current assigned response
   when permitted. Explain build changes and cache differences; retain both revisions rather than
   silently combining them. Confirm runtime use only through an authorized normal interaction or
   instrumented disposable context; unexecuted bundled code remains an inventory observation.
2. For sensitive-content claims, establish the intended audience and actual sensitive meaning.
   Prefer an owner-provided synthetic canary or test value, compared with a public/example-value
   control. Verify that the excluded actor can retrieve the value from the assigned asset. Do not
   redeem real embedded credentials or access unrelated systems to determine whether they work.
3. For integrity assessment, inspect the actual referencing element, integrity metadata, resource
   bytes, origin/CORS behavior, and browser outcome. Missing SRI alone is a configuration observation;
   determine what attacker-controlled modification path and protected execution effect the claim
   requires. CSP nonces do not validate the bytes of an otherwise trusted external script.
4. Where an owned isolated page and fixture asset are authorized, compare a matching integrity hash
   with a deliberately mismatched hash while preserving loading conditions. Use harmless marker code
   and observe execution versus browser rejection, with a normal-load control. This demonstrates the
   browser control under those conditions, not an ability to modify the target's script. Do not
   tamper with production/CDN resources or infer target exploitability from a local demonstration.
5. Leave dependency exploitability to its specific controlled method. Scanner labels, absent runtime
   errors, unobserved function calls, or an available patch are insufficient conclusions about the
   tested code path. Preserve reachability, configuration, and backport uncertainty explicitly.

## Execution

1. After a boundary is verified and minimal impact is authorized, preserve one synthetic sensitive
   value delivered to the excluded actor or one harmless script marker through the verified trust
   failure. Do not extract bulk secrets, alter third-party assets, or chain into other systems.
2. Remove test fixtures and instrumentation, close disposable browser sessions, and invalidate test
   credentials where applicable. Restore authorized temporary changes and verify normal page loading.
   Preserve unconfirmed cleanup as a limitation.

## Analysis, evidence, and limitations

Deliver the multi-component inventory with every supported version, conflicting clue, unresolved
version, and uninspected asset. For each candidate distinguish identification confidence, actual
runtime use, advisory applicability, and demonstrated impact. A bundle containing two versions is
not automatically using both; a source map or license notice need not match the served build.

Retain assigned-subject provenance, raw/decoded representation relation, hashes, supporting locations,
identification references, browser/page context, actual controls, and cleanup. Keep exact secrets only
in sensitive internal evidence and redact report/shareable output. Do not strip the evidence needed
for later correlation merely because the asset is static.

Static analysis cannot prove all runtime paths, exhaustive dependency absence, server-side behavior,
or supply-chain compromise. Lack of strong ETag, script MIME label, public accessibility, and zero
request inputs are not blanket exclusion criteria. Scope, unreadable evidence, unavailable maps, and
insufficient controls remain bounded limitations rather than evidence of safety or vulnerability.

## Trigger examples and non-examples

Matching: an assigned application script contains multiple possible package/version clues, or its
referencing page needs integrity or embedded-content assessment. Non-matching: library names in an
unrelated shared artifact, a vendor filename without asset evidence, or an unassigned source-map
host. Select the current structured phase; a recommendation does not establish vulnerability or
expand the assigned subject.
