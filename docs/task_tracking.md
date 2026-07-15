# Task Tracking and Workflow

The task tracking system implements **phase-aware persistent state** using a local SQLite database. The top-level workflow is now owned by Python code, not by a long-lived orchestrator agent prompt. Short-lived role agents work on narrow objectives, while Python applies plan, phase, and task state transitions.

## Design Philosophy: Python-Owned Workflow

Long operations degrade when a single model conversation owns all orchestration. The current design separates durable workflow control from model reasoning:

- **Python controller**: owns plan creation/recovery, active phase selection, task activation, phase advancement, budget checks, and task closure.
- **Short-lived agents**: create plans, build prompts, execute task objectives, create new tasks when permitted, and evaluate task/phase outcomes.
- **SQLite plan store**: persists plans and tasks across context pruning, model failures, and continued runs.
- **Mem0 memory**: stores semantic memories such as observations, findings, evidence summaries, and lessons.

This keeps strategic state deterministic while still using agents for security reasoning and prompt tailoring.

## Workflow Roles

The workflow controller creates focused agents as needed:

| Role | Purpose | Can mutate plan/task state? |
|------|---------|-----------------------------|
| `plan_creator` | Create or revise an initial high-level plan | No; returns structured plan data for Python to store |
| `plan_critic` | Approve a proposed plan or return actionable revision feedback | No |
| `task_creator` | Create concrete tasks for current and future phases | May call `create_tasks` only |
| `task_prompt_builder` | Build a task-specific execution prompt and select applicable memory/tools | No |
| `task_prompt_critic` | Approve a proposed task prompt or return actionable revision feedback | No |
| `task_executor` | Execute one active task objective | May call `create_tasks` for follow-up work |
| `task_evaluator` | Decide whether the active task is `done`, `partial_failure`, or `blocked` | No; returns a structured decision |
| `phase_evaluator` | Decide whether the active phase should continue or become terminal | No; returns a structured decision |

Task and phase evaluators are review-only roles. They receive only `editor` for reading referenced artifacts and
`mem0_retrieve` for reviewing existing memories. They do not receive shell or execution tools and must not perform the
task, phase, or operation objective while classifying existing evidence.

Agents may create follow-up work with `create_tasks` when their role permits it. Plan reads/writes, task activation, active-task lookup, task closure, and uncompleted-task listing are applied directly by Python rather than agent-callable tools.

The task creator receives a deterministic controller-owned prompt and payload contract. Every task requires a
non-empty `title` and `objective`; supported optional fields are `phase`, `status`, and `evidence`. Context belongs in
the objective rather than an unsupported `context` or `description` field. The controller permits one bounded repair
attempt when an empty phase receives no durable tasks, and it never retries after tasks are successfully stored.
Valid future-phase IDs are preserved so useful follow-up work can be planned early. Missing, malformed, or nonexistent
phase IDs default to the active phase, as do IDs for phases earlier than the active phase.

Agents also do not have a stop tool. Operation completion is a Python workflow decision; the controller emits a `termination_reason` event with reason `complete`.

The task executor's workflow boundary is controller-owned and shared by every module. Module prompts add distinct access,
safety, domain-execution, and evidence policies without redefining task lifecycle behavior. Module termination policies
are planning inputs as well as phase-evaluation criteria, ensuring that required outcomes shape the durable phase plan.

## State Model

### Plan Phase Status

Plan phases support:

- `active`: the phase currently being worked
- `pending`: future phase not yet active
- `done`: phase objective was achieved
- `partial_failure`: phase made useful progress but could not fully satisfy criteria within constraints
- `blocked`: phase cannot proceed because of a dependency or hard blocker

`done`, `partial_failure`, and `blocked` are terminal phase states. `done` remains the successful completion state.

### Task Status

Tasks support:

- `active`: the task currently being executed
- `pending`: queued work
- `done`: task objective achieved
- `partial_failure`: task made useful progress but did not fully achieve the objective
- `blocked`: task cannot proceed because of a missing dependency, authorization, capability, or prerequisite

Task evaluators decide terminal task status, but Python stores that status.

## Data Model

### Plan Object

```json
{
  "objective": "Assess the web application",
  "current_phase": 1,
  "total_phases": 3,
  "assessment_complete": false,
  "phases": [
    {"id": 1, "title": "Reconnaissance", "status": "active", "criteria": "Map reachable attack surface"},
    {"id": 2, "title": "Validation", "status": "pending", "criteria": "Validate high-signal hypotheses"},
    {"id": 3, "title": "Reporting Prep", "status": "pending", "criteria": "Ensure evidence is complete"}
  ]
}
```

Plans are high-level and do not reference specific tools.

### Task Object

```json
{
  "task_uid": "uuid",
  "title": "Enumerate login flows",
  "objective": "Identify reachable authentication endpoints and store evidence paths.",
  "evidence": ["/outputs/example/OP_.../artifacts/recon.txt:42"],
  "phase": 1,
  "status": "pending",
  "status_reason": "Created from initial recon evidence."
}
```

Tasks are concrete units of work. They may reference evidence paths and memory context.

## Controller Loop

The controller runs this loop:

1. Load the current plan.
2. If no plan exists, run `plan_creator`, then apply the configured critic/revision cycle before storing an approved
   plan. A final critic rejection fails the workflow without persisting the draft.
3. If a plan was previously marked complete at startup, reopen it by making phase 1 active and later phases pending.
4. Ensure exactly one active phase, or mark the plan complete if all phases are terminal.
5. If an active task exists for the active phase, run it first.
6. If no active task exists, choose the next pending task unless the phase is at or beyond its soft budget cap.
7. If no task should be activated, run `phase_evaluator`.
8. If the phase evaluator returns a terminal status, Python marks the phase and activates the next pending phase.
9. If the phase should continue but has no active/pending task, run task creation.
10. If task creation produces no tasks for an empty phase, raise a workflow invariant error.
11. For each active task:
    - run `task_prompt_builder`, then apply the configured critic/revision cycle
    - run `task_executor` with restricted tools
    - run `task_evaluator`
    - Python marks the task terminal
    - loop back to active phase/task selection
12. When all phases are terminal, Python marks the plan complete and emits the completion `termination_reason` event for UI consumers.

Task prompt refinement is controlled by `CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS`, which defaults to two
critic reviews. Setting it to `0` uses the initial builder output without critique. A final rejection or invalid
builder/critic response after configured JSON retries marks the active task `partial_failure`; the executor and
evaluator are not invoked for that task.

After creating or durably changing a plan, the controller also emits a standard `output` event containing the
objective, current phase, and status of every phase. These snapshots appear in interactive and headless output.
Unchanged plan reads do not emit an event, and display failures do not interrupt persistence or workflow execution.

## Budget Policy

Budget progress is distributed across phases using mandatory proportional caps:

```text
phase_cap = phase_id / total_phases * 100
```

When a phase reaches its cap, the controller performs no more task work for that phase. An active task is marked
`partial_failure`, pending tasks remain pending, and `phase_evaluator` must return `done`, `partial_failure`, or
`blocked` before Python advances the plan. If terminal evaluation fails, Python closes the phase as `partial_failure`
so the cap cannot be bypassed.

The controller also tracks advisory budget checkpoints at 20%, 40%, 60%, 80%, and 90%. Below the phase cap, crossing
a checkpoint asks the phase evaluator whether continuing the current phase is still the best use of remaining budget.
These checkpoints may return `continue` and are not injected as prompt instructions.

This design prefers reaching all phases and leaving some tasks pending over spending too much budget on one phase.

## Tool Policy

Worker agents receive restricted tool lists.

Core tools are always available to appropriate workers:

- shell and execution helpers
- memory retrieval and storage tools
- browser/channel/OAST basics when included in core runtime setup
- `tool_catalog`

Optional tools are selected as needed:

- module-specific tools
- MCP tools
- any other discovered non-core tools

Selection happens in two passes:

1. Python narrows candidates based on objective, phase/task context, available tools, MCP metadata, and memories.
2. A prompt-builder agent sees separate `core_tools` and `optional_tools` TOON catalogs, then selects the final
   applicable optional tools and memory references. Core capabilities are supplied automatically and are not returned
   in the prompt-builder's `tools` selection.

When shell is available, the prompt-builder also receives a compact `shell_commands` TOON catalog containing installed
command names, bounded descriptions, capabilities, and shell-only preferred/fallback metadata. The builder may select
every tool and command reasonably applicable to the task, including capabilities that overlap across native, optional,
and shell methods. Overlap, apparent redundancy, and selection count are not critic rejection reasons; selection makes
a capability available without requiring its use or excluding another method. `shell_preference` is advisory ranking
among shell commands only. Selected commands do not restrict shell execution or replace runtime `tool_catalog`
discovery.

Prompt-builder agents also receive compact task history. Successful tasks become useful context for prioritizing similar paths, while `partial_failure` and `blocked` tasks provide dead-end context so workers can pivot without rewriting module prompts on disk.

`create_tasks` is exposed only to task creation roles and task executors that may create follow-up work. Other plan/task mutation tools are withheld from worker agents.

## Task Creation

Task creation still uses the `create_tasks` tool so agents can turn discoveries into durable work. There is no separate active-task fetch tool; active task context is selected by Python and included in the task executor prompt. The rules remain:

- create one task per distinct actionable thread
- use `status=pending`
- set `phase` explicitly when known; missing or invalid values use the current plan phase
- include evidence paths where available
- do not create duplicates
- do not reduce task coverage based only on likelihood or convenience

Python decides when any pending task becomes active.

`create_tasks` returns only `Tasks created.`. It does not activate pending tasks or return active-task XML.

## Evaluation

Task and phase closure is evaluator-driven but Python-applied.

`task_evaluator` returns:

```json
{"status": "done|partial_failure|blocked", "reason": "short evidence-based reason"}
```

`phase_evaluator` returns:

```json
{"status": "continue|done|partial_failure|blocked", "reason": "short evidence-based reason"}
```

Invalid statuses are treated as workflow errors.

## Context Management

Short-lived agents reduce dependence on preserving one huge conversation. Each worker prompt is built from:

- base Cyber-AutoAgent system prompt
- module execution guidance
- current plan and active phase objective
- active task objective, when applicable
- relevant mem0 items
- selected tool names and short descriptions

Conversation pruning still protects useful evidence and memory context, but plan/task authority lives in SQLite and Python helpers rather than prompt state.

## UI Events and Telemetry

Tool events still flow through the React event handler. Task activation and task closure are Python workflow decisions rather than model tool calls.

The UI should treat plan/task state as controller-owned state and tool events as supporting evidence.

## Implementation Components

| Component                    | File                                             | Purpose                                                           |
|------------------------------|--------------------------------------------------|-------------------------------------------------------------------|
| Workflow controller          | `src/modules/agents/multi_agent_workflow.py`     | Python-owned phase/task loop and role-agent coordination          |
| Agent runtime resources      | `src/modules/agents/cyber_autoagent.py`          | Shared memory, handlers, hooks, tool lists, prompt payloads       |
| Plan/task models and storage | `src/modules/tools/memory.py`                    | SQLite persistence and `create_tasks` compatibility tool          |
| Base prompt                  | `src/modules/prompts/templates/system_prompt.md` | Methodology, evidence discipline                                  |
| Task Capture                 | `src/modules/prompts/templates/task_capture.md`  | Task creation guidance                                            |
| Prompt factory               | `src/modules/prompts/factory.py`                 | Memory guidance and prompt assembly                               |
| CLI entry point              | `src/cyberautoagent.py`                          | Runtime setup, fatal failure handling, report generation, cleanup |

## Summary

The task system now combines durable state with multi-agent execution:

- Python owns state transitions.
- Agents do short, defined work.
- Active tasks run before pending tasks.
- Budget progress is a soft phase cap.
- `create_tasks` remains available for durable work capture.
- Phase and task terminal states are explicit: `done`, `partial_failure`, and `blocked`.
