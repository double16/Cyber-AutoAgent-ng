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
- url: https://www.rfc-editor.org/rfc/rfc7578.html
  title: 'RFC 7578: Returning Values from Forms: multipart/form-data'
  retrieved_at: '2026-11-13T11:31:54.212625+00:00'
  selection_reason: Boundary and part metadata, multiple files and preservation of repeated field names and order.
- url: https://developer.mozilla.org/en-US/docs/Web/API/XMLHttpRequest_API/Using_FormData_Objects
  title: Using FormData Objects
  retrieved_at: '2026-11-13T11:31:54.212625+00:00'
  selection_reason: Browser-generated multipart boundaries and explicit file names with FormData.
source_coverage: 'Independently selected retrieved references supply mechanism/control background. extension,
  media type, storage and processing risks. Limits: Development-oriented guidance supplies mechanism/control
  background; application policy, live exploitability, negative controls and minimal-impact procedure
  remain original methodology requiring target-specific verification. Numbered procedures and controls
  are original knowledge-allowed synthesis, not individually source-verified. No completed target execution
  or affected-version claim is supplied. RFC 7578 supplies multipart encoding and repeated-field semantics;
  MDN supplies FormData construction guidance. Size/count comparisons and security interpretations
  are original methodology, not protocol-mandated application policies.'
---

# File upload, storage and downstream processing

## Applicability and prerequisites

An assigned workflow accepts files and stores, serves, extracts or converts them. Establish allowed types, sizes, filenames, storage audience and processing behavior. Use small synthetic files, owned accounts/objects and a reversible test workflow. Define a small size/file-count envelope; archives, conversions and callbacks need their own permission. Capture upload, job and retrieval channels with equivalent HTTP/browser tools, without direct target filesystem access.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. Trace the assigned file through acceptance, storage, asynchronous processing and retrieval. Record filename, declared media type, actual bytes, processing job and served Content-Type/disposition/origin; an upload success response proves only acceptance.
2. Identify expected filename/root, type/content, tenant/access and processor external-resource policies. Distinguish intentionally supported active content or document links from forbidden interpreter/network behavior. Note whether conversion rewrites or merely stores the input.

3. For multipart workflows, record the client-declared method, file-field names, filename metadata and required text parts. Client declarations support future checks; server acceptance, authentication and storage behavior remain unestablished until observed.

## Hypothesis generation

1. Using recorded workflow evidence plan separate future tests for extension/media/content agreement, storage/filename boundaries, retrieval authorization and converter/parser behavior. Change only one layer at a time.
2. Define a valid tiny file, a same-size invalid/mismatched file, a harmless unique output marker and a forbidden-policy comparison. For archives use a few tiny members and an owner-provided marker fixture; never plan expansion bombs.
3. For processing, specify how a generated output, approved callback or restricted resource marker would distinguish actual processor execution from stored bytes or browser rendering. Do not upload, queue jobs or retrieve test content during hypothesis design.

4. For multipart size/count hypotheses, distinguish per-file, aggregate request and file-count limits. Plan below/at/above comparisons only when an established limit fits the authorized envelope; include zero-byte files where relevant. Unknown limits remain future checks, not permission to increase load.
5. For repeated-field hypotheses, plan a valid single-part control and a few repeated parts with distinct harmless markers. Preserve part order and duplicates in the test client; compare which values are validated, stored or processed.

## Vulnerability verification

1. For multipart uploads, establish a valid small baseline with observed field names, required metadata and an explicit file name. Let a multipart client generate matching body delimiters and the Content-Type boundary; when using browser FormData, do not manually set that header. Keep malformed-framing probes separate from the valid baseline. Upload a valid small fixture and follow its complete owned workflow to final storage, conversion and retrieval. Verify actual content, size, owner/tenant and state; establish a valid processing job and cleanup operation.
2. Compare one filename/extension change while keeping bytes and declared type fixed, then one declared type change with bytes/name fixed. Compare a genuinely invalid small file and a correctly typed valid counterpart. Acceptance of a harmless mismatch is not automatically exploitable; determine what downstream trust decision it affects.
3. Test filename/root or duplicate-name behavior only against test-owned objects. For archives, inspect the resulting accessible owned outputs and compare normal names with the one approved boundary case. Do not target existing files, arbitrary paths or high expansion ratios.
4. Retrieve the uploaded/derived object as its owner, a separately owned actor lacking access under established policy and, only where assigned, without credentials. Confirm actual synthetic bytes rather than guessing access from a URL or HTTP200. Public hosting may be intentional.
5. For a converter/parser hypothesis, vary one harmless active feature in the synthetic document: a unique approved external reference, inert template construct or owner-provided nonsecret processing marker. Compare a feature-free file and invalid-feature control. Require actual derived output or attributable server-side effect, not an upload receipt, thumbnail delay or browser-initiated fetch.
6. If client-side execution is the assigned consequence, render only in owned browser sessions and demonstrate a harmless local marker in the actual served origin. If server processing is claimed, browser execution is not sufficient. Re-run the valid file workflow after each branch and stop at the minimal verified boundary.

7. Where size/count enforcement is assigned, compare the planned small cases while holding other inputs fixed. Record actual file bytes, total encoded request size, part count and any partial acceptance or cleanup needs. Separate client-side rejection from server enforcement; interpret results against the established limit and policy.
8. Where repeated-field handling is assigned, compare the single-part control with repeated parts and, if relevant, reversed order. Use a representation that preserves duplicate fields. Different selection behavior or acceptance of malformed framing alone is not a vulnerability; identify a forbidden validation/storage/processing effect.

## Execution

1. Demonstrate one verified storage, access, served-content or processor boundary with a tiny nonsecret fixture. Do not upload web shells, trigger resource exhaustion, overwrite unrelated objects or expose active files to other users.
2. Delete uploaded files, derived previews, extracted members and queued/retry jobs through normal authorized interfaces. Remove callback content and confirm both original and derived object URLs no longer expose test data where deletion is expected.

## Analysis

1. Separate accepted file metadata, durable storage, retrieval permission, client execution and server processor behavior. An extension or media-type bypass matters only through an established forbidden effect.
2. Explain intentionally public uploads, generated safe filenames, converter stripping, quarantine delays, cached previews and archive normalization. A successful re-encode does not prove every processor path safe; failed rendering does not prove the file was never stored.

## Evidence requirements

Preserve synthetic file hashes/bytes and size, name/type variations, upload and job IDs, derived artifact content, owner/nonowner controls, processing attribution, served browser origin and cleanup results. For multipart comparisons retain request Content-Type/boundary, ordered part names, filenames, declared part types, per-file byte sizes and total request size, along with response and returned file/job identifiers. Record the exact layer and policy crossed, not a generic unrestricted-upload label.

## Limitations

No exhaustive file-format fuzzing, parser-CVE exploit, decompression bomb or filesystem inspection is provided. Unobservable async jobs or inaccessible derived outputs limit attribution. Equivalent capabilities may inspect the workflow without a named uploader/scanner.

## Trigger examples and non-examples

Matching: An assigned workflow accepts files and stores, serves, extracts or converts them. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
