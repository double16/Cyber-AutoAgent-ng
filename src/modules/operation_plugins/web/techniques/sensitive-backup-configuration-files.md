---
schema_version: 1
title: Sensitive backup/configuration files
purpose: Test whether an already identified backup or configuration artifact exposes nonpublic application
  data over the web.
applicability:
- A mapped response or assigned candidate URL suggests a backup, configuration, archive or source artifact.
- The relevant operation, identities, data and side effects are within the resolved assessment scope.
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
  when: A mapped response or assigned candidate URL suggests a backup, configuration, archive or source
    artifact. The assigned work needs method-specific prerequisite checks.
  request: Sensitive backup/configuration files reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: Recorded observations concern sensitive backup/configuration files and the assigned work is designing
    future discriminating tests.
  request: Sensitive backup/configuration files hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An explicit sensitive backup/configuration files hypothesis and its prerequisites are available
    for controlled verification.
  request: Sensitive backup/configuration files vulnerability verification
  signals_any: []
- phases:
  - execution
  when: The relevant sensitive backup/configuration files boundary has been verified and an authorized
    minimal demonstration with test data is required.
  request: Sensitive backup/configuration files execution
  signals_any: []
- phases:
  - analysis
  when: Recorded sensitive backup/configuration files observations or comparison outcomes require interpretation
    of policy, controls and remaining uncertainty.
  request: Sensitive backup/configuration files analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Secrets_Management_Cheat_Sheet.md
  title: Secrets Management Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.918578+00:00'
  selection_reason: secret exposure and secret lifecycle.
- url: https://raw.githubusercontent.com/OWASP/wstg/v4.2/document/4-Web_Application_Security_Testing/02-Configuration_and_Deployment_Management_Testing/04-Review_Old_Backup_and_Unreferenced_Files_for_Sensitive_Information.md
  title: wstg-backups
  retrieved_at: '2026-11-07T10:33:40.381069+00:00'
  selection_reason: WSTG-CONF-04 describes backup/unreferenced file exposures and their sensitivity consequences.
source_coverage: 'Selected retrieved references supply mechanism/control background: secret exposure and
  secret lifecycle. WSTG-CONF-04 describes backup/unreferenced file exposures and their sensitivity consequences.
  Limitations: Development-oriented guidance supplies mechanism/control background; application policy,
  live exploitability, negative controls and minimal-impact procedure remain original methodology requiring
  target-specific verification. Original controlled procedures and interpretation are knowledge-allowed
  synthesis, not individually source-verified. No generic reference establishes target applicability or
  exploitation.'
---

# Sensitive backup/configuration files

## Applicability and prerequisites

A mapped response or assigned candidate URL suggests a backup, configuration, archive, source-control
metadata, directory listing, or other nonpublic artifact.

Use only assigned candidate locations and the resolved network scope. Establish expected audience
and a minimal-content retrieval limit. Prefer a planted synthetic marker or owner-confirmed
classification; never exercise exposed production credentials.

Use available HTTP replay, browser or protocol capabilities appropriate to the actual channel; no
named tool is required. Preserve configured HTTP/HTTPS proxy use where the tool supports it. Work
only on the assigned interaction; this method does not authorize general discovery or new
hosts/ports.

## Accepted baselines and controls

The intended public resource is reachable, and a nonexistent sibling candidate produces the
application's normal missing-resource behavior.

## Reconnaissance

1. Inspect the already assigned captures and contract for this method's input, identity, state and
   observation channel. Record supported prerequisites separately from unknown conditions; do not
   scan for new endpoints.
2. Use only assigned candidate locations and the resolved network scope. Establish expected audience
   and a minimal-content retrieval limit. Prefer a planted synthetic marker or owner-confirmed
   classification; never exercise exposed production credentials.
3. Identify which baseline and negative control can be performed within scope. If the policy, test
   data or required capability is missing, preserve that limitation instead of assuming a vulnerable
   condition.

## Hypothesis generation

1. Use recorded observations only in this section; do not send new target requests or execute the
   future tests.
2. Interpret recorded candidate metadata and previews. Include only assigned candidates such as
   backup/configuration files, source-control metadata, or a directory listing. Design a future
   minimal read comparing content identity and access context; a filename, extension, status 200 or
   download header alone does not establish sensitive exposure.
3. Write the expected permitted, prohibited and inconclusive outcomes and the evidence needed to
   distinguish them. State unresolved prerequisites explicitly; absent evidence does not confirm
   either security or vulnerability.

## Vulnerability verification

1. Request the assigned candidate with the least privileged relevant context and preserve explicit
   status, redirects, media type and bounded content.
2. Compare against a nonexistent sibling and the public application shell to distinguish soft-404,
   default route and generic denial content.
3. Identify concrete nonpublic content such as synthetic configuration markers, source fragments,
   repository metadata, or backup record structure. A listing is an exposure only when it reveals
   resources whose audience is restricted. If range requests are ignored, enforce a client-side byte
   limit instead of downloading a large archive.
4. Confirm the candidate's audience excludes the tested actor and compare an authorized view or
   owner-confirmed fixture when available. Stop immediately after sufficient classification; do not
   validate discovered secrets by using them.

## Execution

1. Apply only after the relevant boundary and operation authorization have been established. Reuse
   the validated comparison and avoid unrelated chaining or additional data collection.
2. Retrieve only the minimum synthetic or redacted fragment needed to establish unauthorized
   disclosure. Remove planted test artifacts through the authorized owner and verify the candidate
   no longer exposes them.
3. Verify restoration through the relevant authoritative channel. If cleanup cannot be confirmed,
   stop further changes and preserve that limitation.

## Analysis

1. Compare the observed result with the established policy and the local controls above. A
   successful request, available artifact, changed response or tool execution establishes
   availability only; confirm the precise protected data or effect needed for the claim.
2. Classify the tested mechanism as supported, refuted for the tested case, or inconclusive. Keep
   the tested actor, object, state and channel explicit, and preserve competing explanations and
   untested variants.

### False positives and competing explanations

Public example configuration, documentation archives, intentionally public source-control metadata,
directory listings of public assets and SPA shells can look like sensitive artifacts without
disclosing protected data. An expired or dummy credential string needs classification rather than a
claim of active compromise. Public configuration exposing database details or secrets is also an
excessive-data-exposure question; classify the exact data and do not attempt to use it.

## Evidence requirements

Retain candidate provenance, confidentiality basis, low-privilege request/status, bounded
identifying fragment and nonexistent/public-shell controls.

For hypothesis work, specify these as future evidence requirements rather than inventing completed
outcomes. Preserve secrets only in the appropriate sensitive evidence context; redact secret values
from report text and shareable records.

## Limitations and trigger non-examples

This method does not enumerate filesystem paths or inspect local server files. Inability to classify
encrypted archives or truncated content remains an explicit limitation.

Do not activate this method merely from a vulnerability name, a tool's success status or an
incidental filename. A semantic match recommends guidance; it does not satisfy prerequisites or
prove a finding. Equivalent tooling may be used, and unavailable controls remain explicit gaps.
