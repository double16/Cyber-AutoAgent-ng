---
schema_version: 1
title: Server-side request destination boundaries
purpose: Distinguish server-side request destination boundaries from legitimate behavior using bounded,
  policy-aware comparisons.
applicability:
- An assigned URL, host or remote-resource input may cause server-side network requests.
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
  when: An assigned URL, host or remote-resource input may cause server-side network requests. while checking
    prerequisites in an assigned interaction.
  request: Server-side request destination boundaries reconnaissance
  signals_any: []
- phases:
  - hypothesis_generation
  when: An assigned URL, host or remote-resource input may cause server-side network requests. while designing
    future tests from recorded observations.
  request: Server-side request destination boundaries hypothesis generation
  signals_any: []
- phases:
  - vulnerability_verification
  when: An assigned URL, host or remote-resource input may cause server-side network requests. while checking
    a specific assigned boundary hypothesis.
  request: Server-side request destination boundaries vulnerability verification
  signals_any: []
- phases:
  - execution
  when: An assigned URL, host or remote-resource input may cause server-side network requests. after a
    boundary is verified and a minimal demonstration is authorized.
  request: Server-side request destination boundaries execution
  signals_any: []
- phases:
  - analysis
  when: An assigned URL, host or remote-resource input may cause server-side network requests. while interpreting
    recorded results and competing explanations.
  request: Server-side request destination boundaries analysis
  signals_any: []
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.md
  title: Server-Side Request Forgery Prevention Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.508204+00:00'
  selection_reason: destination validation and server-side network boundaries.
source_coverage: 'Independently selected retrieved references supply mechanism/control background. destination
  validation and server-side network boundaries. Limits: Development-oriented guidance supplies mechanism/control
  background; application policy, live exploitability, negative controls and minimal-impact procedure
  remain original methodology requiring target-specific verification. Numbered procedures and controls
  are original knowledge-allowed synthesis, not individually source-verified. No completed target execution
  or affected-version claim is supplied.'
---

# Server-side request destination boundaries

## Applicability and prerequisites

An assigned URL, host or remote-resource input may cause server-side network requests. Establish the fetcher's intended destination, scheme, port, redirect and response-exposure policy. Use an approved controlled receiver and, for prohibited-boundary testing, an owner-designated harmless destination explicitly in assessment scope but disallowed to the feature. Permission to assess an application URL is not permission to probe its private networks. A replay client and correlated receiver or equivalent observation suffice.

Choose only the assigned branch and obey the operation's scope, identities, rate and state-change constraints. Missing prerequisites stay explicit; a lookup signal is not proof. Equivalent available tools may replace named capabilities.

## Reconnaissance

1. Inspect assigned remote-resource inputs and observed outputs for the distinction between browser navigation, model/tool suggestion, DNS resolution and actual backend fetch. URL acceptance alone establishes none of the later steps.
2. Record which parts the caller controls: authority, path, method, headers, redirects and returned content. Distinguish an intentional public URL fetcher from an allowlisted integration. Unknown network topology or destination policy remains an explicit prerequisite.

## Hypothesis generation

1. From recorded evidence design a future allowed public callback baseline and a one-variable destination-policy test using only scoped owned fixtures. Assign unique markers and a callback-free or invalid-URL control for source attribution.
2. Separate direct authority validation, redirect revalidation and DNS/address binding hypotheses. Define allowed versus forbidden destination categories and the minimal observable effect for each; do not treat every outbound public fetch as SSRF.
3. Specify how browser/preview/scanner fetches, retries and DNS-only events will be excluded. Do not fetch URLs, query internal addresses, change DNS or trigger tools in this context. If a prohibited safe fixture cannot be supplied, plan to assess egress capability only.

## Vulnerability verification

1. Submit an approved allowed URL with a unique token and correlate the receiving server's request with the application operation. Use an invalid/no-URL control and a fresh marker to exclude browser previews, stale retries and unrelated scanners. Record DNS-only resolution separately from HTTP requests and response-body reads.
2. Verify intended behavior with a harmless controlled response and, if applicable, a unique marker in the application's output. A backend HTTP request to an allowed public server is an attribution baseline, not a policy violation.
3. For the assigned destination rule, vary only the relevant authority/scheme/port or parser representation to the owner-designated scoped prohibited fixture. Compare the ordinary allowed destination and a nonexistent/inert destination. Do not substitute real metadata services, arbitrary private addresses or unrelated ports.
4. For redirect validation, use an allowed controlled first hop that redirects once to the approved prohibited fixture, comparing no-redirect and allowed-target redirects. Correlate each hop at controlled receivers; a redirect status returned to the user is not evidence the backend followed it.
5. For an authorized DNS-binding branch, use only owner-controlled resolution and destinations, bounded requests and receiver attribution. Preserve the actual returned address/timing; acceptance of one hostname representation alone does not show a resolved-address-policy bypass.
6. Confirm the exact forbidden fetch/read under established policy and repeat the allowed/control baseline. If policy intentionally permits the destination or only a DNS lookup occurs, retain that narrower result. Stop once the boundary is demonstrated; do not scan neighbors or chain into credential retrieval.

## Execution

1. Demonstrate one prohibited request to the scoped harmless fixture or one nonsecret controlled response marker through the application. Use the least data and request volume needed and make no state-changing upstream action.
2. Remove controlled URL content/redirects, end temporary DNS rules/listeners and clear any stored remote-resource setting or queued fetch through the normal interface. Verify retries have stopped and ordinary allowed fetching still works.

## Analysis

1. Classify user-controlled input, DNS resolution, HTTP fetch, redirect following, response readability and prohibited network access separately. Callback availability alone never establishes the semantic violation.
2. Explain intended public egress, URL previews, browser requests, proxy behavior, DNS caches and asynchronous retries. The policy may prohibit only internal destinations or response exposure; articulate the actual rule rather than assuming all user-directed fetching is unsafe.

## Evidence requirements

Retain destination policy and authorization boundaries, exact URL encodings, unique markers, allowed/prohibited/inert controls, receiver request logs, redirect/DNS timelines and any returned nonsecret marker. Identify server attribution and the precise protocol/port effect; redact embedded secrets.

## Limitations

No broad internal scan, cloud metadata access, credential exfiltration or arbitrary protocol pivot is supplied. Missing a safely scoped prohibited fixture limits the result to fetch capability. Backend proxies and blind channels may leave destination or data-read semantics inconclusive.

## Trigger examples and non-examples

Matching: An assigned URL, host or remote-resource input may cause server-side network requests. Select the section corresponding to the current structured phase. Non-matching: a vulnerability name in unrelated prose, an unassigned surface or a result with no relevant processing path. Signals and semantic conditions recommend this method; they never establish its prerequisites or conclusion.
