## Critical rules the agent must follow before doing anything
- Read `README.md` before acting.
- Update `CHANGELOG.md` for user-facing changes.

## Testing and contribution
- Always write unit tests and check that they pass for new business logic.
- Always run unit tests to verify changes.
- Test both positive and negative scenarios.
- Keep tests in files based on component or functionality. Use existing test files if applicable.
- Do not rename files without a valid technical reason.

## Explicit prohibitions what agents must NOT do
- Do not bump major versions of core dependencies without a dedicated PR and discussion.

## Python Best Practices
- Use the `uv` tool for python ecosystem, i.e `uv run ...`
- Follow PEP 8 with a 120-character line limit
- Use double quotes for Python strings
- Sort imports with `isort`
- Use f-strings for string formatting

## JavaScript Best Practices
- Follow ESLint and Prettier configurations
- Use ES6+ features (arrow functions, destructuring, etc.)
- Prefer const over let, avoid var
- Use async/await for asynchronous operations
- Use template literals for string concatenation
