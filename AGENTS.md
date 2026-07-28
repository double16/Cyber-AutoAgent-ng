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

## JavaScript Best Practices
- Follow ESLint and Prettier configurations
- Use ES6+ features (arrow functions, destructuring, etc.)
- Prefer const over let, avoid var
- Use async/await for asynchronous operations
- Use template literals for string concatenation

## Design Choices
- Fixed-enum tool schemas must advertise canonical values while pre-processing common semantic synonyms into those canonical values; unknown values remain invalid. Strands tool runtime validation strips `Annotated[BeforeValidator(...)]` metadata when it rebuilds a tool input model, so tool-facing aliased enum parameters must use string runtime annotations, provide an explicit canonical `inputSchema`, and normalize plus strictly validate inside the function. Add runtime-schema tests; Pydantic-only direct-call tests are insufficient.
- Reporting/evaluation budget reserves may reserve tokens and cost only. Duration/time must not be reserved for reporting or evaluation.
- Budget of any kind may be exceeded while required reporting completes.
- Planning and task fan-out are independent of operation budget constraints. Budgets govern execution, evaluation, and termination, not the number of planned task records; there should be no budget-aware scheduling.

## Application Best Practices
- Budget is reporting only after reporting or evaluation stages are reached.

## LLM Best Practices
- Design considerations should prefer deterministic actions in Python or Typescript code, reasoning actions in agents/LLM.
- When providing to an LLM a list of data with two or more items that have the same shape, prefer TOON over JSON.
- When an LLM is to return structured data, prefer JSON.
- Primary use-case LLM have ~26b parameters and 48,000 tokens context window.

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
