## Critical rules the agent must follow before doing anything
- Read `README.md` before acting.
- Update `CHANGELOG.md` for user-facing changes. Categorize using `### Features` and `### Fixes`.

## Testing and contribution
- Always write unit tests and check that they pass for new and changed business logic.
- Changed and new code should have at least 80% code and branch coverage.
- Always run unit tests to verify changes.
- Test both positive and negative scenarios.
- Keep tests in files based on component or functionality. Use existing test files if applicable.

## Explicit prohibitions what agents must NOT do
- Do not bump major versions of core dependencies without a dedicated PR and discussion.
- Do not rename files without a valid technical reason.

## Documentation
- Keep documentation up-to-date and accurate.
- Use clear language.
- Follow a consistent style and format for documentation.
- Use examples and diagrams to illustrate concepts.
- Environment variables used for configuration must be documented in a table along-side similar variables.

## Python Best Practices
- Use the `uv` tool for python ecosystem, i.e `uv run ...`
- Follow PEP 8 with a 120-character line limit
- Run `uv run ruff` on new or changed files to validate Python coding standards
- Use double quotes for Python strings
- Sort imports with `isort`
- Use f-strings for string formatting
- If a class member is set in __init__, do not use getattr(), use direct reference.
- For multi-line strings, use triple quotes and limit each line to 100 characters.
- Environment variables used for configuration must be given an example in .env.example and forwarded through docker-compose.yml.

### uv in the sandbox
- Configure `uv` to use the persistent project-local `.uv-cache` directory. The default user cache may be outside the
  sandbox's writable paths and can cause `uv` to fail before Python starts.
- From the repository root, initialize and synchronize the environment with:

  ```bash
  mkdir -p .uv-cache
  UV_CACHE_DIR="$PWD/.uv-cache" uv sync --all-extras
  ```

- Prefix subsequent `uv` commands with the same cache setting. Use the repository's `.venv` through `uv run`; do not
  activate a different virtual environment or invoke tools from a system Python.
- Examples:

  ```bash
  UV_CACHE_DIR="$PWD/.uv-cache" uv run python3 --version
  UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest -q --tb=short
  UV_CACHE_DIR="$PWD/.uv-cache" uv run ruff check src tests
  UV_CACHE_DIR="$PWD/.uv-cache" uv run coverage run -m pytest -q
  UV_CACHE_DIR="$PWD/.uv-cache" uv run coverage report
  ```

- If `.uv-cache` is not present, create it before running commands. Keep it project-local and persistent between agent
  turns; do not use a temporary directory unless the project-local path is unavailable.

## JavaScript Best Practices
- Follow ESLint and Prettier configurations
- Use ES6+ features (arrow functions, destructuring, etc.)
- Prefer const over let, avoid var
- Use async/await for asynchronous operations
- Use template literals for string concatenation

## Design Choices
- If a design decision is given by the user that generally applies, add it to AGENTS.md.
- Complexity leans towards deterministic code and away from the LLM where appropriate. Reasoning belongs in the LLM.
- Generic controller, memory, and acceptance code must be protocol-, target-, tool-, and model-neutral. Put
  module-specific behavior in typed adapters, operation plugins, or declarative catalogs.
- Structured metadata is authoritative for workflow control. Do not infer phase dependencies, task kinds, evidence
  kinds, or scope from titles, filenames, generated prose, or incidental tool-output wording.
- Keep evidence availability separate from semantic conclusions. Readability, non-empty content, status codes, and
  successful tool execution establish availability only; they do not independently prove support or contradiction.
- Automatic semantic dispositions require a narrow declarative validation rule. A contradiction rule must match every
  cited artifact before Python may record a negative disposition.
- Canonicalization may normalize syntax or complete an unambiguous logical or relative reference. It must not silently
  change an explicit service authority, network identity, or filesystem root for execution authorization. A
  non-authorizing persisted record may correct one unique, conservatively matched target typo when the correction is
  logged and canonicalized to an already assigned target.
- Deterministic repair must be information-preserving and unambiguous. If multiple corrections remain possible, give
  bounded recovery the valid choices instead of guessing or applying every candidate.
- A failed task becomes `superseded` only after linked replacement tasks conclusively cover its frozen acceptance
  criteria. Count superseded work as recovered terminal history, not as a phase or operation failure, while preserving
  the original status reason and replacement lineage for audit.
- Optional LLM metadata must not terminate an operation. Validate it inside a bounded semantic repair path; after one
  failed semantic repair, preserve evidence, emit structured failure telemetry, and contain the failure to that task.
- Recovery progress requires new durable state appropriate to the frozen acceptance contract. Reasoning cycles,
  read-only calls, catalog inspection, and repeated artifact reads are not evidence progress.
- State whether a bounded repair permits one model cycle or one tool invocation. Tool-constrained workflow recovery
  means one tool invocation unless its contract explicitly says otherwise.
- Repetition detection must use canonical inputs, outputs, references, and content fingerprints instead of hardcoded
  producer names or filename prefixes.
- Tool-schema changes should be additive where practical. Normalize supported legacy inputs at the boundary, use
  canonical forms internally, and reject unknown values.
- Generic workflow tests must cover representative HTTP, network, and source/filesystem cases. Incident fixtures may
  reproduce a particular model, target, or tool failure, but assertions must test the general invariant rather than
  making that model's wording, target's paths, or tool's artifact names universal behavior.
- `src/modules/config/system/finding_validation_guards.yaml` is the declarative catalog for narrow, unambiguous
  finding-validation rules. Update it when a finding class needs either a deterministic rejection based on
  contradiction markers present in every cited artifact, or a required positive validation mode such as response
  comparison or a rate-limit probe. Do not use it for heuristic or uncertain vulnerability inference; preserve its
  small, evidence-backed scope and add tests for both a matching and a non-matching candidate/artifact case.
- Fixed-enum tool schemas must advertise canonical values while pre-processing common semantic synonyms into those canonical values; unknown values remain invalid. Strands tool runtime validation strips `Annotated[BeforeValidator(...)]` metadata when it rebuilds a tool input model, so tool-facing aliased enum parameters must use string runtime annotations, provide an explicit canonical `inputSchema`, and normalize plus strictly validate inside the function. Add runtime-schema tests; Pydantic-only direct-call tests are insufficient.
- Reporting/evaluation budget reserves may reserve tokens and cost only. Duration/time must not be reserved for reporting or evaluation.
- Report `Total Operation Time` is assessment execution time only: it ends when execution terminates and excludes
  report generation and evaluation.
- Budget of any kind may be exceeded while required reporting completes.
- Planning and task fan-out are independent of operation budget constraints. Budgets govern execution, evaluation, and termination, not the number of planned task records; there should be no budget-aware scheduling.
- The workflow should prefer new task creation to reduce complexity in the LLM as long as losing the LLM context does not reduce precision. Producing artifacts from one task to feed another task is encouraged.

## Application Best Practices
- Budget is reporting only after reporting or evaluation stages are reached.

## LLM Best Practices
- Design considerations should prefer deterministic actions in Python or Typescript code, reasoning actions in agents/LLM.
- When providing to an LLM a list of data with two or more items that have the same shape, prefer TOON over JSON.
- When an LLM is to return structured data, prefer JSON.
- Primary use-case LLM have ~26b parameters and 48,000 tokens context window.
- CWE mappings describe software weaknesses and may use deterministic candidate seeds derived from vulnerability
  terminology. MITRE ATT&CK mappings describe observed adversary behavior and should be inferred only from evidence
  such as execution traces, commands, access paths, or post-exploitation activity; a vulnerability alone does not
  imply an ATT&CK technique. Require artifact-backed evidence and confidence thresholds, and omit uncertain ATT&CK
  mappings rather than guessing. Threat-emulation modules may use ATT&CK-first reasoning because their workflows
  explicitly produce behavioral evidence.

## User Interface
- When considering user interface changes, there is a React Terminal UI and a headless/console UI in index.tsx.

## Cyber Operations Log Review
- Read the session header and tail first to establish the operation ID, start/end time, final metrics, budget limits, termination reason, and `assessment_complete` event.
- Use `rg -n` with narrow, case-insensitive patterns to locate phase transitions, task creation/evaluation, budget-limit events, `workflow_coverage_summary`, `progress_update`, and `assessment_complete`; avoid dumping the entire log because reasoning payloads can be very large.
- Inspect targeted line-numbered ranges with `sed -n` around each phase transition and the final completion block. Preserve exact line numbers when reporting findings so conclusions are auditable.
- Reconcile the plan with execution: compare planned phase count to applicable phases, phase statuses, task counts, and per-task status counts. Treat `not_applicable`, omitted inventory items, and `partial_failure` as explicit coverage results rather than assuming completion means exhaustive work.
- Cross-check `workflow_coverage_summary` against final health. In particular, check `applicable_phase_count`, `phase_inconsistent`, failure counts, and the validation-candidate rationale; an excellent health score can coexist with a skipped phase or incomplete coverage.
- Distinguish logical completion from resource termination. Compare elapsed duration with `maxDurationMinutes`, and inspect token/cost limits and `termination_reason`; `progressPercent` is budget/utilization progress, not phase completion.
- Do not recommend changing task fan-out based on lack of budget. Lack of budget is for the user to control.
- Broken or missing tools are acceptable, the operation will remove them from consideration, do not flag or offer plans to address.
- Do not complain or recommend changes because reporting or evaluation taking time and that it should be budgeted.
- Do not complain or recommend changes because an operation continues to run when it knows from the health status that it will not finish. Coverage is most important, the user can continue the operation later, and the report already recommends to the user how much more budget to allocate.
