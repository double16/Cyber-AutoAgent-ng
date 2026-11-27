---
schema_version: 1
title: Filesystem read-policy comparison
purpose: Describe a reusable read-access comparison using an established policy, the
  actual process identity, allowed and denied controls, and evidence requirements.
  No target execution or particular tool is requested.
applicability:
- A declared policy and controlled test subjects are available
phases:
- hypothesis_generation
triggers:
- phases:
  - hypothesis_generation
  when: The declared subject and policy are under review
  request: Describe a reusable read-access comparison using an established policy,
    the actual process identity, allowed and denied controls, and evidence requirements.
    No target execution or particular tool is requested.
  signals_any: []
sources:
- url: https://man7.org/linux/man-pages/man2/open.2.html
  title: Linux open system-call manual
  retrieved_at: ''
  selection_reason: Retained source excerpt contributes the stated technical foundation;
    procedure is original synthesis
active: true
source_coverage: 'Considered 1 fetched documents; selected 1. {"requirements": [{"id":
  "method", "description": "Describe a reusable read-access comparison using an established
  policy, the actual process identity, allowed and denied controls, and evidence requirements.
  No target execution or particular tool is requested.", "kind": "authored_deliverable",
  "origins": []}]}

  Authority sufficient: True; requirement coverage sufficient: True; research ready:
  True. Public technical source; request completeness is assessed on the draft

  Research readiness is permission to draft, not fulfillment or factual proof. All
  recorded concerns remain for authoring and independent critique.

  {"coverage": [{"requirement_id": "method", "source_urls": ["https://man7.org/linux/man-pages/man2/open.2.html"],
  "explanation": "Assess this original procedure against the supplied obligation and
  retained technical facts.", "authority_sufficient": null, "claim_support": [], "authoring_obligations":
  [], "synthesis": "", "gaps": [], "future_validation": []}]}'
---

# Filesystem read-policy comparison

## Purpose and prerequisites
Describe future read-only checks on explicitly selected test files. Establish the intended read
policy independently from approved configuration or an authoritative policy owner. Select an
allowed file and a denied file for the same declared test principal. Do not infer a denial from
ownership alone: establish applicable sharing, ACL, identity, privilege and isolation conditions.
When those conditions are unknown, inspect or establish them before making a security conclusion.

## Procedure
1. Record the process's effective identity and applicable groups, privilege context and filesystem
   namespace. Bind each test to the intended object using stable file metadata and resolved path
   information. Exclude unexpected symlink resolution, identity changes or replacement objects.
2. Confirm from the established effective policy that the same principal should read the allowed
   control and should not read the denied control. If the policy cannot decide either case, record
   the missing prerequisite rather than assuming that access must fail.
3. Under that same verified process context, attempt a read-only open and a small bounded read of
   non-sensitive marker content in the allowed test file. Then perform the same operation on the
   denied test file without changing identity or privilege. Do not create, truncate or modify files.
4. Record both open and read outcomes. Distinguish a missing path or unavailable filesystem from an
   access denial. Repeat the allowed control to detect changes in availability or process context.

## Interpretation and evidence
Reading the allowed file is expected. A denied read supports enforcement only with a working
allowed control and verified file/identity binding. Suspect a boundary violation only if marker
content from the file independently established as denied is returned under the verified principal
and unchanged policy. Reading an owned file or a deliberately shared file is not a violation.
An ambiguous identity, object binding or policy leaves the result inconclusive.

Preserve the policy reference, effective identity and privilege context, relevant sharing/ACL
information, object metadata, exact read-only operations, return values, timestamps and marker
comparison. The source supports the read-permission basis; these original procedural controls are
future checks, not observed exploitation or a requirement for additional reusable methodology.
