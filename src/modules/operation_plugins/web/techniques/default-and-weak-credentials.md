---
schema_version: 1
title: Default and weak credential assessment
purpose: Determine whether explicitly authorized test accounts accept default or predictably weak credentials.
applicability:
  - An assigned authentication flow and disposable test account permit a bounded credential check.
phases: [reconnaissance, hypothesis_generation, vulnerability_verification, execution, analysis]
active: true
triggers:
  - phases: [reconnaissance, hypothesis_generation, vulnerability_verification, execution, analysis]
    when: An assigned login flow or test account has an explicit default or weak credential hypothesis.
    request: Default and weak credential assessment
    signals_any: []
sources:
  - url: https://wstg.owasp.org/stable/4-Web_Application_Security_Testing/04-Authentication_Testing/02-Testing_for_Default_Credentials/
    title: OWASP WSTG Testing for Default Credentials
    retrieved_at: '2026-11-09'
    selection_reason: Defines default credential assessment and predictable initial credentials.
source_coverage: OWASP WSTG supplies background; bounded account selection, controls and interpretation are original methodology. No target credential or exploitability is asserted.
---

# Default and weak credential assessment

## Prerequisites

Use only an owner-provisioned disposable account or an explicitly approved known default for a dedicated test instance. Establish login policy, request limits, lockout behavior, and a protected identity marker. Do not try credentials against arbitrary users, enumerate usernames, spray passwords, or reuse leaked secrets.

## Procedure

1. Record the allowed test identity and one known-good baseline. Establish a fresh session and verify that the baseline reaches only that account's private marker.
2. If expressly in scope, make at most one attempt with a documented vendor or deployment default for that test account. Otherwise use one owner-supplied weak candidate. Never derive candidates from other users or exposed secrets.
3. Compare the candidate with a known-invalid control and the valid baseline in isolated sessions. Follow any accepted login to the same protected marker and verify the resulting principal.
4. Stop after a single confirmed acceptance or after the bounded candidate set is exhausted. Do not test more candidates to estimate password strength.
5. Restore the account password, invalidate test sessions, and verify the ordinary valid login remains functional.

## Interpretation and controls

An accepted candidate is relevant only when it authenticates as the intended test identity under the established policy. A success response, redirect, or token-shaped value alone is insufficient. A failure does not establish resistance to other guesses. Account lockouts, MFA, SSO, stale cookies, and rate controls can confound results; preserve these as separate observations.

## Evidence and limitations

Retain the owner authorization, synthetic account identifier, credential class (not the secret in report text), request count, isolated-session provenance, invalid and valid controls, authenticated identity result, cleanup, and remaining uncertainty. This method does not authorize brute force, password spraying, or testing credentials found in public configuration.
