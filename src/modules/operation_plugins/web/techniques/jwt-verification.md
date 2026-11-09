---
schema_version: 1
title: JWT trust boundaries
purpose: Distinguish jwt trust boundaries from legitimate behavior using bounded, policy-aware comparisons.
applicability:
- A compact token candidate is present in an assigned interaction or a JWT validation hypothesis is assigned.
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
  when: A compact token candidate is present in an assigned interaction or a JWT validation hypothesis
    is assigned. while checking prerequisites in an assigned interaction.
  request: JWT trust boundaries reconnaissance
  signals_any:
  - http.jwt
- phases:
  - hypothesis_generation
  when: A compact token candidate is present in an assigned interaction or a JWT validation hypothesis
    is assigned. while designing future tests from recorded observations.
  request: JWT trust boundaries hypothesis generation
  signals_any:
  - http.jwt
- phases:
  - vulnerability_verification
  when: A compact token candidate is present in an assigned interaction or a JWT validation hypothesis
    is assigned. while checking a specific assigned boundary hypothesis.
  request: JWT trust boundaries vulnerability verification
  signals_any:
  - http.jwt
- phases:
  - execution
  when: A compact token candidate is present in an assigned interaction or a JWT validation hypothesis
    is assigned. after a boundary is verified and a minimal demonstration is authorized.
  request: JWT trust boundaries execution
  signals_any:
  - http.jwt
- phases:
  - analysis
  when: A compact token candidate is present in an assigned interaction or a JWT validation hypothesis
    is assigned. while interpreting recorded results and competing explanations.
  request: JWT trust boundaries analysis
  signals_any:
  - http.jwt
sources:
- url: https://www.rfc-editor.org/rfc/rfc8725.txt
  title: jwt-rfc8725
  retrieved_at: '2026-11-06T21:29:21.104917+00:00'
  selection_reason: RFC8725 sections2–3 establish algorithm, key, issuer, audience and cross-JWT substitution
    threats and validation practices.
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/REST_Security_Cheat_Sheet.md
  title: REST Security Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.745034+00:00'
  selection_reason: token verification, key and algorithm trust.
source_coverage: 'Independently selected retrieved references supply mechanism/control background. RFC8725
  sections2–3 establish algorithm, key, issuer, audience and cross-JWT substitution threats and validation
  practices. Limits: Development-oriented guidance supplies mechanism/control background; application
  policy, live exploitability, negative controls and minimal-impact procedure remain original methodology
  requiring target-specific verification. token verification, key and algorithm trust. Limits: Development-oriented
  guidance supplies mechanism/control background; application policy, live exploitability, negative controls
  and minimal-impact procedure remain original methodology requiring target-specific verification. Numbered
  procedures and controls are original knowledge-allowed synthesis, not individually source-verified.
  No completed target execution or affected-version claim is supplied.'
---

# JWT trust boundaries

## Applicability and prerequisites

A compact token candidate is present in an assigned interaction or a JWT validation hypothesis is assigned. Identify the token producer, consumer, protected operation and expected identity. Obtain owned test identities and policy for issuer, audience, purpose, algorithms, keys and time validity. Missing policy remains unknown. Process tokens locally with an available decoder and HTTP client; decoding never verifies a signature. Keep token/key values in sensitive internal artifacts and redact public outputs.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. Inspect the assigned capture for where the token travels: header, cookie, URL or body. Distinguish observed authentication use from an untested assumption; a response-only token may lack an identified consumer.
2. Classify JWS, JWE, unsecured and nested forms. Read available bounded header/claim metadata, noting encrypted or inaccessible claims. Record whether key-selection fields are actually processed rather than assuming from their names.

## Hypothesis generation

1. Using recorded data only, choose plausible branches: integrity, bounded weak-HMAC-key assessment, algorithm/key confusion, untrusted key selection, or issuer/audience/time/purpose enforcement. Do not send requests or search keys in this context.
2. For each branch specify an accepted token/identity baseline, a single mutation, an unrelated-key or malformed-token negative control and the protected result that would discriminate acceptance from reflection. Identify competing cookies, refresh flows and cache state to isolate later.
3. For claim policy prefer legitimately signed wrong-purpose/issuer/audience tokens. A tampered claim with a bad signature cannot isolate claim validation. Algorithm confusion requires obtainable public-key bytes and a compatible verifier; note key-format uncertainty and clock-skew policy explicitly.

## Vulnerability verification

1. Replay an unchanged valid token in an isolated session. Verify the actual identity and one owned protected resource. Remove alternate cookies/credentials and automatic refresh; compare absent, malformed and valid other-owned-identity tokens. If the resource is public or another credential explains access, stop the bypass branch.
2. For integrity, alter one security-relevant claim without regenerating its signature; separately test an unsecured form only where signatures are required by established policy. Re-run the valid baseline afterward.
3. For untrusted key selection, sign a controlled claim with an owned private key and supply its public JWK or approved hosted JWKS where that field is processed. Contrast an unrelated key. A key-fetch callback is not token acceptance; do not embed or host private keys.
4. For algorithm confusion, use the relevant public key's exact candidate byte representation as a symmetric key for one controlled token, comparing unrelated-key and unchanged-asymmetric controls. An algorithm edit alone and failure with one encoding are inconclusive. With symmetric signing, an expressly bounded offline candidate set may test weak keys: reproduce the original MAC before generating a variation; no unbounded guessing.
5. For issuer/audience/purpose compare properly signed tokens from a test issuer or legitimate owned flow. For expiry/not-before use signed times beyond the allowed skew. Without issuance capability, mark that claim branch untested. Confirm only a reproducible prohibited identity/resource effect attributable to the tested token, not an HTTP status or echoed claim.

## Execution

1. After a verified branch and authorization, use the modified token for one harmless read of a protected owned fixture or identity endpoint that demonstrates the exact prohibited privilege. Do not enumerate unrelated data or chain into key-resolution injection.
2. Discard generated tokens and hosted test keys, restore the normal credential context and verify the valid baseline. If any offline secret was recovered, handle it as sensitive evidence and coordinate rotation through the authorized owner.

## Analysis

1. Compare baseline, variant, absent/malformed-token and unrelated-key outcomes. Separate offline cryptographic weakness, key retrieval and live authorization effects; each supports a different conclusion.
2. Explain legitimate unsecured-token contexts, alternate authentication, cache/redirect behavior, clock skew and token-type substitution. Token presence, missing claims or a public key alone prove no bypass. JWE/nested handling needs its own cryptographic prerequisites; do not apply JWS edits blindly.

## Evidence requirements

Retain sensitive exact transformations and token provenance, request/response comparisons, observed identity/resource entitlement, key encoding, time/skew policy and repeat outcomes. Report only the boundary actually demonstrated; methodology sources are separate from runtime evidence.

## Limitations

Rejected variations do not establish all JWT validation is sound. No exhaustive JWE, product-CVE or secondary key-resolution exploit procedure is supplied. Missing stable baselines, keys, test issuers or policy make the relevant branch inconclusive.

## Trigger examples and non-examples

Matching: A compact token candidate is present in an assigned interaction or a JWT validation hypothesis is assigned. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
