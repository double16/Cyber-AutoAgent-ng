
**Purpose**: Externalized work queue. You may create tasks when the current role asks for task capture, do not execute created tasks.

## Task spec
- Fields: `title`, `objective`, `evidence`, optional `phase`, `status=active|pending|done|partial_failure|blocked`, `status_reason`.
- `objective`: what to accomplish / problem to solve / more info to gather.
- `evidence`: list of artifact path refs that motivated the task (paths may include `:line`/`#anchor`).

## Create tasks
Use batch creation only when the role prompt permits task creation:
- `create_tasks(tasks=[{title, objective, evidence:[...], phase, status}, ...])`

When to create:
- DISCOVERY: new surface/endpoint/path/file/host needs exploration
- HYPOTHESIS: potential vuln/issue/mis-config/cve
- VALIDATION: repro, control case, confirm impact
- FINDING: proof pack
- CHAINING: each chain step

Defaults:
- If the correct phase is known, set it explicitly. Missing or invalid phases default to the current plan phase.
- Use `status=pending`. Do not activate, close, or otherwise mutate task status.

## Task Capture Pass (MANDATORY)
Trigger after: any tool output or hypothesis change.

Algorithm (fixed-point):
1) Enumerate candidate threads from: memory_context, plan, existing tasks, findings/observations, fresh tool output.
2) Create 1 task per thread (do not merge unrelated threads). Prefer full capture of all implied candidates.
3) Repeat until a **no-new-tasks pass**.

No-new-tasks pass definition: you reviewed the *new* evidence and either created all implied tasks or determined none can be created from it.

Fan-out rules (MUST create multiple tasks when lists exist):
- Endpoints/paths → ≥1 task per set of parameterized paths.
- Params/injection points → ≥1 task per parameter/point.
- Host → ≥1 task per host.
- Tech/Version → ≥1 task per tech/version.
- Multiple vuln classes → ≥1 task per class per endpoint/path/param/host.
- Multiple auth flows/roles/resources → ≥1 task per flow/role/resource.

## Pruning Prohibition (STRICT)
- You MUST NOT reduce task creation counts due to likelihood, convenience, or "most common" issues.
- The ONLY valid reasons to not create a task for an in-scope candidate are: out-of-scope, unreachable with artifact proof, or exact duplicate.
- If a page yields >=10 distinct in-scope candidates (e.g., endpoints), create tasks for ALL of them (batch if needed).

Capture invariants:
- Existing tasks do NOT satisfy capture; rerun after new evidence even if it yields 0 tasks.
- You MAY also create future-phase tasks (phase>current_phase) **in the same pass** if evidence implies them, but they must remain `pending`.
- Capture is tasks-only (no heavy tool runs).

**Clarification: capture vs execute**
- Task Capture Pass is allowed to create tasks for follow up work.
- Execution is allowed only for the task objective provided by the role prompt.

Anti-stall: if the same objective fails twice with no new evidence, explain the blocker and pivot to a different capability class when the role prompt asks you to continue.

Pivot rule: If the role prompt says prior work was partial or blocked, use a different capability class.
