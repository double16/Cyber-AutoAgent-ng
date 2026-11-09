---
schema_version: 1
title: Path traversal and file inclusion boundaries
purpose: Distinguish path traversal and file inclusion boundaries from legitimate behavior using bounded,
  policy-aware comparisons.
applicability:
- An assigned path, filename or template parameter selects server-side content or inclusion behavior.
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
  when: An assigned path, filename or template parameter selects server-side content or inclusion behavior.
    while checking prerequisites in an assigned interaction.
  request: Path traversal and file inclusion boundaries reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: An assigned path, filename or template parameter selects server-side content or inclusion behavior.
    while designing future tests from recorded observations.
  request: Path traversal and file inclusion boundaries hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An assigned path, filename or template parameter selects server-side content or inclusion behavior.
    while checking a specific assigned boundary hypothesis.
  request: Path traversal and file inclusion boundaries vulnerability verification
  signals_any: []
- phases:
  - execution
  when: An assigned path, filename or template parameter selects server-side content or inclusion behavior.
    after a boundary is verified and a minimal demonstration is authorized.
  request: Path traversal and file inclusion boundaries execution
  signals_any: []
- phases:
  - analysis
  when: An assigned path, filename or template parameter selects server-side content or inclusion behavior.
    while interpreting recorded results and competing explanations.
  request: Path traversal and file inclusion boundaries analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/File_Upload_Cheat_Sheet.md
  title: File Upload Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.387751+00:00'
  selection_reason: file-name and storage boundaries; inclusion-specific tests are original synthesis.
- url: https://raw.githubusercontent.com/OWASP/wstg/v4.2/document/4-Web_Application_Security_Testing/05-Authorization_Testing/01-Testing_Directory_Traversal_File_Include.md
  title: wstg-traversal
  retrieved_at: '2026-11-07T10:32:58.896506+00:00'
  selection_reason: WSTG-ATHZ-01 distinguishes traversal/file-inclusion entrypoints and explains path/encoding
    variations.
source_coverage: 'Independently selected retrieved references supply mechanism/control background. file-name
  and storage boundaries; inclusion-specific tests are original synthesis. Limits: Development-oriented
  guidance supplies mechanism/control background; application policy, live exploitability, negative controls
  and minimal-impact procedure remain original methodology requiring target-specific verification. WSTG-ATHZ-01
  distinguishes traversal/file-inclusion entrypoints and explains path/encoding variations. Limits: Development-oriented
  guidance supplies mechanism/control background; application policy, live exploitability, negative controls
  and minimal-impact procedure remain original methodology requiring target-specific verification. Numbered
  procedures and controls are original knowledge-allowed synthesis, not individually source-verified.
  No completed target execution or affected-version claim is supplied.'
---

# Path traversal and file inclusion boundaries

## Applicability and prerequisites

An assigned path, filename or template parameter selects server-side content or inclusion behavior. Establish the allowed root/resource policy and obtain an owner-provided nonsecret fixture with a known unique marker outside that allowed selector boundary, plus an allowed fixture. Use network requests to the application only; do not inspect the target filesystem, containers or source locally. If no safe prohibited fixture exists, limit testing to selector behavior and record impact as untested.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. Inspect assigned filename/template inputs, observed path normalization and error responses. Distinguish a resource identifier mapped by the application from a literal filesystem path; file-like parameter names alone are weak clues.
2. Identify whether the feature returns bytes, renders/includes templates or fetches remote content. A file read does not establish executable inclusion, and a remote fetch does not establish code execution. Record expected directory/type restrictions and OS/path uncertainties.

## Hypothesis generation

1. From recorded inputs design a future allowed-file baseline, a known nonexistent-path control and one boundary-crossing path targeting only the approved marker fixture. Specify the expected unique content and why access is prohibited by the selector's policy.
2. Plan relative segments, separator or encoding variations one at a time only where normalization clues justify them. Do not guess arbitrary system files or enumerate paths.
3. For inclusion, design a separate inert-content versus owner-approved harmless interpreted fixture comparison, if such a fixture/capability is available. Without it, leave execution unproven. No reads, uploads or remote fetches occur during hypothesis generation.

## Vulnerability verification

1. Retrieve the allowed fixture through the assigned interface and verify its unique marker. Compare the nonexistent resource and a direct ordinary application route to distinguish fallback pages, public content and path reflection.
2. Submit the smallest relative boundary-crossing selector for the approved outside fixture, keeping identity, method and other parameters fixed. Compare a path pointing to an allowed fixture and a nonexistent sibling with similar syntax.
3. If an encoding/normalization hypothesis is assigned, vary only that representation and preserve exact wire bytes. Show that returned content identifies the prohibited fixture rather than merely an error disclosing a path. Do not broaden to arbitrary traversal depths or unrelated sensitive files.
4. For local or remote inclusion, test only an owner-provided harmless fixture, with a literal-content control and a unique generated marker if interpretation is claimed. Static file bytes, a remote-host callback or an included text marker alone prove only read/fetch/inclusion, not server code execution.
5. Repeat the original baseline and deny/inert controls in reversed order. Confirm the exact directory/resource-policy boundary crossed and state whether the mechanism was byte retrieval, template inclusion or separately demonstrated interpretation. A readable public asset or authorized shared file is not a violation.

## Execution

1. Demonstrate only the unique nonsecret fixture bytes or owner-approved harmless inclusion marker through the affected network interface. Avoid passwords, environment files, logs, code execution payloads and remote protocol pivots.
2. Remove test fixtures through authorized owner/application procedures, restore any selector preferences and callback content, and verify ordinary allowed-file behavior. Do not delete or change preexisting target files.

## Analysis

1. Separate accepted path syntax, normalized traversal, prohibited file access and executable inclusion. Path disclosure in an error is a different narrower observation.
2. Explain aliases, symlinks, intentional shared roots, resource mapping, URL fetching, caches and fallback pages. A successful read needs policy evidence; nonownership alone does not imply denial. No result from one OS/path encoding excludes every inclusion or normalization path.

## Evidence requirements

Retain exact encoded requests, allowed/outside/nonexistent fixture markers, established allowed-root/resource policy, identity entitlement, returned bytes or generated inclusion output, repeat controls and cleanup. Use nonsecret fixtures; do not publish target filesystem details unnecessarily.

## Limitations

External-only access may leave actual paths, symlinks or inclusion execution unknown. Missing a scoped prohibited fixture means only tentative selector behavior can be assessed. No arbitrary local-file enumeration, log poisoning or executable inclusion chain is supplied.

## Trigger examples and non-examples

Matching: An assigned path, filename or template parameter selects server-side content or inclusion behavior. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
