---
schema_version: 1
title: Policy-Based Connectivity Comparison with Identity Binding
purpose: Provide a reusable methodology for comparing connectivity outcomes under
  a defined policy using identity binding, controlled variations, and evidence requirements
  to distinguish legitimate behavior from boundary violations.
applicability:
- network policy validation
- firewall rule evaluation
- stateful connectivity testing
phases:
- planning
- validation
triggers:
- phases:
  - planning
  - validation
  when: A policy-based connectivity decision needs to be reproduced and compared across
    identities or rule sets
  request: Describe a reusable policy-based connectivity comparison with identity
    binding, controls and evidence requirements
  signals_any: []
sources:
- url: https://man.openbsd.org/pf.conf
  title: OpenBSD pf.conf manual
  retrieved_at: ''
  selection_reason: OpenBSD pf.conf manual provides authoritative description of pf
    rule evaluation order and state creation, supporting policy-based connectivity
    comparison methodology. The excerpt 'the last matching rule decides what action
    is taken' establishes rule precedence for policy decisions, and 'the first time
    a packet matches a pass rule, a state entry is created' supports evidence requirements
    around stateful tracking and baseline vs variation observations.
active: true
source_coverage: 'Considered 1 fetched documents; selected 1. {"requirements": [{"id":
  "method", "description": "Describe a reusable policy-based connectivity comparison
  with identity binding, controls and evidence requirements. No target execution or
  particular command syntax is requested.", "kind": "authored_deliverable", "origins":
  []}]}

  Authority sufficient: True; requirement coverage sufficient: True; research ready:
  True. Retained 1 sources. Latest source assessment: The current document is an authoritative
  primary source for OpenBSD pf configuration semantics. The retained excerpts provide
  at least one relevant supported technical fact about policy evaluation order and
  state creation that can support original procedural synthesis for a reusable policy-based
  connectivity comparison. Identity binding, specific controls and evidence requirements
  are not covered in the retained excerpts and remain limitations. Authority for the
  document''s own relevant contributions is sufficient; coverage of the full request
  is assessed separately.

  Research readiness is permission to draft, not fulfillment or factual proof. All
  recorded concerns remain for authoring and independent critique.

  {"coverage": [{"requirement_id": "method", "source_urls": ["https://man.openbsd.org/pf.conf"],
  "explanation": "The retained pf.conf excerpts provide authoritative support for
  policy evaluation precedence and stateful tracking that can anchor a reusable comparison
  methodology. The last-matching-rule semantics supports policy-based decision ordering
  for baseline vs variation tests, and first-packet state creation supports evidence
  requirements around stateful observation. Identity binding, specific controls and
  detailed evidence collection can be described generically as procedural prerequisites
  and future validation steps without asserting unsupported pf mechanisms, so the
  supported facts are sufficient to author the requested reusable guidance.", "authority_sufficient":
  true, "claim_support": [{"claim": "pf evaluates rules in order and the last matching
  rule decides the action taken, enabling policy-based connectivity decisions.", "source_urls":
  ["https://man.openbsd.org/pf.conf"], "authority_basis": "OpenBSD pf.conf manual
  is the authoritative primary source for pf rule evaluation semantics."}, {"claim":
  "The first time a packet matches a pass rule a state entry is created, providing
  a stateful tracking basis for baseline and variation observations.", "source_urls":
  ["https://man.openbsd.org/pf.conf"], "authority_basis": "OpenBSD pf.conf manual
  describes state creation behavior for pass rules."}], "authoring_obligations": [],
  "synthesis": "", "gaps": [], "future_validation": ["Establish intended policy and
  baseline connectivity for the specific identity context during future application.",
  "Confirm rule precedence and state creation behavior in the target pf version and
  configuration.", "Collect baseline and controlled variation observations with state
  table evidence to distinguish legitimate vs prohibited outcomes."]}]}'
---

# Policy-Based Connectivity Comparison with Identity Binding

## Purpose
Establish a repeatable comparison framework that evaluates whether connectivity is allowed or denied according to an explicit policy, bound to a specific identity context, using baseline vs controlled variation and stateful evidence.

## Prerequisites
1. **Policy definition**: Documented intended allow/deny decisions per identity attribute, service, and direction. Policy must be expressed as ordered rules where evaluation precedence is defined.
2. **Identity binding model**: Explicit mapping of identity attributes to enforcement points, e.g., source identifier, user context, device tag. The binding mechanism and its trust assumptions must be stated.
3. **Test environment controls**: Ability to generate controlled probe traffic under two conditions: baseline and variation, with identity held constant within each comparison set.
4. **Observation capability**: Access to policy decision logs and state table observations to record first-packet state creation and subsequent handling.

## Procedure

### 1. Establish Policy and Baseline
1.1 Record the ordered policy set. Note that evaluation order matters and the last matching rule decides the action taken.
1.2 Define the identity binding for the test: which attribute is used to select the rule set and how the binding is enforced at the enforcement point.
1.3 Define legitimate outcome and prohibited outcome for the specific identity-service pair.
1.4 Capture baseline connectivity observation for the bound identity under current policy. Record decision result and state creation evidence.

### 2. Define Controlled Variation
2.1 Select one policy variable to change, e.g., rule ordering, identity attribute value, or service parameter, while keeping all other variables constant.
2.2 Document the expected policy interpretation for both baseline and variation given the last-matching-rule semantics.

### 3. Execute Comparison
3.1 Perform baseline observation with identity binding A.
3.2 Perform variation observation with identity binding B or modified rule condition, maintaining same measurement method.
3.3 For each observation record:
- Identity attributes used for binding
- Policy rule set version and evaluation order
- First-packet decision and state entry creation timestamp
- Subsequent packet handling within the established state
- Decision outcome: allowed, denied, or dropped

### 4. Controls
- **Identity isolation control**: Repeat test with a different identity that should map to a different policy outcome to verify binding is effective.
- **Order sensitivity control**: If policy allows, test with a non-matching rule preceding a matching rule to confirm last-matching-rule precedence.
- **Statefulness control**: Observe first-packet state creation on pass and verify subsequent packets follow state, not re-evaluate policy.
- **Negative control**: Test a known denied identity-service pair to confirm denial is observable.

### 5. Evidence Requirements
- Policy artifact: ordered rule list with version identifier.
- Identity binding evidence: mapping of test traffic to identity attribute and enforcement point.
- Decision evidence: timestamped log of policy decision per packet, indicating matching rule and action.
- Stateful evidence: state table entry creation record for first matching pass, including tuple and lifetime.
- Comparison table: baseline vs variation with identity, rule matched, decision, state created yes/no.

### 6. Interpretation
- Legitimate behavior: outcome matches documented policy for the bound identity and state creation follows first-packet pass semantics.
- Boundary violation hypothesis: outcome differs from policy for the bound identity, or state is created/maintained contrary to policy, or identity binding is bypassed.
Interpretation must be conditioned on established policy and binding; do not infer violation from outcome alone.

## Limitations
Identity binding mechanisms, specific controls, and detailed evidence collection procedures are described generically. Actual binding enforcement, rule syntax, and logging formats are implementation-specific and must be established during future application. Source support is limited to policy evaluation order and state creation semantics.

## Future Validation
- Establish intended policy and baseline connectivity for the specific identity context during future application.
- Confirm rule precedence and state creation behavior in the target pf version and configuration.
- Collect baseline and controlled variation observations with state table evidence to distinguish legitimate vs prohibited outcomes.
