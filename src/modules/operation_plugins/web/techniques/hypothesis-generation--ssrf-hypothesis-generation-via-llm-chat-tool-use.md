---
schema_version: 1
title: LLM tool-mediated SSRF boundary assessment
purpose: Separate model-proposed URLs from server execution and test destination policy using bounded
  controlled comparisons.
applicability:
- An assigned chat or multimodal interaction can influence URL arguments, an adapter destination or a
  server-executed retrieval tool.
phases:
- hypothesis_generation
- vulnerability_verification
- analysis
active: true
triggers:
- phases:
  - hypothesis_generation
  when: Recorded chat, multimodal or gateway tool observations suggest a user-controlled destination may
    reach a server-side fetch.
  request: 'LLM tool-mediated SSRF boundary assessment: hypothesis generation'
- phases:
  - vulnerability_verification
  when: A specific LLM-mediated destination-policy hypothesis is assigned with test identities and approved
    controlled destinations.
  request: 'LLM tool-mediated SSRF boundary assessment: vulnerability verification'
- phases:
  - analysis
  when: Recorded tool proposals, callback traffic or retrieved content need attribution and destination-policy
    interpretation.
  request: 'LLM tool-mediated SSRF boundary assessment: analysis'
sources:
- url: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.md
  title: Server-Side Request Forgery Prevention Cheat Sheet
  retrieved_at: '2026-11-06T21:29:20.508204+00:00'
  selection_reason: OWASP distinguishes legitimate outbound requests, destination allowlists, redirect
    handling and SSRF boundary controls.
source_coverage: The retained OWASP SSRF prevention reference supplies general destination-policy and
  redirect/DNS considerations, not proof of a named LLM product flaw. LLM data-flow and comparison procedures
  are original knowledge-allowed synthesis. No product/CVE or version-sensitive applicability claim is
  made.
---

# LLM tool-mediated SSRF boundary assessment

## Applicability and prerequisites

Use a known assigned interaction, not a general endpoint hunt. Distinguish these paths: user text
→ model-proposed arguments → actual server tool executor → outbound request; multimodal URL →
gateway/adapter fetch; or user-controlled configuration → adapter destination. Tool schemas and
`tool_choice` responses may only propose client-side work and do not prove any server execution.
A normal image fetch or web-search tool may intentionally accept arbitrary public URLs.

Before live verification, establish the actor, route, expected destination/scheme/port policy,
accepted tool behavior, a repeatable benign request and approved controlled destinations. The
policy must come from a documented contract, owner confirmation or independently established
boundary, not the assumption that all user-directed requests are forbidden. If policy is unknown,
record a fetch capability without labelling it SSRF. Tools are optional; equivalent HTTP/browser
and callback-recording capabilities suffice. Any HTTP tooling must honor configured proxies.

## Hypothesis generation — design from recorded evidence

1. Map each attacker-controlled field to the proposed argument and actual executor. Consider
   messages, image/file URLs, tool arguments and configurable adapter base URLs only when observed
   or plausibly supported. Mark unknown argument validation and execution as prerequisites.
2. Separate tool-call proposals from gateway-initiated multimodal fetches. Record which component
   would connect, under whose authority, and whether the client must execute the returned call.
3. Define a future accepted baseline using an approved public owned endpoint returning one harmless
   unique marker. State the ordinary successful tool result and callback evidence needed to attribute
   the fetch. This baseline is expected legitimate behavior, not a violation.
4. Design a future single-boundary variation against an independently established destination rule.
   Prefer a controlled owned endpoint forbidden by an allowlist policy. If the rule is public-only,
   use a separately approved internal canary reachable by the executor only when that environment
   and network access are explicitly in scope. With no safe prohibited destination, leave the
   violation test unavailable. Never substitute metadata services or unrelated internal hosts.
5. Define negative controls: an unrelated unused marker, the same chat with the URL omitted, and a
   non-executing tool response when the client can be kept from executing proposed calls. Specify
   repeat bounds and how cached, delayed or model-variable responses will be distinguished.
6. Specify the claimed result: correlated request proves a fetch; a request to a prohibited destination
   under the established policy supports SSRF; a returned canary proves response readability only.
   Keep these claims separate and hand off unmet prerequisites without sending live requests.

## Vulnerability verification — controlled live comparisons

1. Check assigned authorization, destination policy and exact baseline prerequisites. Preserve the
   original request, identity and tool configuration. Stop if the allowed test cannot distinguish
   prohibited behavior from intended retrieval. Do not enable additional tools or roles to make
   the hypothesis work.
2. Send one ordinary bounded request using a fresh harmless owned-public marker. Record the actual
   callback request, timestamp and application result. Where possible, identify the connecting
   component with gateway records or a protocol identifier. Source IP alone may identify shared
   infrastructure; model text claiming a fetch is not network evidence.
3. Run omission/non-execution controls and check the unused marker has no traffic. Prevent the
   browser or local client from following URLs or executing proposed tool calls during attribution.
   Account for link scanners, preview services and retries. If traffic cannot be tied to the server
   executor, attribution is inconclusive.
4. Change only the assigned destination boundary using the approved prohibited canary. Keep prompt,
   actor, tool, resource size and other arguments stable. Use fresh markers and the agreed small
   request bound. If redirects are the specific hypothesis, use one owned redirect hop to the
   approved prohibited canary and compare a direct allowed URL; never expand to unrelated schemes,
   ports, hosts or address ranges. DNS variants require a separately controlled and approved setup.
5. Check the prohibited destination's actual request record. Compare with the legitimate baseline,
   omission control and expected rejection. A model-proposed prohibited URL, generic error or HTTP
   200 does not establish that the executor connected. If only output contains a canary, rule out
   input reflection and prior knowledge before treating it as retrieved content.
6. Repeat the original allowed baseline to check stability. Stop after the minimal boundary result.
   Remove temporary owned callback/redirect resources and revert any explicitly permitted test
   configuration; verify restoration. Do not retrieve production data, modify downstream resources
   or pursue further internal access. A later impact demonstration needs its own assigned method.

## Analysis — attribution and interpretation

1. Reconstruct the path and controls from existing records. Attribute each event to model proposal,
   client action, gateway fetch, executor fetch or an unresolved intermediary. Do not collapse them.
2. Compare the exact destination to the established policy, including redirect handling and actor
   entitlements. An owned public callback remains a baseline when arbitrary public URLs are allowed.
   Configuration changes by an authorized administrator may be legitimate; a low-privilege actor's
   unauthorized configuration write is a distinct authorization question.
3. Classify the tested boundary as reproducibly violated, rejected for this variant, or inconclusive.
   A denial may result from egress, parsing, model choice or absent execution; it does not establish
   universal URL validation. A callback without response content can still establish the narrower
   prohibited request if attribution and policy are established.

## Evidence requirements and limitations

Retain the destination policy and its origin, actor entitlements, exact input-to-tool mapping,
request/control references, fresh marker provenance, callback timing, redirect chain and any returned
canary comparison. Preserve secrets only in sensitive internal evidence and redact report/log values.
This is not a generic discovery or evidence-management procedure.

Nondeterministic tool selection, caches, asynchronous execution, provider infrastructure and
intermediary scanning limit inference. A gateway may delegate network work to another service;
record that boundary rather than assuming the origin server fetched it. Tool-mediated behavior
and multimodal retrieval are related entry paths, not evidence of a named CVE. General SSRF URL
parser, DNS-rebinding and non-HTTP exploitation need their own prerequisites and procedures.
