---
schema_version: 1
title: XML external-resource resolution
purpose: Distinguish xml external-resource resolution from legitimate behavior using bounded, policy-aware
  comparisons.
applicability:
- An assigned XML-processing interaction may resolve entities, external DTDs, XInclude, schemas or transformations.
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
  when: An assigned XML-processing interaction may resolve entities, external DTDs, XInclude, schemas
    or transformations. while checking prerequisites in an assigned interaction.
  request: XML external-resource resolution reconnaissance
  signals_any:
  - http.xml
- phases:
  - hypothesis_generation
  when: An assigned XML-processing interaction may resolve entities, external DTDs, XInclude, schemas
    or transformations. while designing future tests from recorded observations.
  request: XML external-resource resolution hypothesis generation
  signals_any:
  - http.xml
- phases:
  - vulnerability_verification
  when: An assigned XML-processing interaction may resolve entities, external DTDs, XInclude, schemas
    or transformations. while checking a specific assigned boundary hypothesis.
  request: XML external-resource resolution vulnerability verification
  signals_any:
  - http.xml
- phases:
  - execution
  when: An assigned XML-processing interaction may resolve entities, external DTDs, XInclude, schemas
    or transformations. after a boundary is verified and a minimal demonstration is authorized.
  request: XML external-resource resolution execution
  signals_any:
  - http.xml
- phases:
  - analysis
  when: An assigned XML-processing interaction may resolve entities, external DTDs, XInclude, schemas
    or transformations. while interpreting recorded results and competing explanations.
  request: XML external-resource resolution analysis
  signals_any:
  - http.xml
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/XML_External_Entity_Prevention_Cheat_Sheet.md
  title: XML External Entity Prevention Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.322695+00:00'
  selection_reason: parser entity and external-resource controls.
source_coverage: 'Independently selected retrieved references supply mechanism/control background. parser
  entity and external-resource controls. Limits: Development-oriented guidance supplies mechanism/control
  background; application policy, live exploitability, negative controls and minimal-impact procedure
  remain original methodology requiring target-specific verification. Numbered procedures and controls
  are original knowledge-allowed synthesis, not individually source-verified. No completed target execution
  or affected-version claim is supplied.'
---

# XML external-resource resolution

## Applicability and prerequisites

An assigned XML-processing interaction may resolve entities, external DTDs, XInclude, schemas or transformations. Establish the XML-processing feature and policy for external resources, owned small XML fixtures and an approved callback receiver or owner-provided nonsecret file fixture. The web module remains external-only: any file demonstration uses a scoped network request to the application, never direct local target filesystem access. Avoid entity expansion, large content and resource exhaustion.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. Inspect the assigned media type and processing route: XML parsing, import, document conversion, schema validation or transformation. A Content-Type or XML namespace only suggests applicability; malformed XML accepted as text is a different path.
2. Record where parsed values are reflected or stored and whether external fetches are intended. Distinguish the initial document URL from resources resolved while parsing; restrictions on entities do not necessarily govern the initial fetch.

## Hypothesis generation

1. Using recorded observations, plan an accepted small document, one external-resource variant and a no-reference/inert reference control. Assign a unique correlation marker to the future reference and identify an actual server-side output or callback discriminator.
2. Keep general entities, parameter/external DTD loading, XInclude and schema/stylesheet retrieval as separate mechanism branches. Do not infer one from another or from a generic HTTP fetch.
3. Specify which external resolution is forbidden by established policy. An expected schema lookup to an allowed host is not XXE evidence by itself. Do not make network requests, read files or submit XML in this context.

## Vulnerability verification

1. Submit the accepted small XML fixture through the assigned feature and verify an application-level parsed result. Compare a harmless changed literal, keeping size, authentication and processing settings stable.
2. For a general-entity hypothesis, define one external entity referencing a unique URL on the approved receiver and reference it once in the relevant value. Compare the same document with a literal marker and an inert/unused definition. Do not use recursive entities, system paths or internal-network ranges.
3. Attribute any callback to the server-side processing step using unique per-request markers and a receiver timeline. Exclude the tester's browser, link previewers or unrelated scanners fetching the URL. An unused declaration may still be fetched by a processor; classify external resolution separately from entity substitution into output.
4. If another branch is assigned, use a minimal independent XInclude, schema or stylesheet reference, with a corresponding reference-free document and the same approved destination. Require observed processing of that construct; XML acceptance alone is insufficient.
5. If the owner supplies a nonsecret fixture behind a prohibited file/resource boundary and expressly authorizes its retrieval, request only that marker through the XML feature and compare an absent/incorrect fixture control. Otherwise leave file disclosure untested. Repeat the original document and stop once the narrow forbidden resolution or read is established.

## Execution

1. Demonstrate one correlated prohibited external resolution or the specifically approved nonsecret fixture read. Record only the minimal marker, never arbitrary system files, metadata credentials or expansive XML content.
2. Delete uploaded XML/import outputs through the normal application flow, disable test receiver routes and verify ordinary processing. Remove references from any stored document so asynchronous retries cannot continue contacting the receiver.

## Analysis

1. Distinguish server-side fetch, external entity substitution, file read and resource exhaustion; a callback supports only the observed mechanism and policy effect.
2. Explain legitimate schema/catalog resolution, renderer URL fetches, browser-origin traffic, DNS caching and suppressed output. No callback may reflect blocked egress or a nontriggered code path rather than safe parsing; parser/version defaults alone do not establish the configured behavior.

## Evidence requirements

Preserve the small baseline and variant XML, exact processing route, reference type, correlation IDs, receiver or response marker data, source attribution controls, external-resource policy and repeat results. Keep sensitive parser outputs internal and report only the demonstrated boundary.

## Limitations

No recursive expansion, OOM test, arbitrary local-file read, protocol pivot or broad SSRF scan is supplied. External-only access may leave parser settings, invisible resolution and file impact unknown; mark those branches untested.

## Trigger examples and non-examples

Matching: An assigned XML-processing interaction may resolve entities, external DTDs, XInclude, schemas or transformations. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
