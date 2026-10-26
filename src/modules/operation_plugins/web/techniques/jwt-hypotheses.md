---
schema_version: 1
title: JWT trust-boundary hypothesis generation
purpose: Turn observed JWT handling into grounded future verification plans.
applicability:
- An assigned HTTP interaction contains a compact JWT or JWE candidate.
phases:
- hypothesis_generation
active: true
triggers:
- phases:
  - hypothesis_generation
  when: A compact JWT or JWE appears anywhere in an HTTP request or response, including cookies, URLs,
    headers, or body fields, while generating hypotheses.
  signals_any:
  - http.jwt
  request: JWT trust-boundary hypothesis generation
sources:
- url: https://www.rfc-editor.org/rfc/rfc7519.html
  title: 'RFC 7519: JSON Web Token'
  retrieved_at: '2026-10-26'
  selection_reason: Claim semantics and signed, encrypted, and unsecured token distinctions.
- url: https://www.rfc-editor.org/rfc/rfc8725.html
  title: 'RFC 8725: JWT Best Current Practices'
  retrieved_at: '2026-10-26'
  selection_reason: Algorithm, key, issuer, audience, and token-type trust boundaries.
- url: https://portswigger.net/web-security/jwt
  title: JWT attacks | Web Security Academy
  retrieved_at: '2026-10-26'
  selection_reason: Practical signature and attacker-controlled key-selection assessment cases.
- url: https://portswigger.net/web-security/jwt/algorithm-confusion
  title: Algorithm confusion attacks | Web Security Academy
  retrieved_at: '2026-10-26'
  selection_reason: Distinguishes key-format prerequisites from merely observing an asymmetric algorithm.
source_coverage: RFCs define trust requirements; PortSwigger supplies JWS assessment cases. This is not
  an exhaustive JWE, library-specific, or CVE verification guide.
---

# JWT trust-boundary hypothesis generation

## Prerequisites and scope

Use assigned request/response evidence and access policy. Token presence alone establishes neither
identity nor weakness. Keep exact tokens in sensitive evidence; decode locally, never externally.
This technique designs future tests and does not execute verification requests or key searches.

## Procedure

1. Locate the token field, producer, consumer, and protected operation. Record whether its use for
   authentication or authorization is observed, inferred, or unknown. A response-only token may not
   yet have an identified consumer.
2. Inspect bounded local header/claim metadata. Distinguish JWS, JWE, unsecured, and nested tokens;
   decoding does not verify authenticity, and encrypted claims may be inaccessible.
3. Record expected issuer, audience, token purpose, permitted algorithms, trusted keys, and time
   policy from evidence. Unknown policy stays an explicit prerequisite, not a guessed vulnerability.
4. Select plausible future branches rather than mechanically proposing every attack:

   | Observation or prerequisite | Candidate question |
   | --- | --- |
   | Token influences protected behavior | Are altered signatures or unsigned tokens rejected? |
   | Symmetric signing | Is there evidence justifying a bounded offline weak-key check? |
   | Asymmetric signing and obtainable public key | Can algorithm/key confusion occur? |
   | `jwk`, `jku`, or `kid` is processed | Can untrusted input select verification keys? |
   | Known issuer, audience, purpose, or time contract | Is that specific boundary enforced? |

5. For each candidate, specify evidence references, the protected operation, required test identities,
   unmet prerequisites, an accepted baseline, one controlled variation, expected rejection, and an
   independent negative control. Describe the observable identity or access result needed to decide.
6. Hand actionable candidates to vulnerability verification using the lookup request
   "JWT trust-boundary vulnerability verification". If evidence is insufficient, hand off a bounded
   information-gathering prerequisite or a reasoned no-hypothesis disposition.

## Interpretation, controls, and limits

A public key, readable claim, algorithm name, missing claim, or header name alone is no finding.
Algorithm confusion additionally depends on verifier behavior and exact key representation. A
legitimate token for the wrong audience/purpose is preferable to a tampered token when testing claim
policy: failed integrity would confound the result. Time tests must account for allowed clock skew.

A useful proposal identifies how authentication from other cookies or headers will be isolated and
how public responses, cached pages, redirects, or reflected claim text will be excluded as false
positives. With no usable baseline, identify the missing prerequisite instead of predicting success.
JWE cryptography, product-specific CVEs, and secondary injection chains require separate guidance.
JWT capabilities are tool-independent; do not assume a named parser or request tool is installed.
