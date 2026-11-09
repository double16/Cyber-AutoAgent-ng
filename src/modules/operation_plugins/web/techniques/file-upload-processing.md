---
schema_version: 1
title: File upload, storage and downstream processing
purpose: Distinguish file upload, storage and downstream processing from legitimate behavior using bounded,
  policy-aware comparisons.
applicability:
- An assigned workflow accepts files and stores, serves, extracts or converts them.
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
  when: An assigned workflow accepts files and stores, serves, extracts or converts them. while checking
    prerequisites in an assigned interaction.
  request: File upload, storage and downstream processing reconnaissance
  signals_any:
  - http.multipart
- phases:
  - hypothesis_generation
  when: An assigned workflow accepts files and stores, serves, extracts or converts them. while designing
    future tests from recorded observations.
  request: File upload, storage and downstream processing hypothesis generation
  signals_any:
  - http.multipart
- phases:
  - vulnerability_verification
  when: An assigned workflow accepts files and stores, serves, extracts or converts them. while checking
    a specific assigned boundary hypothesis.
  request: File upload, storage and downstream processing vulnerability verification
  signals_any:
  - http.multipart
- phases:
  - execution
  when: An assigned workflow accepts files and stores, serves, extracts or converts them. after a boundary
    is verified and a minimal demonstration is authorized.
  request: File upload, storage and downstream processing execution
  signals_any:
  - http.multipart
- phases:
  - analysis
  when: An assigned workflow accepts files and stores, serves, extracts or converts them. while interpreting
    recorded results and competing explanations.
  request: File upload, storage and downstream processing analysis
  signals_any:
  - http.multipart
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/File_Upload_Cheat_Sheet.md
  title: File Upload Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.387751+00:00'
  selection_reason: extension, media type, storage and processing risks.
source_coverage: 'Independently selected retrieved references supply mechanism/control background. extension,
  media type, storage and processing risks. Limits: Development-oriented guidance supplies mechanism/control
  background; application policy, live exploitability, negative controls and minimal-impact procedure
  remain original methodology requiring target-specific verification. Numbered procedures and controls
  are original knowledge-allowed synthesis, not individually source-verified. No completed target execution
  or affected-version claim is supplied.'
---

# File upload, storage and downstream processing

## Applicability and prerequisites

An assigned workflow accepts files and stores, serves, extracts or converts them. Establish allowed types, sizes, filenames, storage audience and processing behavior. Use small synthetic files, owned accounts/objects and a reversible test workflow. Define a small size/file-count envelope; archives, conversions and callbacks need their own permission. Capture upload, job and retrieval channels with equivalent HTTP/browser tools, without direct target filesystem access.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. Trace the assigned file through acceptance, storage, asynchronous processing and retrieval. Record filename, declared media type, actual bytes, processing job and served Content-Type/disposition/origin; an upload success response proves only acceptance.
2. Identify expected filename/root, type/content, tenant/access and processor external-resource policies. Distinguish intentionally supported active content or document links from forbidden interpreter/network behavior. Note whether conversion rewrites or merely stores the input.

## Hypothesis generation

1. Using recorded workflow evidence plan separate future tests for extension/media/content agreement, storage/filename boundaries, retrieval authorization and converter/parser behavior. Change only one layer at a time.
2. Define a valid tiny file, a same-size invalid/mismatched file, a harmless unique output marker and a forbidden-policy comparison. For archives use a few tiny members and an owner-provided marker fixture; never plan expansion bombs.
3. For processing, specify how a generated output, approved callback or restricted resource marker would distinguish actual processor execution from stored bytes or browser rendering. Do not upload, queue jobs or retrieve test content during hypothesis design.

## Vulnerability verification

1. Upload a valid small fixture and follow its complete owned workflow to final storage, conversion and retrieval. Verify actual content, size, owner/tenant and state; establish a valid processing job and cleanup operation.
2. Compare one filename/extension change while keeping bytes and declared type fixed, then one declared type change with bytes/name fixed. Compare a genuinely invalid small file and a correctly typed valid counterpart. Acceptance of a harmless mismatch is not automatically exploitable; determine what downstream trust decision it affects.
3. Test filename/root or duplicate-name behavior only against test-owned objects. For archives, inspect the resulting accessible owned outputs and compare normal names with the one approved boundary case. Do not target existing files, arbitrary paths or high expansion ratios.
4. Retrieve the uploaded/derived object as its owner, a separately owned actor lacking access under established policy and, only where assigned, without credentials. Confirm actual synthetic bytes rather than guessing access from a URL or HTTP200. Public hosting may be intentional.
5. For a converter/parser hypothesis, vary one harmless active feature in the synthetic document: a unique approved external reference, inert template construct or owner-provided nonsecret processing marker. Compare a feature-free file and invalid-feature control. Require actual derived output or attributable server-side effect, not an upload receipt, thumbnail delay or browser-initiated fetch.
6. If client-side execution is the assigned consequence, render only in owned browser sessions and demonstrate a harmless local marker in the actual served origin. If server processing is claimed, browser execution is not sufficient. Re-run the valid file workflow after each branch and stop at the minimal verified boundary.

## Execution

1. Demonstrate one verified storage, access, served-content or processor boundary with a tiny nonsecret fixture. Do not upload web shells, trigger resource exhaustion, overwrite unrelated objects or expose active files to other users.
2. Delete uploaded files, derived previews, extracted members and queued/retry jobs through normal authorized interfaces. Remove callback content and confirm both original and derived object URLs no longer expose test data where deletion is expected.

## Analysis

1. Separate accepted file metadata, durable storage, retrieval permission, client execution and server processor behavior. An extension or media-type bypass matters only through an established forbidden effect.
2. Explain intentionally public uploads, generated safe filenames, converter stripping, quarantine delays, cached previews and archive normalization. A successful re-encode does not prove every processor path safe; failed rendering does not prove the file was never stored.

## Evidence requirements

Preserve synthetic file hashes/bytes and size, name/type variations, upload and job IDs, derived artifact content, owner/nonowner controls, processing attribution, served browser origin and cleanup results. Record the exact layer and policy crossed, not a generic unrestricted-upload label.

## Limitations

No exhaustive file-format fuzzing, parser-CVE exploit, decompression bomb or filesystem inspection is provided. Unobservable async jobs or inaccessible derived outputs limit attribution. Equivalent capabilities may inspect the workflow without a named uploader/scanner.

## Trigger examples and non-examples

Matching: An assigned workflow accepts files and stores, serves, extracts or converts them. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
