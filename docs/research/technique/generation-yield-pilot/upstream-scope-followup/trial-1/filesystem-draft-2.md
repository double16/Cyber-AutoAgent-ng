---
schema_version: 1
title: Reusable Read-Access Comparison Methodology
purpose: Provide a reusable, source-grounded procedure for comparing read-access outcomes
  against an established policy using process identity, allowed and denied controls,
  and evidence requirements to distinguish legitimate access from policy boundary
  violations.
applicability:
- Linux file read access evaluation
- Policy-based access comparison
- Process identity verification
phases:
- planning
- validation
triggers:
- phases:
  - planning
  - validation
  when: A read-access decision needs to be compared against an established policy
  request: Describe a reusable read-access comparison using an established policy,
    the actual process identity, allowed and denied controls, and evidence requirements.
  signals_any:
  - read permission requirement
  - access denied semantics
sources:
- url: https://man7.org/linux/man-pages/man2/open.2.html
  title: Linux open system-call manual
  retrieved_at: ''
  selection_reason: Provides source-backed technical facts that read access to a file
    requires the caller have read permission on the object and that the kernel returns
    an error when requested access is not allowed, which supports establishing policy/prerequisite
    checks and defining legitimate vs denied outcomes for a read-access comparison
    methodology.
active: true
source_coverage: 'Considered 1 fetched documents; selected 1. {"requirements": [{"id":
  "method", "description": "Describe a reusable read-access comparison using an established
  policy, the actual process identity, allowed and denied controls, and evidence requirements.
  No target execution or particular tool is requested.", "kind": "authored_deliverable",
  "origins": []}]}

  Authority sufficient: True; requirement coverage sufficient: True; research ready:
  True. Retained 1 sources. Latest source assessment: The current document is an authoritative
  Linux man page source for the open(2) system call. The retained excerpts provide
  at least one relevant supported technical fact about read permission requirements
  and denial semantics that can be used to ground policy establishment and outcome
  classification in a reusable read-access comparison methodology. Limitations are
  noted for missing mechanisms and unknown omitted text.

  Research readiness is permission to draft, not fulfillment or factual proof. All
  recorded concerns remain for authoring and independent critique.

  {"coverage": [{"requirement_id": "method", "source_urls": ["https://man7.org/linux/man-pages/man2/open.2.html"],
  "explanation": "The retained open(2) man page excerpts provide source-backed facts
  that read access requires the caller have read permission on the object and that
  the kernel returns an error when requested access is not allowed. These facts support
  grounding a reusable methodology in policy establishment and outcome classification
  for read-access comparisons, with process identity, allowed/denied controls and
  evidence requirements treated as procedural design elements derived from the supported
  permission/denial semantics.", "authority_sufficient": true, "claim_support": [{"claim":
  "The Linux open system call requires the caller have read permission on the object
  for read access.", "source_urls": ["https://man7.org/linux/man-pages/man2/open.2.html"],
  "authority_basis": "Linux man-pages open(2) manual is an authoritative reference
  for kernel file access semantics."}, {"claim": "When requested access is not allowed,
  the kernel denies the open request.", "source_urls": ["https://man7.org/linux/man-pages/man2/open.2.html"],
  "authority_basis": "Linux man-pages open(2) manual documents denial semantics for
  file access."}], "authoring_obligations": [], "synthesis": "", "gaps": [], "future_validation":
  ["Establish the specific policy source and how read permission is enforced for the
  target environment", "Confirm actual process identity resolution method used in
  the comparison", "Define concrete allowed and denied control configurations for
  baseline and variation", "Specify evidence to collect for each outcome to distinguish
  legitimate vs prohibited access"]}]}'
---

# Reusable Read-Access Comparison Methodology

## Purpose
Describe how to design a reusable comparison of read-access outcomes to an established policy using process identity, allowed and denied controls, and evidence requirements. The methodology is advisory and does not prove vulnerability, scope, access, or task completion. No target execution is requested.

## Prerequisites
1. Established policy source identified and documented. Policy must specify which process identities are authorized to read which objects under which conditions.
2. Object under test identified with stable path/identifier.
3. Method for observing process identity resolution for the caller and the kernel access decision is known in principle.
4. Baseline environment design where allowed and denied controls can be described without target execution.

## Controls
- **Allowed control**: Configuration where policy states read is permitted for the test process identity.
- **Denied control**: Configuration where policy states read is prohibited for the test process identity.
- **Identity control**: Variation of process identity while keeping object and policy constant.

## Procedure Design

### 1. Establish Policy
1.1 Retrieve authoritative policy documentation, configuration, or independently known authorization baseline for the object.
1.2 Record the explicit read permission requirement for the object and the mapping of process identities to allowed/denied.
1.3 Note limitations: policy may be implicit, environment-specific, or incomplete. Document assumptions.

### 2. Establish Process Identity
2.1 Determine how the actual process identity is resolved in the target environment, e.g., UID/GID, capabilities, SELinux/AppArmor context.
2.2 Record the identity attributes that will be used for comparison.

### 3. Baseline Allowed Outcome Design
3.1 Define allowed control: ensure policy permits read for the chosen identity.
3.2 Describe the hypothetical read-access attempt under controlled conditions and the evidence that would be collected.
3.3 Evidence set to specify:
- Process identity attributes at time of attempt
- Requested access mode
- Kernel return code / error message
- Timestamp and object identifier
3.4 Expected legitimate behavior: access succeeds if policy permits and caller has read permission on the object. Source support: Linux open(2) requires caller have read permission on the object for read access.

### 4. Baseline Denied Outcome Design
4.1 Define denied control: ensure policy prohibits read for the chosen identity.
4.2 Describe the hypothetical read-access attempt under same conditions as step 3 and the evidence that would be collected.
4.3 Collect same evidence set as step 3.3.
4.4 Expected legitimate behavior: access is denied. Source support: Linux open(2) documents that the requested access to the file is not allowed when permission is lacking.

### 5. Identity Variation Comparison Design
5.1 Keep object and policy constant, vary process identity between allowed and denied identities.
5.2 Define comparison matrix:
- Allowed identity + allowed control → success expected
- Denied identity + denied control → denial expected
- Cross combinations to verify identity binding
5.3 Record divergence from expected outcomes as a design artifact for future validation.

### 6. Evidence Requirements
For each hypothetical attempt specify:
- Policy reference and version
- Process identity snapshot
- Object identifier and permission state
- Requested access flags
- Kernel/system response: success or error, error code
- Timestamp and environment context

Evidence must be sufficient to distinguish legitimate behavior from a boundary violation per the established policy.

## Interpretation
- Legitimate: outcome matches policy for the observed process identity and control.
- Boundary violation hypothesis: outcome contradicts established policy for the observed identity. Requires future validation with additional controls and evidence.

## Limitations
Retained source is a bounded excerpt of Linux open(2) man page. Omitted text is unknown. Source provides support for read permission requirement and denial semantics only. It does not cover specific enforcement mechanisms, identity resolution methods, or policy configuration details. Those must be established per environment.

## Future Validation
- Establish the specific policy source and how read permission is enforced for the target environment
- Confirm actual process identity resolution method used in the comparison
- Define concrete allowed and denied control configurations for baseline and variation
- Specify evidence to collect for each outcome to distinguish legitimate vs prohibited access

*Attribution: Linux open system-call manual, man7.org/linux/man-pages/man2/open.2.html. Retained excerpts: "requires that the caller have read permission on the object"; "The requested access to the file is not allowed". Scope is bounded; omitted source text unknown.*
