# Task Tracking System

The Task Tracking System implements **phase-aware persistent state** using a local SQLite database. This enables long-running operations to maintain coverage, avoid context loss, and reliably progress through complex work by decoupling task state from ephemeral LLM context.

## Design Philosophy: Work Queues for Continuous Progress

The core philosophy centers on **externalizing intent into durable tasks**, transforming ephemeral model attention into an explicit queue that survives context pruning, long tool output, and multi-phase execution.

Long operations degrade without a durable work queue:
- **Context Loss**: discovered threads fall out of the window as tools run and outputs accumulate
- **Thread Collapse**: multiple distinct leads get merged into one “next step”
- **Premature Phase Progression**: agents move phases after a single success while work remains
- **Stop-Too-Early**: recon objectives stop after first validated vuln even when mapping tasks remain

### Task Tracking Persistence

Tasks and plans are stored in a dedicated SQLite database (`plan_store.db`) co-located with the operation's vector memory. This ensures:
- **ACID Compliance**: Reliable task state transitions even during system crashes.
- **Relational Queries**: Efficient filtering and sorting of tasks by phase, status, and creation time.
- **Fuzzy Matching**: Duplicate detection uses fuzzy string comparison (`rapidfuzz`) to prevent redundant work when the model generates slightly different task descriptions for the same objective.

### The Task-First Approach

The system enforces a loop of:
- **Capture**: extract all actionable threads into tasks
- **Execute**: work one active task at a time within the current plan phase
- **Close**: record completion state with status + reason and activate next work
- **Preserve**: keep active task + related evidence context durable across pruning

---

## Architecture

```mermaid
graph TB
    A[Operation Start] --> B[mem0_store_plan]
    B --> C[Seed DISCOVERY tasks]
    C --> D[Task Capture Pass]
    D --> E[mem0_get_active_task]
    E -->|task|null? F[Create 1-3 tasks from observations]
    F --> E

    E -->|task active| G[Execute task.objective]
    G --> H[Store artifacts + mem0_store observations/findings]
    H --> I[Task Capture Pass if new evidence]
    I --> J[mem0_task_done status + status_reason]
    J --> E

    E --> K{Checkpoint 20/40/60/80%?}
    K -->|Yes| L[mem0_get_plan]
    L --> M[Evaluate criteria vs evidence]
    M -->|Advance| N[mem0_store_plan current_phase++]
    M -->|Continue| E

    style D fill:#f3e5f5,stroke:#333,stroke-width:2px
    style E fill:#e3f2fd,stroke:#333,stroke-width:2px
    style J fill:#e8f5e9,stroke:#333,stroke-width:2px
```

---

## Core Concepts

### Plan vs Tasks

- **Plan**: defines *phases* and *criteria*. Updated only at checkpoints.
- **Tasks**: fine-grained work items that satisfy phase criteria; persisted to memory; executed one at a time.

> Plan = “what phase and what completion criteria”  
> Tasks = “what concrete work to do next”

### Task State Model

A task has:
- `title`: short label
- `objective`: concrete work instruction
- `evidence`: list of strings (artifact path references; may include `:line` / `#anchor`)
- `phase`: integer mapped to active plan phases
- `status`: `active | pending | done | partial_failure | blocked`
- `status_reason`: why the status is what it is (especially for partial_failure/blocked)

---

## Data Model

### Task Object

```json
{
  "task_uid": "uuid",
  "title": "Discover endpoints",
  "objective": "Enumerate all reachable URLs on target; store endpoints list artifact.",
  "evidence": [
    "/Users/.../artifacts/recon123.txt",
    "/Users/.../artifacts/recon456.txt:56",
    "/Users/.../artifacts/recon789.txt:57-78"
  ],
  "phase": 1,
  "status": "active",
  "status_reason": "Seed mapping task created from initial scope."
}
```

---

## Tool Actions and Operations

### Task Tools

The task system uses memory-backed tools:

| Tool                                                | Purpose                                   | Notes                           |
|-----------------------------------------------------|-------------------------------------------|---------------------------------|
| `mem0_create_tasks(tasks=[...])`                    | Batch create tasks                        | preferred during capture pass   |
| `mem0_get_active_task()`                            | Return active task for current phase      | canonical task selection        |
| `mem0_task_done(status, task_uid?, status_reason?)` | Close a task + activate next (same phase) | tool enforces phase gating      |
| `mem0_list()`                                       | Retrieve observations/findings/tasks      | used when no active task exists |

**Phase gating**:
- selection and advancement tools operate only on `current_phase`
- tasks may exist for future phases, but are not executed until their phase becomes current

---

## Task Capture Pass

### Motivation
Task capture ensures new information becomes durable work items before execution consumes context.

### Trigger Conditions
A capture pass runs after:
- loading memories
- any substantial tool output
- phase changes
- hypothesis changes
- storing substantial observation/finding (new evidence)

### Fixed-Point Algorithm
1) Enumerate candidate threads from:
   - memory_context, plan, existing tasks, findings/observations, fresh tool outputs
2) Create **one task per thread**
3) Repeat until a **no-new-tasks pass**

**No-new-tasks pass definition**:
- you reviewed the *new evidence* and either created all implied tasks or determined none can be created from it

---

## Execution Loop

### Current Phase Work Loop

1) Task Capture Pass → no-new-tasks  
2) `mem0_get_active_task()`  
3) If task: execute `task.objective` (minimal steps; one variable per test)  
4) Store artifacts + mem0_store observation/finding  
5) If new info: Task Capture Pass again  
6) Close via `mem0_task_done(status=done|partial_failure|blocked, status_reason, task_uid?)`  
7) Repeat until `mem0_get_active_task()` returns null for current_phase or checkpoint triggers

### No Active Task Recovery

If `mem0_get_active_task()` returns null:
- call `mem0_list()` (focus on `category="observation"`)
- derive 1–3 DISCOVERY tasks from the highest-signal observations (include evidence paths)
- call `mem0_get_active_task()` again

---

## Context Management and State Preservation

Long runs require aggressive pruning; the task system remains reliable by preserving state markers and evidence context.

### State Markers

- `<active_task ...>...</active_task>`: canonical current task state, emitted by:
  - `mem0_get_active_task` results
  - `mem0_task_done` results
- Plan state is preserved by keeping the most recent toolResult of:
  - `mem0_get_plan` or `mem0_store_plan`

### Preservation Rules

The conversation manager ensures:
- only the most recent `<active_task>` message remains (dedupe)
  - messages referencing any `task.evidence` path tokens (full path + basename)
- only the most recent plan toolResult remains (dedupe)

---

## UI Events and Telemetry

To keep the UI synchronized with task state:

- **task_activated**: emitted when either `mem0_get_active_task` or `mem0_task_done` result is received and the active `task_uid` changes.
  - includes `task_uid`, `title`, `status`

- **task_done**: emitted when `mem0_task_done` is called.
  - best when mem0_task_done output includes closure provenance (e.g., `closed.task_uid/status` or tool input contains `task_uid/status`)

This provides a reliable timeline for operators without requiring the model to infer state.

---

## Failure Handling

### Anti-Stall Rule
If the same objective fails twice with no new evidence:
- close as `partial_failure`
- set `status_reason` (constraint type + evidence)
- create a new task using a different capability class

### Blocked Tasks
Use `blocked` when a dependency prevents progress (missing auth, missing capability, prerequisite phase info).
- include `status_reason` to allow future resumption

---

## Implementation Components

| Component             | File                      | Purpose                                                           |
|-----------------------|---------------------------|-------------------------------------------------------------------|
| Task models + tools   | `memory.py`               | TaskCreate normalization, task storage, mem0 task tools           |
| Prompt directives     | `system_prompt.md`        | Task capture + execution loop + invariants                        |
| Prompt assembly       | `factory.py`              | Startup guidance (seed tasks, capture gates)                      |
| Context management    | `conversation_budget.py`  | Dedupe/preserve `<active_task>` + plan toolResult + evidence refs |
| UI event bridge       | `react_bridge_handler.py` | Emit `task_activated` / `task_done` events                        |

---

## Summary

The Task Tracking System externalizes operational work into a durable, phase-aware queue:

- Tasks are captured to saturation from new evidence
- Execution is single-active-task, phase-gated
- Completion is explicit with status + reason
- Context pruning preserves the active task and the evidence needed to continue
- Pending work can persist across runs to enable long-lived coverage goals

**Key innovation**: a fixed-point task capture pass + durable state markers that survive context reduction, enabling sustained multi-phase operations without losing threads.
