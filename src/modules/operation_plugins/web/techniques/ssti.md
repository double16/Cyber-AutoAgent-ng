---
schema_version: 1
title: Server-side template injection
purpose: Distinguish server-side template injection from legitimate behavior using bounded, policy-aware
  comparisons.
applicability:
- An assigned input is rendered through a server-side template-like feature or shows candidate expression
  evaluation.
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
  when: An assigned input is rendered through a server-side template-like feature or shows candidate expression
    evaluation. while checking prerequisites in an assigned interaction.
  request: Server-side template injection reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: An assigned input is rendered through a server-side template-like feature or shows candidate expression
    evaluation. while designing future tests from recorded observations.
  request: Server-side template injection hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An assigned input is rendered through a server-side template-like feature or shows candidate expression
    evaluation. while checking a specific assigned boundary hypothesis.
  request: Server-side template injection vulnerability verification
  signals_any: []
- phases:
  - execution
  when: An assigned input is rendered through a server-side template-like feature or shows candidate expression
    evaluation. after a boundary is verified and a minimal demonstration is authorized.
  request: Server-side template injection execution
  signals_any: []
- phases:
  - analysis
  when: An assigned input is rendered through a server-side template-like feature or shows candidate expression
    evaluation. while interpreting recorded results and competing explanations.
  request: Server-side template injection analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Injection_Prevention_Cheat_Sheet.md
  title: Injection Prevention Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.121839+00:00'
  selection_reason: interpreter trust boundaries; template-specific discrimination is original synthesis.
- url: https://raw.githubusercontent.com/OWASP/wstg/v4.2/document/4-Web_Application_Security_Testing/07-Input_Validation_Testing/18-Testing_for_Server-side_Template_Injection.md
  title: wstg-ssti
  retrieved_at: '2026-11-07T10:33:40.381083+00:00'
  selection_reason: WSTG-INPV-18 describes text/code template contexts, expression testing and engine
    identification.
source_coverage: 'Independently selected retrieved references supply mechanism/control background. interpreter
  trust boundaries; template-specific discrimination is original synthesis. Limits: Development-oriented
  guidance supplies mechanism/control background; application policy, live exploitability, negative controls
  and minimal-impact procedure remain original methodology requiring target-specific verification. WSTG-INPV-18
  describes text/code template contexts, expression testing and engine identification. Limits: Its introductory
  RCE framing does not mean every template evaluation demonstrates RCE; designed template features and
  sandbox capabilities need policy-specific controls. Numbered procedures and controls are original knowledge-allowed
  synthesis, not individually source-verified. No completed target execution or affected-version claim
  is supplied.'
---

# Server-side template injection

## Applicability and prerequisites

An assigned input is rendered through a server-side template-like feature or shows candidate expression evaluation. Know whether the feature intentionally permits templates and its allowed expression/object capabilities. Obtain a harmless owned preview or test record and a raw-response view independent of browser scripts. HTTP capture/replay and a browser or equivalent inspector suffice. Engine documentation may guide syntax only after evidence supports that engine.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. Inspect assigned rendering examples and locate the input in text, a template expression, a template variable name or a stored rendering field. Distinguish server-generated source from browser DOM changes.
2. Record candidate delimiters and error clues without asserting the engine or sandbox escape. Identify whether arithmetic/text interpolation is an intended user feature and what data/capability boundary is actually prohibited.

## Hypothesis generation

1. Using recorded evidence, design a future expression pair with two independently predicted harmless results and an escaped or unbalanced delimiter control. A conceptual arithmetic example is an expression for 8 multiplied by 6 versus 8 multiplied by 5; actual syntax depends on the candidate engine.
2. Specify a raw-response observation that would distinguish server evaluation from reflection or client-side evaluation. For stored rendering, separate the write from the later render and identify the owned renderer/viewer.
3. If templates are intentionally enabled, propose a separate restricted-data/capability test using an owner-provided nonsecret marker that policy forbids the author to access. Do not assume arithmetic evaluation implies arbitrary code execution. No preview or rendering requests occur during hypothesis generation.

## Vulnerability verification

1. Render the accepted benign input and preserve the raw server response before browser JavaScript. Confirm the owned preview/result is stable; submit a plain unique literal as a reflection control.
2. In the justified text or expression context, send one harmless expression, its changed-constant counterpart and an inert escaped/unbalanced form. Require predictable outputs tracking expression semantics in the raw response. Avoid large ranges, object recursion, file access, commands or process creation.
3. If testing a code-context break-out, preserve the valid outer construct and vary only the suspected boundary. Compare a still-contained expression and an invalid syntax control. A server error indicates a parsing lead, not proof that a new statement executed.
4. For an intended template editor, distinguish permitted interpolation from prohibited data/object access. Only if an owner-provided safe marker and policy exist, test that one restricted capability against an allowed marker and invalid-name control; do not inspect environment secrets or enumerate object graphs.
5. Repeat the valid baseline and compare raw versus browser-rendered results. Record server evaluation as the narrow conclusion unless a separate restricted capability was reproducibly demonstrated.

## Execution

1. Demonstrate one harmless computed rendering value or the specifically approved nonsecret capability marker. Use a private test preview rather than publishing a persistent page viewed by unrelated users.
2. Restore stored templates/content through the normal application interface, remove generated preview outputs and verify ordinary rendering. Do not leave a reusable injection expression or public test page behind.

## Analysis

1. Separate literal reflection, client-side template evaluation, server-side evaluation and a sandbox/policy escape. Each needs its own channel and policy evidence; arithmetic never establishes RCE by itself.
2. Explain intentional template languages, format-string features, escaped output, asynchronous conversion and stale preview caches. Generic syntax overlap cannot reliably identify an engine; failures apply only to the tested context and syntax.

## Evidence requirements

Preserve input bytes, expected arithmetic/text result, raw response before scripts, paired expressions, inert control, template-author permissions and write/render correlation for stored cases. Identify the exact capability demonstrated rather than an assumed exploit chain.

## Limitations

No general sandbox escape, engine-specific RCE or object enumeration is supplied. Without a stated template policy, permitted arithmetic may only establish a feature. Inaccessible raw output or unstable rendering leaves server attribution inconclusive.

## Trigger examples and non-examples

Matching: An assigned input is rendered through a server-side template-like feature or shows candidate expression evaluation. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
