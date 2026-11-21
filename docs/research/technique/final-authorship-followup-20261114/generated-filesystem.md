---
schema_version: 1
title: Reusable Read-Access Comparison via Policy-Identity Baseline
purpose: Provide a reusable methodology to compare an established read-access policy
  against the actual process identity and observed access outcomes, with explicit
  allowed and denied controls and evidence requirements.
applicability:
- Linux POSIX file access
- process identity verification
- access control auditing
phases:
- assessment
- validation
triggers:
- phases:
  - assessment
  when: A read-access decision needs to be evaluated against a documented policy rather
    than assumed.
  request: Describe a reusable read-access comparison using an established policy,
    the actual process identity, allowed and denied controls, and evidence requirements.
  signals_any: []
sources:
- url: https://man7.org/linux/man-pages/man2/open.2.html
  title: Linux open system-call manual
  retrieved_at: ''
  selection_reason: Provides authoritative Linux kernel documentation that a successful
    open for reading requires the caller have read permission on the object and that
    the kernel returns an error when requested access is not allowed, supporting policy
    establishment for read-access checks.
active: true
source_coverage: 'Considered 1 fetched documents; selected 1. {"requirements": [{"id":
  "method", "description": "Describe a reusable read-access comparison using an established
  policy, the actual process identity, allowed and denied controls, and evidence requirements.
  No target execution or particular tool is requested.", "kind": "authored_deliverable",
  "grounding": "knowledge_allowed", "origins": []}]}

  Required-source obligations ready: True; references selected: 1. Retained 1 sources.
  Latest source assessment: The man page is an authoritative primary source for the
  open(2) semantics used in Linux. The retained excerpts supply a supported technical
  fact about read permission prerequisites and the ''access not allowed'' error condition,
  which can support original procedural synthesis for establishing policy and interpreting
  outcomes. The contribution is limited to permission semantics; process identity
  verification, allowed/denied control design, and evidence requirements must be derived
  from model knowledge and remain future checks. No prior source contributions are
  present.

  Knowledge-allowed content is not source-verified. Reference readiness does not establish
  document completeness or correctness.

  {"coverage": [{"requirement_id": "method", "source_urls": [], "explanation": "Model
  knowledge and reasoning are allowed; this requirement is not source-verified.",
  "authority_sufficient": null, "claim_support": [], "authoring_obligations": [],
  "synthesis": "", "gaps": [], "future_validation": []}]}'
revision_id: d86b917f-4633-5f01-b20b-14835e721ab8
lineage: generated:0f46feaf-8403-499f-b342-ca2ef2c0f797
origin: generated
source_sha256: 32dc86874acd7c49b4438c45a33deaf493c8c96857ada2d311a097e2a9c66ed4
inactive_reason: ''
sentiment_count: 0
sentiment_mean: 0
feedback_reasons: []
---

# Reusable Read-Access Comparison via Policy-Identity Baseline<|message|>## Purpose and Scope  This technique describes how to build a reusable comparison between an established read-access policy and the actual process identity that attempts access. It defines how to establish allowed and denied controls, what evidence to collect, and how to interpret outcomes without performing target execution.  The method is applicable to POSIX-like file access where the kernel requires the caller have read permission on the object, and returns an access-not-allowed condition when the permission check fails.  ## Prerequisites  1. **Established Policy Artifact**  - Documented policy stating which process identities are authorized to read which objects, and which identities are explicitly denied.  - Policy source: configuration, IAM manifest, or organizational access matrix. Record policy version and retrieval timestamp.  - Future check: verify policy is current and enforced by the system under test.  2. **Process Identity Definition**  - Identity attributes to be compared: UID/GID, process name, executable path, SELinux/AppArmor context, container ID.  - Method to obtain identity independently of the access attempt, e.g., process listing or audit record.  3. **Object Definition**  - Canonical object identifier: absolute path, inode, device.  - Baseline permission state: owner, group, mode bits, ACLs. Record at comparison time.  4. **Controls**  - Allowed control: a process identity known to be authorized per policy attempting read on the object.  - Denied control: a process identity known to be unauthorized per policy attempting read on the same object.  - Baseline control: same identity and object with no change to policy or permissions.  ## Procedure  1. Establish Policy Baseline  - Extract from policy artifact the set of allowed identities A = {id | policy permits read} and denied identities D = {id | policy denies read}.  - Record policy citation, version, and effective date.  - Future check: confirm policy enforcement mechanism is active.  2. Capture Object State  - Record object path, inode, owner, group, mode, ACLs.  - Record filesystem mount options relevant to access, e.g., noexec, nosuid.  3. Determine Actual Process Identity  - Identify the process that will be evaluated: UID, GID, effective IDs, executable, context.  - Collect identity via independent source, not via the access attempt itself.  - Evidence required: process listing snapshot with timestamp, parent-child relationship, command line.  4. Map Identity to Policy Decision  - Compare actual identity attributes to A and D.  - Decision categories:  - Identity ∈ A → policy expects allow  - Identity ∈ D → policy expects deny  - Identity ∉ A ∪ D → policy undefined; do not interpret as violation.  5. Define Allowed and Denied Controls  - Allowed control: use an identity from A with the same object and permission state. Expected outcome per policy: access permitted.  - Denied control: use an identity from D with the same object and permission state. Expected outcome per policy: access not permitted.  - Controls must isolate identity as the variable; keep object state, time, and environment constant.  6. Evidence Requirements for Comparison  - Policy evidence: artifact excerpt showing allow/deny rule for the identity-object pair.  - Identity evidence: timestamped process record showing UID/GID/context matching the evaluated process.  - Object evidence: timestamped permission snapshot showing mode/ACL at time of evaluation.  - Outcome evidence: system call result classification, e.g., success vs. access-not-allowed error, with timestamp correlation to identity and object snapshots.  - Chain of custody: link policy version → identity record → object snapshot → outcome record via timestamps.  7. Interpretation Rules  - Legitimate behavior: outcome matches policy decision for the mapped identity.  - Boundary violation hypothesis: outcome contradicts policy decision for a mapped identity, e.g., denied identity succeeds or allowed identity fails without a documented exception.  - Do not label undefined identity outcomes as violations.  - Permission semantics: on Linux, a read attempt requires the caller have read permission on the object; otherwise the kernel returns an access-not-allowed condition. This is a supported technical fact for interpreting outcomes, not proof of policy enforcement.  ## Limitations and Future Checks  - Policy establishment is external to this technique; an undocumented or unenforced policy cannot support a violation claim.  - Process identity spoofing or privilege escalation changes the mapping; verify identity integrity via independent audit.  - Time-of-check/time-of-use changes to permissions or policy between snapshot and access invalidate the comparison.  - This method describes comparison design only; no target execution or specific tool is required.  ## Optional Tool Choices  - Policy review: document viewer, configuration management database.  - Identity capture: process listing utilities, audit logs.  - Object state capture: stat, getfacl equivalents.  - Evidence correlation: timestamped logging and log aggregation.  All tool choices are illustrative; any equivalent capability may be used.
