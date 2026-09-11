# LLM Role and Agent Profile Refactor

## Problem Statement

The per-agent model-settings implementation has two paths that make correctly named workflow agents appear to use the
`unknown` profile:

1. `src/modules/config/models/factory.py::_get_parameters_by_role()` resolves `plan_creator` or `plan_critic` from the
   registry, but then unconditionally replaces the profile temperature with the generic provider configuration's
   temperature. This makes the effective request resemble the generic/unknown profile even when registry lookup was
   correct.
2. The same function catches every exception raised while applying server output-token limits and changes the resolved
   role to `unknown`. A failure to read an optional limit must not change agent identity or select another profile.

The type boundary does not prevent either problem. `LLMRoleType` still advertises the retired coarse roles `primary`,
`report`, and `evaluation`, while the runtime now uses concrete workflow roles such as `plan_creator`, `plan_critic`, and
`task_executor`. Provider code also retains role checks for the old values, the swarm profile is named `swarm` while the
runtime emits `swarm_agent`, and the report-agent factory still passes the legacy `report` name or bypasses the profile
registry for some providers.

## Goals

- Make `LLMRoleType` the authoritative vocabulary for model-bearing agent profiles.
- Preserve a canonical agent role from the workflow call site through profile lookup, provider translation, model
  construction, runtime adaptation, observability, and Appendix C.
- Ensure provider configuration can constrain a profile but cannot silently replace its role-specific settings.
- Keep compatibility aliases only at explicit input boundaries; do not treat retired coarse roles as canonical roles.
- Make an unknown or missing role observable without allowing an unrelated exception to manufacture one.
- Add tests that validate the final provider constructor payload for planning roles, including negative and degraded
  configuration paths.

## Non-Goals

- Re-tune the current profile values. The later changes to reasoning levels, temperatures, and token limits are retained
  unless a separate tuning change is requested.
- Change provider protocols or the runtime adaptation policy.
- Add environment variables or change dependency versions.

## Design Decisions

### 1. One canonical role vocabulary

Move the authoritative `LLMRoleType` definition next to `AgentModelSettings` and `AgentSettingsRegistry` in
`src/modules/config/models/agent_profiles.py`. Implement it as a string enum so the same definition provides runtime
validation, stable serialization, and type annotations. Canonical model-bearing roles are:

- `plan_creator`
- `plan_critic`
- `task_creator`
- `task_prompt_builder`
- `task_prompt_critic`
- `task_executor`
- `task_evaluator`
- `phase_evaluator`
- `task_phase_classifier`
- `report_agent`
- `report_critic`
- `taxonomy_annotator`
- `attack_enricher`
- `swarm_agent`
- `unknown`

Keep roles separate when runtime adaptations must be isolated. In particular, `phase_evaluator` must not inherit a
reasoning downgrade or token promotion recorded for `task_evaluator`. Keep `plan_builder`, `report_executive`, and
`report_finding` only as documented compatibility aliases where they intentionally share a profile. Remove `primary`,
`report`, `evaluation`, `swarm`, `default`, and `executor` from canonical usage and migrate their internal callers.

Re-export `LLMRoleType` from `modules.config.system` and, if needed for compatibility, from
`modules.config.system.defaults`; `defaults.py` should no longer own a stale duplicate. Avoid import cycles by making
`agent_profiles.py` independent of provider-default construction.

### 2. Normalize once and preserve identity

Change `normalize_agent_type()` to accept strings or `LLMRoleType`, resolve known compatibility aliases, and return a
canonical `LLMRoleType`. Unknown non-empty values should normalize to `LLMRoleType.UNKNOWN` and emit a targeted warning
at the boundary rather than creating arbitrary unknown-profile entries keyed by misspellings.

Pass the canonical role through `_CreateModelParameters` and provider factories. Do not call `str()` on enum inputs
before normalization unless serialization explicitly requires `.value`. Model instances should retain the canonical
role in a private metadata attribute so fallback wrappers, recovery code, logs, and tests can verify the selected
profile.

### 3. Separate profile settings from provider constraints

Refactor `_get_parameters_by_role()` into explicit stages:

1. Normalize the requested role.
2. Fetch the role's active settings from `AgentSettingsRegistry`.
3. Apply learned provider/model exclusions from the registry.
4. Clamp `max_tokens` to authoritative provider/model/server output ceilings.
5. Return the same canonical role and the resulting parameters.

The generic `config["temperature"]` and `config["max_tokens"]` values are not alternate profiles. Use
`config["max_tokens"]` only as a positive output ceiling. Do not overwrite a non-`None` profile temperature with the
generic temperature. If a role-specific setting is `None`, leave it omitted so provider capability fallback remains
authoritative rather than filling it from the coarse config.

Replace the broad exception behavior around server-limit lookup with a narrow, logged degradation: retain the already
resolved role and settings, apply any independently available model/config ceiling, and never set the role to
`unknown`. Invalid explicit inputs should fail or normalize at the role boundary; infrastructure errors must not alter
identity.

### 4. Remove legacy role gates from provider construction

Update every model factory signature to accept the canonical role type plus strings only at public compatibility
boundaries. Replace checks such as `role in ["primary", "swarm"]` with decisions based on resolved profile settings and
model capabilities:

- Bedrock thinking configuration is selected from `reasoning_level` and capabilities, not a coarse role name.
- LiteLLM reasoning verbosity applies to supported models independently of the retired `primary`/`swarm` labels.
- Ollama and Gemini continue to translate the resolved profile reasoning level.
- Logging always uses the canonical role value.

Ensure the thinking-model path does not bypass `_get_parameters_by_role()`; all provider branches must start from the
same role settings and then translate them.

### 5. Align all model-bearing callers

- Keep workflow actor calls (`plan_creator`, `plan_critic`, task roles, taxonomy roles) canonical end to end.
- Change swarm subagents from the mismatched `swarm` profile to `swarm_agent`, including server-limit selection and
  runtime adaptation records.
- Route `ReportAgentFactory` through the centralized model factory for Bedrock, Ollama, LiteLLM, and Gemini. Pass
  `report_agent` or `report_critic`; remove the legacy `report` argument and provider-specific hard-coded temperature.
- Treat `operation_controller` as observability metadata only unless it directly owns an LLM invocation. The current
  main execution model remains `task_executor`; do not add an unused profile solely to match callback metadata.

### 6. Make the registry fail safely

Validate at registry initialization that:

- every canonical model-bearing role except `unknown` has exactly one default profile;
- every canonical profile key is represented by `LLMRoleType`;
- aliases target an existing canonical role;
- no retired coarse role is present in `DEFAULT_AGENT_PROFILES`.

`unknown` remains a deliberate safety profile for genuinely missing or unsupported external role values. It must not be
selected because provider config access, capability lookup, or token-ceiling discovery failed.

## Implementation Steps

### Step 1: Establish the canonical role contract

- Add `LLMRoleType` to `src/modules/config/models/agent_profiles.py` with the canonical values above.
- Type `DEFAULT_AGENT_PROFILES`, `ROLE_ALIASES`, adjustment records, normalization, and registry APIs with the new role
  contract while preserving string serialization in Appendix C.
- Remove the old literal definition from `src/modules/config/system/defaults.py` and add compatibility re-exports in
  `src/modules/config/models/__init__.py` and `src/modules/config/system/__init__.py` as required.
- Add registry integrity validation and explicit alias tests.

### Step 2: Correct resolution and constraint application

- Refactor `_get_parameters_by_role()` in `src/modules/config/models/factory.py` so generic temperature never overrides
  a role profile and server-limit failures never rewrite the role.
- Preserve max-token clamping against valid model/provider ceilings, with checks for absent, invalid, and lower ceilings.
- Attach the canonical role to the constructed model/wrapper for diagnostics and recovery correlation.
- Add focused logging for an actual unknown input and for a failed optional ceiling lookup.

### Step 3: Make provider paths profile-driven

- Update Bedrock, Ollama, LiteLLM, and Gemini factory signatures and internal branches to use canonical roles and
  resolved reasoning settings.
- Eliminate `primary`, `report`, `evaluation`, and `swarm` conditionals from model construction.
- Ensure Bedrock's thinking-model branch uses the same resolved per-agent temperature, reasoning, and output limit as
  the standard branch before provider translation.

### Step 4: Migrate callers and reporting

- Update `src/modules/agents/cyber_autoagent.py`, `src/modules/agents/factory.py`, and
  `src/modules/tools/swarm.py` to pass canonical role values.
- Refactor `src/modules/agents/report_agent.py` to construct both report roles through the shared factory and profile
  registry for every provider.
- Verify max-token recovery, learned capability fallback, trace attributes, and Appendix C continue to use the same
  canonical role without alias-induced cross-role state.
- Update architecture/configuration documentation where it describes coarse model roles.
- Add a `### Fixes` entry to `CHANGELOG.md` for the planning-profile fallback and role unification.

### Step 5: Add regression and integration coverage

Extend `tests/config/test_agent_profiles.py` and `tests/test_model_factory.py`, and use existing workflow/report test files
where applicable.

Positive scenarios:

- `plan_creator` and `plan_critic` resolve to their distinct profiles and preserve their canonical role.
- Each provider constructor receives the profile temperature, reasoning translation, `top_k`, `top_p`, and clamped
  output-token value expected for both planning roles.
- `phase_evaluator` and `task_evaluator` start with equivalent defaults but maintain independent adaptation state.
- `swarm_agent`, `report_agent`, and `report_critic` resolve without compatibility aliases.
- Report generation uses the central registry on all providers and Appendix C lists canonical roles.

Negative and degradation scenarios:

- A server-config/ceiling lookup exception preserves `plan_creator` settings and identity.
- A generic provider temperature different from the profile does not overwrite the profile temperature.
- A smaller valid provider output ceiling clamps `max_tokens`; an invalid or missing ceiling does not corrupt settings.
- Missing role input and an unsupported external role select `unknown` with an observable warning.
- Retired coarse internal role values are absent from production call sites.
- Registry construction fails for missing canonical profiles, unknown profile keys, or aliases with invalid targets.

Add an integration-style workflow test that runs plan creation and criticism with mocked provider models and asserts the
actual model-constructor payload for each call. This closes the current coverage gap where registry-only tests pass even
though the factory later overwrites the selected settings.

## Verification

From the repository root:

```bash
mkdir -p .uv-cache
UV_CACHE_DIR="$PWD/.uv-cache" uv sync --all-extras
UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest -q --tb=short
UV_CACHE_DIR="$PWD/.uv-cache" uv run coverage run -m pytest -q
UV_CACHE_DIR="$PWD/.uv-cache" uv run coverage report
UV_CACHE_DIR="$PWD/.uv-cache" uv run ruff check src tests
git diff --check
```

Require at least 80% code and branch coverage for changed and new business logic. Confirm positive and negative tests
pass for all four provider paths and that no tests continue to use `primary`, `report`, `evaluation`, or `swarm` as a
model-factory role.

## Acceptance Criteria

- A `plan_creator` invocation reaches every provider constructor with the active `plan_creator` profile and is logged as
  `plan_creator`, even if optional server-config lookup fails.
- A `plan_critic` invocation uses its own temperature, reasoning level, and token limit rather than generic provider or
  unknown-profile values.
- `LLMRoleType` contains the canonical agent-profile roles and no retired coarse roles.
- No internal model construction call passes `primary`, `report`, `evaluation`, or `swarm`.
- Every model-bearing canonical role has a validated default profile; genuine unknown inputs alone use `unknown`.
- Runtime adaptations and Appendix C remain isolated and attributable to the canonical role that produced them.
- Unit tests, branch coverage, Ruff, and `git diff --check` pass, and `CHANGELOG.md` documents the fix.
