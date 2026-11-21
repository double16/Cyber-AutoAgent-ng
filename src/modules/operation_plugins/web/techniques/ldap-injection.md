---
schema_version: 1
title: LDAP filter and distinguished-name injection
purpose: Distinguish ldap filter and distinguished-name injection from legitimate behavior using bounded,
  policy-aware comparisons.
applicability:
- An assigned search, identity lookup or authentication flow plausibly builds LDAP filters or distinguished
  names from input.
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
  when: An assigned search, identity lookup or authentication flow plausibly builds LDAP filters or distinguished
    names from input. while checking prerequisites in an assigned interaction.
  request: LDAP filter and distinguished-name injection reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: An assigned search, identity lookup or authentication flow plausibly builds LDAP filters or distinguished
    names from input. while designing future tests from recorded observations.
  request: LDAP filter and distinguished-name injection hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An assigned search, identity lookup or authentication flow plausibly builds LDAP filters or distinguished
    names from input. while checking a specific assigned boundary hypothesis.
  request: LDAP filter and distinguished-name injection vulnerability verification
  signals_any: []
- phases:
  - execution
  when: An assigned search, identity lookup or authentication flow plausibly builds LDAP filters or distinguished
    names from input. after a boundary is verified and a minimal demonstration is authorized.
  request: LDAP filter and distinguished-name injection execution
  signals_any: []
- phases:
  - analysis
  when: An assigned search, identity lookup or authentication flow plausibly builds LDAP filters or distinguished
    names from input. while interpreting recorded results and competing explanations.
  request: LDAP filter and distinguished-name injection analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/LDAP_Injection_Prevention_Cheat_Sheet.md
  title: LDAP Injection Prevention Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.227300+00:00'
  selection_reason: LDAP filter and distinguished-name escaping.
source_coverage: 'Independently selected retrieved references supply mechanism/control background. LDAP
  filter and distinguished-name escaping. Limits: Development-oriented guidance supplies mechanism/control
  background; application policy, live exploitability, negative controls and minimal-impact procedure
  remain original methodology requiring target-specific verification. Numbered procedures and controls
  are original knowledge-allowed synthesis, not individually source-verified. No completed target execution
  or affected-version claim is supplied.'
---

# LDAP filter and distinguished-name injection

## Applicability and prerequisites

An assigned search, identity lookup or authentication flow plausibly builds LDAP filters or distinguished names from input. Establish the allowed search grammar and identity/resource policy. Use two owned test directory identities or a controlled lookup fixture, with a valid nonempty wrong password for authentication controls. An HTTP client and result comparator suffice; direct LDAP or directory administration access is not assumed.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. Inspect assigned input/output for filter-like or DN-like behavior. Record whether the input is a search value, an intentional filter or a component of a distinguished name; escaping rules differ across these contexts.
2. Identify stable owned records and distinguish public wildcard search from restricted lookup. A directory error or an asterisk returning extra public entries is context, not necessarily injection or unauthorized access.

## Hypothesis generation

1. From recorded observations design separate filter-structure and DN-boundary hypotheses. Specify the accepted exact-match baseline, a valid nonmatch, a literal escaped-special-character control and one balanced structural variation.
2. For filter contexts plan complementary conditions targeting only the two owned fixtures; avoid using a bare wildcard result as a conclusion. For DN contexts plan an owned component containing an escaped comma/plus against an unescaped boundary variation.
3. For login, plan to hold a known nonempty incorrect password fixed and verify the resulting identity. Empty-password anonymous-bind semantics would confound an authentication bypass claim. Do not send LDAP-related requests during hypothesis generation.

## Vulnerability verification

1. Replay an accepted lookup or owned login and a valid nonexistent search/wrong-password request. Confirm identity and entitlement via an authenticated result, not a redirect label or submitted username.
2. In a justified filter context, vary only the suspected filter boundary and compare predicates selecting one owned fixture versus its complementary nonmatch. Use a grammar-correct structural pair appropriate to the observed expression; do not assume SQL quote syntax applies to LDAP.
3. Compare a literal special-character value using the filter escaping expected by the protocol with its structural form and an invalid/unbalanced control. Require evidence that Boolean structure or a filter clause was changed, rather than intended wildcard matching or application-side normalization.
4. In a justified DN context, compare escaped versus unescaped separators using only owned directory components. Verify which identity/resource was resolved; resolution failure or a parser error alone does not show a successful boundary escape.
5. For authentication, keep the same nonempty wrong password across variant/control, compare an independent valid session and check the resulting owned account. If anonymous binding, public lookup or an existing session explains success, injection-based authentication bypass is unproven. Repeat normal baseline after the variation.

## Execution

1. Use the confirmed structure change to demonstrate one prohibited lookup or owned-account authentication result. Limit output to a synthetic nonsecret field; do not enumerate the directory, change attributes or search credentials.
2. Clear test sessions, remove only fixtures created for this test through authorized owner interfaces and restore any saved search. Verify the exact-match baseline still behaves normally.

## Analysis

1. Classify grammar control separately from a policy violation. A publicly supported wildcard or documented advanced search may be legitimate even when broad.
2. Explain DN canonicalization, filter escaping, anonymous bind, application caches and normal search ranking. Encoded punctuation accepted as a literal is a negative control; failure of one context says nothing about another encoder or input.

## Evidence requirements

Retain raw filter/DN input encodings, expected grammar, exact-match/nonmatch/escaped/structural controls, nonempty-password condition where relevant, actual resolved identity/record and repeat comparisons. Keep directory personal data and credential material internal.

## Limitations

Directory schema and filter grammar may be externally unknown. Missing owned fixtures, policy or identity confirmation makes the corresponding branch inconclusive. No directory-wide enumeration or administration test is provided.

## Trigger examples and non-examples

Matching: An assigned search, identity lookup or authentication flow plausibly builds LDAP filters or distinguished names from input. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
