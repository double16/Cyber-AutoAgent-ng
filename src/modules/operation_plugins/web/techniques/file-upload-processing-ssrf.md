---
schema_version: 1
title: SSRF through upload processing and metadata
purpose: Distinguish backend external-resource resolution during file processing from
  storage, browser requests and permitted fetching using controlled comparisons.
applicability:
- An assigned upload or conversion workflow may resolve external references in file content or metadata.
phases:
- reconnaissance
- hypothesis_generation
- vulnerability_verification
- execution
- analysis
active: true
triggers:
- phases: [reconnaissance, hypothesis_generation]
  when: An assigned file-processing workflow has recorded external references or indications of conversion,
    preview generation or metadata-driven resource loading.
  request: SSRF through upload processing and metadata prerequisites and future comparisons
  signals_any: []
- phases: [vulnerability_verification, execution, analysis]
  when: An assigned hypothesis or recorded processing result concerns backend fetching of resources
    referenced by an uploaded file or its metadata.
  request: SSRF through upload processing and metadata attribution and destination-policy controls
  signals_any: []
sources:
- url: https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html
  title: File Upload Cheat Sheet
  retrieved_at: '2026-11-27'
  selection_reason: Upload processing and parser risks, distinct from browser-side active content.
- url: https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html
  title: Server-Side Request Forgery Prevention Cheat Sheet
  retrieved_at: '2026-11-27'
  selection_reason: Document rendering and conversion fetch paths and application/network destination controls.
- url: https://imagemagick.org/security-policy/
  title: ImageMagick Security Policy
  retrieved_at: '2026-11-27'
  selection_reason: Configurable coder, module and delegate restrictions and explicit policy-denial errors.
source_coverage: >-
  OWASP supplies upload-processing risk and server-side destination-control background; ImageMagick
  supplies vendor-specific policy mechanisms. The comparisons and attribution procedure are original
  knowledge-allowed methodology, not individually source-verified. These references establish no target
  processor, metadata dereference, configured policy, affected version or completed vulnerability.
---

# SSRF through upload processing and metadata

## Applicability and prerequisites

Use this method for an assigned upload, thumbnail, preview, extraction or conversion workflow where
file content or metadata may influence backend resource resolution. A URL in a file, accepted upload,
format name or installed library is a lead only. Metadata extraction does not imply URL dereference.

Establish the accepted file type, actual processing operation, permitted external-resource behavior,
owned upload/job/output identifiers, a small file and request envelope, and cleanup interfaces. Use
an approved controlled receiver. To test a forbidden destination, obtain an owner-designated harmless
fixture explicitly in assessment scope but disallowed to the feature. Without it, limit conclusions
to attributed fetch capability. Do not substitute cloud metadata services or arbitrary private hosts.

Access the target only through authorized application network interfaces. Local tools may construct
synthetic files and inspect saved artifacts; do not inspect target files, processes or policy files
directly. Owner-provided diagnostic exports may help. Use equivalent available tools and respect the
assigned identities, rate, state-change and callback constraints.

## Reconnaissance

1. Trace recorded upload, processing, job polling and output retrieval separately. Identify whether
   bytes are merely stored, rendered in a browser, or processed by a backend. A thumbnail delay or
   content-type header does not establish which processor ran.
2. Record known, unknown and contradicted prerequisites for the assigned branch: SVG external image
   resolution; PDF/Office external-resource processing; metadata URL dereference; or a particular
   ImageMagick coder/delegate path. General format capabilities establish no target prerequisite.
3. Identify the destination policy and observation channel. Distinguish permitted asset fetching
   from forbidden network access. Record whether redirects, retries, caches or asynchronous workers
   can change attribution. Do not expand reconnaissance into unassigned upload or network tests.

## Hypothesis generation

1. Use recorded observations only; do not upload files, enqueue conversions or contact receivers in
   this phase. State the suspected input-to-processor-to-fetch path and the specific forbidden effect.
   Turn missing processor, dereference and policy facts into explicit future checks.
2. Plan a tiny valid reference-free file, a counterpart referencing an allowed controlled resource,
   and an inert/reference-free control. Keep type, authentication and processing options stable.
   Assign a fresh receiver path or marker to each future operation. A successful public fetch is an
   attribution baseline, not SSRF proof.
3. Select one mechanism branch. For SVG, use a single external image reference without scripting.
   For PDF/Office, use only a reference type the actual converter is expected to resolve; a clickable
   link alone is insufficient. For metadata, vary one relevant field only if evidence motivates a
   dereference path; EXIF/XMP/IPTC strings are ordinarily just data. Keep ImageMagick conditional on
   its involvement and configured coder/delegate behavior; do not assume universal scheme support.
4. For the same prohibited-fixture probe, specify a supporting observation (attributed forbidden
   fetch or returned nonsecret marker), a refuting observation (correlated explicit destination denial
   or demonstrated removal of the reference before resolution), and inconclusive outcomes. Allowed
   baseline success, unrelated invalid files and timeout alone cannot substitute for that refutation.

## Vulnerability verification

1. Through the assigned application flow, process the valid tiny file and confirm the expected
   owned output or completed job. Then process the allowed-reference counterpart and inert control
   with distinct markers per file/job. Retain exact synthetic bytes, upload/job IDs, upload and
   processing-trigger times and output evidence. Where supported, compare a stored-but-not-processed
   counterpart. Correlate callbacks with conversion/preview execution or a returned resource marker;
   if the channels cannot distinguish a renderer from an upload-time scanner, report an unattributed
   fetch rather than a confirmed processor fetch.
2. Correlate receiver requests or equivalent observations with processing, separating DNS lookup,
   connection, HTTP request and resource content returned in the derived output. Avoid opening the
   active file in a browser during backend attribution. Exclude tester requests, link previews,
   antivirus scanners and unrelated worker retries; source IP or User-Agent alone is insufficient.
3. Change only the destination to the explicitly scoped prohibited fixture, keeping the resource
   representation and processing path comparable. Use equivalent tiny resource bytes, media type and
   response behavior at both fixtures where feasible; retain unavoidable differences. Unsupported
   content, TLS failures and response-format differences are not evidence of destination denial.
   Do not independently contact prohibited fixtures outside the authorized observation path. Use
   fixed non-redirecting resources unless redirect testing is separately authorized; if tested,
   preserve per-hop attribution and scope. A DNS-only observation supports resolution only;
   an HTTP request supports fetching, and resource bytes in output support response consumption.
   Identify the precise policy violated rather than calling every fetch SSRF.
4. For an ImageMagick branch, correlate any policy error with this job and the attempted operation.
   An explicit coder/delegate denial may refute that branch; an installed version, generic conversion
   error or a different blocked operation does not establish the tested destination restriction.
5. When no callback arrives, check the observation window, receiver health, job completion, cache and
   reference handling using available evidence. A reachable allowed destination does not prove the
   prohibited destination was reachable or denied. Timeout, reset, DNS error, missing output and
   absent callback remain inconclusive without evidence of the relevant control decision. Reference
   removal supports only that processing path when the retained output or diagnostics establish
   removal before resolution; silence alone does not establish sanitization.
6. Repeat the smallest decisive comparison with fresh markers where permitted. Stop at the narrow
   demonstrated boundary. Unavailable telemetry stays a limitation; do not invent backend logs or
   broaden scope to obtain them.

## Execution and cleanup

1. Reuse the validated branch to demonstrate one prohibited request or one nonsecret fixture marker.
   Do not retrieve credentials, probe neighboring hosts, introduce scripts/macros, use expansion bombs
   or change upstream state. This procedure supplies no arbitrary protocol pivot or parser exploit.
2. Delete owned uploads, previews and derived outputs through the normal interface, cancel owned
   queued/retry jobs where supported, and remove stored external references. Confirm callbacks cease
   within the authorized observation window before retiring the receiver. If retry intervals are
   unknown or cancellation is unavailable, record the limitation and arrange an owner-approved
   harmless receiver retention or retirement plan. Silence does not prove all queued work has ceased.
   Preserve any unresolved cleanup gap.

## Analysis and evidence

1. Classify storage, browser loading, backend resolution, fetching, response consumption and forbidden
   destination access separately. Intentional public fetching is legitimate. Neither a retained URL
   nor successful conversion proves an external request; a callback alone proves no policy violation.
2. Retain file hashes and bytes, reference type/field, method and processing options, identity context,
   job timeline, receiver marker/logs, allowed/prohibited/inert comparisons, relevant output or denial
   evidence, destination policy and cleanup results. Explain proxies, caches, scanners and retries.
3. State whether the tested branch is supported, refuted for the observed path, or inconclusive, and
   distinguish capability from policy breach. Redact secrets from reports; use synthetic nonsecret
   markers throughout this method. Unknown format/library variants remain untested.

## Trigger examples, non-examples and limitations

Matching: an assigned image-preview workflow retains an external SVG reference; an assigned metadata
pipeline is documented to fetch linked resources; a conversion result records a correlated outbound
request. Select the phase-specific procedure. The initial signal recommends investigation only.

Non-matching: multipart syntax alone, a URL in ordinary EXIF text with no relevant processing evidence,
browser-only loading, or a library name without an assigned processing path. A single silent callback
test does not establish safety. Async jobs and inaccessible telemetry can leave attribution unresolved.
This focused method complements general upload/storage and server-side destination-boundary guidance;
it does not add mapping obligations or authorize assessment execution.
