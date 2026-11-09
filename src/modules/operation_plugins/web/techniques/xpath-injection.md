---
schema_version: 1
title: XPath query injection
purpose: Distinguish xpath query injection from legitimate behavior using bounded, policy-aware comparisons.
applicability:
- An assigned lookup or authentication input plausibly affects an XPath expression over XML data.
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
  when: An assigned lookup or authentication input plausibly affects an XPath expression over XML data.
    while checking prerequisites in an assigned interaction.
  request: XPath query injection reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: An assigned lookup or authentication input plausibly affects an XPath expression over XML data.
    while designing future tests from recorded observations.
  request: XPath query injection hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An assigned lookup or authentication input plausibly affects an XPath expression over XML data.
    while checking a specific assigned boundary hypothesis.
  request: XPath query injection vulnerability verification
  signals_any: []
- phases:
  - execution
  when: An assigned lookup or authentication input plausibly affects an XPath expression over XML data.
    after a boundary is verified and a minimal demonstration is authorized.
  request: XPath query injection execution
  signals_any: []
- phases:
  - analysis
  when: An assigned lookup or authentication input plausibly affects an XPath expression over XML data.
    while interpreting recorded results and competing explanations.
  request: XPath query injection analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Injection_Prevention_Cheat_Sheet.md
  title: Injection Prevention Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.121839+00:00'
  selection_reason: XPath interpreter boundary; controlled XPath tests are original synthesis.
- url: https://raw.githubusercontent.com/OWASP/wstg/v4.2/document/4-Web_Application_Security_Testing/07-Input_Validation_Testing/09-Testing_for_XPath_Injection.md
  title: wstg-xpath
  retrieved_at: '2026-11-07T10:32:58.928624+00:00'
  selection_reason: WSTG-INPV-09 describes XPath expression manipulation and authentication/query effects.
source_coverage: 'Independently selected retrieved references supply mechanism/control background. XPath
  interpreter boundary; controlled XPath tests are original synthesis. Limits: Development-oriented guidance
  supplies mechanism/control background; application policy, live exploitability, negative controls and
  minimal-impact procedure remain original methodology requiring target-specific verification. WSTG-INPV-09
  describes XPath expression manipulation and authentication/query effects. Limits: Development-oriented
  guidance supplies mechanism/control background; application policy, live exploitability, negative controls
  and minimal-impact procedure remain original methodology requiring target-specific verification. Numbered
  procedures and controls are original knowledge-allowed synthesis, not individually source-verified.
  No completed target execution or affected-version claim is supplied.'
---

# XPath query injection

## Applicability and prerequisites

An assigned lookup or authentication input plausibly affects an XPath expression over XML data. Know the intended exact-match/search contract and owned XML-backed records or identities. Select a stable read-only response field and establish who may access it. A replay client and semantic comparator are sufficient. XML on the wire alone does not imply XPath evaluation, and direct XML-store access is unnecessary.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. Inspect assigned observations for XML-backed query behavior, XPath-specific diagnostics or field relationships. Treat backend identification as uncertain unless the evidence distinguishes it from SQL, LDAP or application expression parsing.
2. Identify the suspected string, numeric or path context and whether users intentionally supply an XPath/filter language. Record a valid matching value, a valid nonmatch and the authorized data boundary for later comparison.

## Hypothesis generation

1. Design a future complementary Boolean pair for the suspected XPath context, preserving quote/parenthesis balance. Specify the owned-record result predicted by true and false variants, not merely an error difference.
2. Plan an inert quoted/encoded value and an invalid-structure control that distinguish expression interpretation from loose search matching or generic validation. For login hold a known wrong password constant and require an authenticated identity result.
3. If the XML structure is unknown, mark that uncertainty rather than proposing whole-document extraction. Do not issue queries, reconstruct XML data or brute-force names during hypothesis generation.

## Vulnerability verification

1. Replay a valid owned lookup and a valid nonmatching input in an isolated session. Confirm response stability, result identifiers and lack of alternate authentication. Select a comparator independent of reflected input.
2. In the justified context introduce a minimal balanced predicate pair, such as a fixed equality versus inequality on two synthetic constants after the suspected string boundary. Adapt syntax to the actual outer expression; do not treat familiar SQL comments or operators as universally valid XPath.
3. Compare an inert literal form and a malformed expression with the same surrounding input. Require a reproducible semantic result that tracks predicate truth, not just a parsing exception, WAF rejection or a larger public search result.
4. For authentication, keep the same valid nonempty incorrect password and compare a known valid login, an invalid-expression control and the candidate. Inspect a protected identity/resource endpoint; a submitted username displayed on a login error page does not establish authentication.
5. Re-run the original matching/nonmatching baselines and repeat the pair in reversed order. Stop once expression control and any prohibited owned-record access are distinguished. Do not enumerate node names, credentials or the entire XML document.

## Execution

1. Demonstrate the verified expression effect using one synthetic owned record or one test identity under the established prohibited query/authentication condition. A nonsecret marker is sufficient; do not reconstruct the backing XML.
2. End test sessions and remove test records only through the normal authorized interface if they were created for this assessment. Restore original values and verify the baseline lookup.

## Analysis

1. Separate XPath syntax clues, controlled expression interpretation and unauthorized access. An XML parser error, quote-induced failure or query-language feature alone is not a confirmed security boundary violation.
2. Explain intentional query APIs, public wildcard/search behavior, null/empty results, application filtering and cache effects. XPath's ability to navigate an XML tree does not imply that this endpoint can access every node or bypass its independent policy.

## Evidence requirements

Retain exact encoding/outer-context assumptions, accepted matching/nonmatching requests, complementary predicate and inert controls, stable result markers, actual identity and access policy, and reversed-order repeats. Record unknown XML structure as a limitation.

## Limitations

No blind whole-document reconstruction or exhaustive XPath dialect testing is included. Absent backend attribution, stable comparator or owned restricted fixture leaves the mechanism or impact inconclusive.

## Trigger examples and non-examples

Matching: An assigned lookup or authentication input plausibly affects an XPath expression over XML data. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
