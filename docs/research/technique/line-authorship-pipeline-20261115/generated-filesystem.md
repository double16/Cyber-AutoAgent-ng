---
schema_version: 1
title: Reusable Read-Access Comparison via Policy, Identity and Controls
purpose: Provide a reusable methodology for comparing an established read-access policy
  against the actual process identity and observed access outcomes using allowed and
  denied controls, with explicit evidence requirements.
applicability:
- Unix-like file systems
- POSIX permission models
- Access-control audits
phases:
- planning
- validation
triggers: []
sources:
- url: https://man7.org/linux/man-pages/man2/open.2.html
  title: Linux open system-call manual
  retrieved_at: ''
  selection_reason: Provides authoritative Linux kernel documentation that a successful
    open for reading requires the caller have read permission on the object and that
    the kernel returns an error when requested access is not allowed, which supports
    establishing the permission policy prerequisite for a read-access comparison.
active: true
source_coverage: 'Considered 1 fetched documents; selected 1. {"requirements": [{"id":
  "method", "description": "Describe a reusable read-access comparison using an established
  policy, the actual process identity, allowed and denied controls, and evidence requirements.
  No target execution or particular tool is requested.", "kind": "authored_deliverable",
  "grounding": "knowledge_allowed", "origins": []}]}

  Required-source obligations ready: True; references selected: 1. Retained 1 sources.
  Latest source assessment: The man page is an authoritative primary source for the
  open(2) syscall semantics. The retained excerpts supply a supported technical fact
  about read permission being required and access being denied when not allowed, which
  can be used to ground policy/prerequisite establishment in a reusable read-access
  comparison methodology. The contribution is limited to permission semantics; process
  identity handling, allowed/denied control construction, and evidence collection
  procedures are not covered by the bounded excerpt and remain future checks.

  Knowledge-allowed content is not source-verified. Reference readiness does not establish
  document completeness or correctness.

  {"coverage": [{"requirement_id": "method", "source_urls": [], "explanation": "Model
  knowledge and reasoning are allowed; this requirement is not source-verified.",
  "authority_sufficient": null, "claim_support": [], "authoring_obligations": [],
  "synthesis": "", "gaps": [], "future_validation": []}]}'
revision_id: 66c2fa19-a21e-521a-9338-8ba36155222c
lineage: generated:d094b75b-8121-42a8-b29a-a364052c3456
origin: generated
source_sha256: 3403c0ac6ced134ea390dcf96859a81e9c10369a6988923ee607ddcfd8379c12
inactive_reason: ''
sentiment_count: 0
sentiment_mean: 0
feedback_reasons: []
---

# Reusable Read-Access Comparison via Policy, Identity and Controls

## Purpose
Compare an established read-access policy for an object against the actual process identity and observed access outcomes using deliberately constructed allowed and denied controls. The method produces evidence that can be interpreted against policy without asserting a violation.

## Prerequisites

1. **Policy establishment.** Obtain an authoritative, object-specific read-access policy. Sources may include documentation, configuration, or independently known authorized and denied baselines. Record the policy statement, scope, and effective date.
2. **Object identification.** Identify the object by a stable, policy-relevant identifier, e.g., absolute path or inode. Record object metadata.
3. **Process identity definition.** Define how process identity is determined for the environment, e.g., real UID/GID, effective UID/GID, supplementary groups, SELinux/AppArmor context. Record the identity attributes that the policy references.
4. **Access semantics.** Confirm the read-access semantics for the operation under test. For POSIX open, read permission is required on the object and access is denied when not allowed.
5. **Test environment isolation.** Ensure tests do not affect production data. Use copies or dedicated test objects where possible.

## Controls

### Allowed control
Construct a process with an identity explicitly permitted by the established policy for read access to the object.
- Create a test principal or run under an account whose identity matches the allowed set in the policy.
- Verify identity attributes before the access attempt.

### Denied control
Construct a process with an identity explicitly denied by the established policy for read access to the object.
- Create a test principal or run under an account whose identity is outside the allowed set.
- Verify identity attributes before the access attempt.

## Procedure

1. **Baseline identity capture.**
   - Record the process identity attributes that will be used for the test.
   - Evidence: process listing, `id` output, audit log entry showing UID/GID/context at test time.

2. **Policy baseline documentation.**
   - Record the policy statement that defines which identities may read the object and which may not.
   - Evidence: policy document excerpt, configuration snippet, or configuration management record with timestamp.

3. **Allowed control test.**
   - Execute a read-access attempt using the allowed identity control.
   - Capture the outcome: success or failure, return code, system error.
   - Evidence: command invocation record, timestamp, identity snapshot, outcome log.
   - Expected legitimate behavior under policy: access succeeds if the policy permits read for that identity.

4. **Denied control test.**
   - Execute a read-access attempt using the denied identity control.
   - Capture the outcome: success or failure, return code, system error.
   - Evidence: command invocation record, timestamp, identity snapshot, outcome log.
   - Expected legitimate behavior under policy: access is denied.

5. **Comparison.**
   - Compare observed outcomes to policy.
   - Allowed control should match allowed outcome; denied control should match denied outcome.
   - Document any deviation as a future investigation item, not as a confirmed violation.

## Evidence Requirements

- Identity evidence: UID/GID/context before each attempt.
- Policy evidence: authoritative source and version for the allowed/denied sets.
- Object evidence: identifier and metadata at test time.
- Access evidence: timestamped log of the attempt, command or syscall used, return code or error string.
- Control evidence: proof that the allowed and denied identities were constructed per policy.

## Interpretation

- Legitimate behavior: outcomes consistent with the established policy and identity.
- Boundary concern: outcomes inconsistent with policy require additional verification, e.g., policy misinterpretation, caching, or identity mapping errors.
- Do not infer a violation from a single observation; require both controls and policy alignment.

## Limitations and Future Checks

- Policy may change over time; re-establish policy before reuse.
- Process identity mechanisms vary by OS and container runtime; confirm mapping.
- Permission checks may be mediated by ACLs, capabilities, or mandatory access control beyond basic POSIX bits.
- The retained source confirms read permission is required and access is denied when not allowed, but does not cover process identity handling, control construction, or evidence collection procedures.

## Optional Tool Choices

- Identity inspection: standard user/group listing utilities.
- Access testing: POSIX open/read primitives or equivalent language wrappers.
- Logging: system audit logs, shell history with timestamps.

Note: This is a reusable methodology. No target execution or specific tool is required.
