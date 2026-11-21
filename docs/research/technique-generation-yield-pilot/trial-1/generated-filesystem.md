---
schema_version: 1
title: Reusable Read-Access Comparison Methodology
purpose: Provide a reusable, source-grounded procedure for comparing observed read
  access outcomes against an established policy using process identity, allowed and
  denied controls, and evidence requirements, without performing target execution.
applicability:
- Linux file read access evaluation
- Permission-based access control review
- Policy vs observed behavior comparison
phases:
- planning
- validation
triggers:
- phases:
  - planning
  - validation
  when: A reviewer needs to compare read access outcomes to an established policy
  request: Describe a reusable read-access comparison using an established policy,
    the actual process identity, allowed and denied controls, and evidence requirements.
  signals_any:
  - read permission policy
  - process identity
  - allowed denied controls
  - evidence requirements
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
  "explanation": "The open(2) man page provides source-backed facts that read access
  requires the caller have read permission on the object and that the kernel reports
  access not allowed when permission is missing. These facts ground policy/prerequisite
  establishment and legitimate vs denied outcome classification for a reusable read-access
  comparison methodology. Process identity resolution, control configuration and evidence
  collection are procedural design choices for the authored guidance and are treated
  as future validation rather than missing technical support.", "authority_sufficient":
  true, "claim_support": [{"claim": "The open system call requires the caller have
  read permission on the object for read access.", "source_urls": ["https://man7.org/linux/man-pages/man2/open.2.html"],
  "authority_basis": "Linux open system-call manual retained excerpt states requires
  that the caller have read permission on the object."}, {"claim": "When requested
  access is not allowed, the kernel reports access denial.", "source_urls": ["https://man7.org/linux/man-pages/man2/open.2.html"],
  "authority_basis": "Linux open system-call manual retained excerpt states The requested
  access to the file is not allowed."}], "authoring_obligations": [], "synthesis":
  "", "gaps": [], "future_validation": ["Establish policy documentation or configuration
  defining which principals may read which objects.", "Confirm actual process identity
  and credentials presented to open.", "Collect baseline allowed and denied open outcomes
  under controlled variation.", "Record kernel error and permission check evidence
  for each test."]}]}'
revision_id: 2f5ec5e3-a617-529a-bcb1-8e10d3b87f30
lineage: generated:587c680d-9aa9-45cb-ad26-eacf4b6512be
origin: generated
source_sha256: 05d8720154079e7a399db35a6b28b1ba4b3abbe74e57e478aebc693793b60ddd
inactive_reason: ''
sentiment_count: 0
sentiment_mean: 0
feedback_reasons: []
---

# Reusable Read-Access Comparison Methodology for File Read Permission

## Purpose
Compare observed read access outcomes to an established read-access policy using process identity, controlled allowed/denied baselines, and evidence collection. The methodology is advisory and does not prove vulnerability, scope, access, or task completion.

## Prerequisites
- Established policy documentation or configuration defining which principals may read which objects and under what conditions. Policy must be established independently of the test, e.g., via documentation, configuration, or independently known authorized/denied baselines.
- Ability to identify the actual process identity and credentials presented to the kernel for an open operation, e.g., UID/GID, effective user, supplementary groups, and any relevant security context.
- Controlled test environment where allowed and denied controls can be exercised without affecting production.
- Logging capability for kernel error reporting and permission checks.

## Source-grounded technical facts
- The Linux `open(2)` system call requires that the caller have read permission on the object for read access.
- When requested access is not allowed, the kernel reports access denial.

These facts ground policy/prerequisite establishment and legitimate vs denied outcome classification. Process identity resolution, control configuration, and evidence collection are procedural design choices for guidance.

## Methodology

### 1. Establish Policy and Scope
1. Document the intended read-access policy: which principals are authorized to read which objects, and which are explicitly denied.
2. Record the policy source: documentation, configuration, or independently known baseline.
3. Define the comparison scope: objects under review and the set of process identities to evaluate.

### 2. Identify Actual Process Identity
1. Determine the process identity that will perform the open operation: effective UID/GID, real UID/GID, supplementary groups, and security context if applicable.
2. Record how identity is resolved by the system for the open call.
3. Note any identity transformation mechanisms in the environment, e.g., setuid, containers, namespaces.

### 3. Define Allowed and Denied Controls
1. **Allowed control**: a principal/object pair that policy states should succeed. Use this as baseline for legitimate behavior.
2. **Denied control**: a principal/object pair that policy states should be rejected. Use this as baseline for prohibited behavior.
3. Ensure controls are independent of the test subject and reflect the established policy.

### 4. Evidence Requirements
For each comparison, collect:
- Process identity presented to open: UID/GID, security context.
- Object path and permission bits relevant to the identity.
- System call invocation parameters, e.g., flags indicating read intent.
- Kernel outcome: success with file descriptor returned, or denial reported by kernel.
- Error reporting evidence, e.g., errno value and textual description from kernel.
- Timestamp and environment context.

Evidence must distinguish legitimate behavior from boundary violations. Do not infer violation from outcome alone.

### 5. Comparison Procedure
1. Baseline allowed control: verify that an authorized principal can read an authorized object per policy. Record evidence of success.
2. Baseline denied control: verify that an unauthorized principal is denied read access to a protected object per policy. Record evidence of denial.
3. Test subject comparison: evaluate the process identity and object against policy, then compare observed outcome to expected outcome derived from policy.
4. Interpretation:
   - Outcome matches policy expectation → consistent with legitimate behavior.
   - Outcome diverges from policy expectation → requires further investigation; do not label as violation without established policy breach.

### 6. Limitations
- Source coverage is bounded to the retained open(2) excerpt. Omitted source text is unknown; mechanisms beyond read permission requirements are not covered.
- Policy establishment, identity resolution, and control configuration are future validation steps, not asserted facts.
- Methodology does not execute against a target and does not prove vulnerability.

## Optional Tool Choices
Any tool capable of observing process identity, file permissions, system call parameters, and kernel error reporting may be used. No specific tool is required.

## Future Validation Checks
- Confirm policy documentation or configuration defining principals and objects.
- Confirm actual process identity and credentials presented to open.
- Collect baseline allowed and denied open outcomes under controlled variation.
- Record kernel error and permission check evidence for each test.
