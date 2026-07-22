# Agent Architecture

Cyber-AutoAgent implements a **Python-orchestrated multi-agent workflow** using the Strands framework for autonomous security assessment.

## Design Philosophy: Python-Owned Workflow, Focused Agents

The core design philosophy centers on deterministic Python ownership of workflow state with short-lived agents assigned to narrow, well-defined roles.

### Why Python-Owned Orchestration?

Long-lived autonomous conversations tend to accumulate stale assumptions, lose context, and mutate plan/task state inconsistently. Cyber-AutoAgent keeps the durable operation loop in Python:

- plans, phases, and task statuses are stored in SQLite
- Python chooses the active phase and task
- Python applies task and phase evaluator decisions
- Python controls budget-aware phase progression
- agents handle reasoning, prompt tailoring, task execution, task creation, and evaluation

### Focused Agent Roles

The controller creates agents for specific jobs:

- **plan_creator**: creates or revises an initial high-level plan and infers operation-wide constraints
- **plan_critic**: reviews an initial plan and either approves it or returns actionable revision feedback
- **task_creator**: creates concrete current-phase tasks with finite acceptance contracts from a deterministic
  controller prompt
- **task_prompt_builder**: reviews core, optional-tool, and installed shell-command catalogs, then selects applicable
  memory, optional tools, and likely commands for one task
- **task_prompt_critic**: approves a proposed task execution prompt or returns actionable revision feedback
- **task_executor**: executes one active task objective and retains its conversation across critic-guided passes
- **task_evaluator**: reviews semantically complete acceptance ledgers and returns `done`, `partial_failure`, or `blocked`
- **phase_evaluator**: returns phase status: `continue`, `done`, `partial_failure`, or `blocked`

Module execution guidance supplies operation intent, explicit access boundaries, domain behavior, and evidence rules to
planning and execution roles. The module termination policy is also supplied directly to plan creation, plan criticism,
plan revision, and phase evaluation so required end states become measurable plan criteria. A controller-owned executor
contract keeps individual workers scoped to one task regardless of module.

Each task also has an immutable acceptance manifest. Procedure bases declare machine-readable limits and whether they
produce a generic artifact or versioned inventory manifest; coverage bases freeze one inventory artifact and its hash.
Executors submit an atomic evidence-backed result for every criterion and, for coverage work, one terminal disposition
per manifest item. Python resolves typed artifact, memory, observation, and finding evidence before semantic
evaluation. This preserves broad, cohesive work under one retained executor without allowing moving completion
targets.

```mermaid
flowchart LR
    M[Missing or invalid snapshot] --> R[Reject coverage task batch]
    R --> P[Create bounded prerequisite inventory task]
    P --> V[Validate and hash version-1 manifest]
    V --> C[Create coverage tasks against frozen snapshot]
```

The swarm tool remains available as an execution capability, but it is no longer the top-level orchestration model.

## Core Architecture

```mermaid
graph TB
    A[User Input] --> B[Cyber-AutoAgent]
    B --> C[Python Workflow Controller]
    C --> D[SQLite Plan/Task Store]
    C --> E[Runtime Resources]
    C --> F[Short-Lived Role Agents]
    F --> G[Restricted Tool Registry]
    F --> H[Memory System]
    F --> I[AI Models]
    
    G --> J[shell]
    G --> K[Typed memory tools / mem0_retrieve]
    G --> L[create_tasks when allowed]
    G --> M[Selected Module Tools]
    G --> N[Selected MCP Tools]
    G --> O[swarm when selected]
    
    J --> P[Security Tools]
    P --> Q[nmap, sqlmap, etc.]
    P --> R[self install package]
 
    style B fill:#f3e5f5,stroke:#333,stroke-width:2px
    style C fill:#fff3e0,stroke:#333,stroke-width:2px
    style F fill:#e8f5e8,stroke:#333,stroke-width:2px
```

## Strands Tools

Agents operate through role-specific restricted tool lists.

### Primary Tools
- **shell**: Execute system commands (nmap, sqlmap, custom scripts)
- **editor**: Create/modify files and custom tools
- **swarm**: Deploy parallel agents for complex tasks
- **http_request**: Make HTTP requests for web testing
- **mem0_...**: Store/retrieve findings and knowledge
- **load_tool**: Dynamically load created tools
- **create_tasks**: Create durable tasks when a role permits task creation

Plan/task state transitions are owned by Python workflow code. Agents can only add follow-up work with `create_tasks` when their role permits it. Active task context is injected into the role prompt by the workflow; agents do not fetch it with a tool.

There is no agent-callable stop tool. When Python workflow evaluation determines the assessment is complete, the controller emits a `termination_reason` event with reason `complete`.

Generated plan constraints are durable workflow guardrails. Task creation and prompt-building roles must honor them,
and task/phase evaluators prevent successful completion when evidence shows a constraint violation.

Initial plan generation uses a bounded actor/critic cycle before persistence. Critic approval immediately accepts the
current draft; rejection sends feedback to `plan_creator` for revision. The
`CYBER_WORKFLOW_PLAN_REFINEMENT_ITERATIONS` environment variable limits reviews and defaults to three. A rejection on
the final configured review fails the workflow so an unapproved plan is never persisted or executed.

Task prompt generation uses the same bounded pattern. `CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS` defaults to
two critic reviews and accepts `0` to disable critique. A final rejection or invalid structured response after JSON
retries marks only the active task `partial_failure`, without invoking its executor or evaluator.

Task execution then uses a second bounded actor/critic loop:

```mermaid
flowchart LR
    E[Retained task-executor] --> V[Fresh task-evaluator]
    V -->|done| D[Persist done]
    V -->|partial_failure or blocked| F[Persist final verdict]
    E -->|no valid complete ledger; pass remains| E
    E -->|cycle limit reached| F
```

`CYBER_WORKFLOW_TASK_EXECUTION_CYCLES` limits executor attempts to produce a valid atomic ledger and defaults to three,
with a minimum of one. Once that ledger exists, the evaluator's semantic verdict is terminal because the ledger cannot
be revised. Python persists `done`, `partial_failure`, or `blocked` without replaying completed acceptance.

Task creators stop within the Strands event loop after the first successful `create_tasks` mutation. Task executors
stop the same way after a complete `record_task_acceptance` result. Rejected calls do not set the success marker and
remain correctable; role completion therefore depends on durable success rather than raw tool-call counts or a later
text-only turn.

Bounded procedure contracts declare whether their output is a generic artifact or a version-1 inventory manifest.
Python rejects mismatched evidence requirements during task creation. Inventory executors receive the canonical JSON
shape in both their task prompt and acceptance-tool description, and acceptance validation evaluates each referenced
artifact independently so an unrelated supporting file cannot invalidate a separate valid inventory.

Each retained task executor also keeps a bounded controller-owned tool-outcome journal. A locally correctable failure,
such as a missing input file, unavailable command, invalid argument, or timeout, permits one optional diagnostic and
one corrected invocation without consuming another actor/critic pass. Until correction succeeds, the executor cannot
create follow-up tasks or write observations, knowledge, findings, or validation results. An unresolved correction is
deterministically `partial_failure`. Evaluators receive the authoritative outcome journal separately from the worker's
final narrative and must prefer it when the two conflict. Failed diagnostic and preflight shell commands remain visible
in the journal but do not start recovery; only failed task actions can open the bounded correction path. A failed
correction stops the executor immediately, while other recovery-policy violations stop it after a configurable limit
of two by default.

Task creation similarly has a deterministic tool-loop boundary. After an initial rejected `create_tasks` call, the
creator may make `CYBER_TASK_CREATOR_MAX_CORRECTIONS` corrected calls (two by default). Exhausting that allowance stops
the role inside the current Strands loop, so payload variations cannot evade the outer workflow policy or repeat guard.
Agents submit a flat `TaskProposal`; Python supplies procedure invariants, derives source references and target scope,
and compiles the proposal into the frozen acceptance domain before validation and storage.

Successful task acceptance populates task evidence with the validated immutable ledger references. Phase
evaluators receive that canonical per-criterion ledger and may read only its resolved artifact paths, preventing stale
predicted filenames from overriding accepted evidence. Task creators use a closed flat schema with explicit limits,
criterion descriptions and evidence, snapshot references, and target IDs. Python generates readable unique criterion
IDs and owns the remaining contract and lifecycle fields.

Complete task acceptance also publishes one bounded operation observation containing the task objective, criterion
statuses, concrete summaries, evidence references, and aggregate coverage counts. Publication is replay-safe and lets
later task-prompt-builders select accepted information by memory ID. A memory-backend failure is reported in the tool
result but does not invalidate the immutable acceptance ledger or add an undeclared evaluator requirement.

There is also no prompt optimizer tool, prompt rebuild hook, or stalled-loop conversation rebuild fallback. Prompt
adaptation is workflow-native: prompt-builder agents receive current plan state, active phase/task context, compact task
history, memory summaries, and selected optional tool candidates. Python enforces proportional phase budget caps before
task work and handles separate advisory checkpoints before pending task activation.

### Security Tool Access

Security tools are accessed **via shell**, not as direct tools:

```python
# Agent uses shell tool to run security commands
shell("nmap -sV 192.168.1.1")
shell("sqlmap -u 'http://target.com?id=1' --batch")
shell("nikto -h target.com")
```

### MCP Tool Access

MCP tools can be accessed as direct tools, but they are optional tools. They are selected per worker role and task objective, not included in every worker's core tool list.

## Execution Flow

```mermaid
sequenceDiagram
    participant User
    participant Controller as Python Controller
    participant State as SQLite Plan/Task Store
    participant Builder as Prompt Builder Agent
    participant Worker as Task Worker Agent
    participant Eval as Evaluator Agent
    participant Tools
    participant Memory as Mem0 Memory
    
    User->>Controller: Start Assessment
    Controller->>State: Load or create plan
    
    loop Assessment Cycle
        Controller->>State: Select active phase/task
        alt No task candidates
            Controller->>Worker: Run task_creator
            Worker->>Tools: create_tasks when needed
            Tools-->>State: Persist tasks
        end

        Controller->>Builder: Build task-execution prompt
        Builder-->>Controller: Prompt + selected optional tools/memory
        Controller->>Worker: Execute active task
        Worker->>Tools: shell/http/MCP/module tools
        Worker->>Memory: Store observations/findings
        Controller->>Eval: Evaluate task or phase
        Eval-->>Controller: Structured status decision
        Controller->>State: Apply status and advance loop

        alt Phase soft budget reached
            Controller->>Eval: Evaluate phase before activating more pending tasks
        end
    end
    
    Controller->>User: completion termination_reason event + Final Report
```

## Role-Agent Reasoning

Focused agents still use the Cyber-AutoAgent methodology, module guidance, confidence updates, and evidence standards. The difference is that role prompts constrain the scope of each agent.

```mermaid
flowchart TD
    A[Controller: Active Phase + Task] --> B[Prompt Builder: Select Memory + Tools]
    B --> C[Task Executor: Analyze Task State]
    C --> D{Confidence Assessment}
    
    D -->|High >80%| E[Direct Specialized Tools]
    D -->|Medium 50-80%| F[Deploy Swarm or Module Tool]
    D -->|Low <50%| G[Gather More Intelligence]
    
    E --> H[Typed memory: Evidence]
    F --> H
    G --> H
    
    H --> I[Task Evaluator]
    I --> J{Task Status}
    J -->|done / partial_failure / blocked| K[Controller Applies State]
    
    style A fill:#e3f2fd,stroke:#333,stroke-width:3px
    style K fill:#e3f2fd,stroke:#333,stroke-width:3px
    style E fill:#e8f5e8
    style F fill:#f3e5f5
    style G fill:#fff3e0
```

**Key Principles:**
- **Python State Authority**: Controller owns phase/task transitions and plan completion
- **Focused Reasoning**: Agents receive narrow role prompts and task-specific context
- **Metacognitive Awareness**: Agents assess confidence within their assigned objective
- **Dynamic Capability Expansion**: Workers can use shell, selected tools, and swarm when appropriate
- **Centralized Memory**: Discoveries flow into mem0 and reports query that memory

## Tool Hierarchy

Based on confidence, task complexity, and role-specific tool selection:

1. **Specialized Security Tools** (via shell)
   - When vulnerability type is known
   - High confidence scenarios
   - Direct exploitation

2. **Swarm Deployment**  
   - Multiple approaches needed
   - Medium confidence
   - Parallel reconnaissance

3. **Meta-Tool Creation** (via editor + load_tool)
   - Novel exploits required
   - No existing tool fits
   - Custom payload generation

## Environment Discovery

```mermaid
graph LR
    A[Auto Setup] --> B[Tool Discovery]
    B --> C{Tool Available?}
    
    C -->|Yes| D[Add to Available Tools]
    C -->|No| E[Mark Unavailable]
    
    D --> F[Security Tools List]
    E --> F
    
    F --> G[nmap ✓]
    F --> H[nikto ✓]  
    F --> I[sqlmap ✓]
    F --> J[gobuster ✓]
    F --> K[metasploit ○]
    F --> L[iproute2 ○]

    M[MCP Config] --> F
```

Tools discovered via `which` command:
- Available tools accessible via `shell`
- Unavailable tools noted but not usable
- Dynamic discovery adapts to environment

MCP Tools pre-configured:
- Python reads from `CYBER_MCP_CONNECTIONS` environment
- React reads from `config.json`
- MCP Server is assigned or one or more modules

## Memory Integration

```mermaid
graph TB
    A[Agent Actions] --> B[Finding Discovered]
    B --> C[store_finding / store_observation / store_knowledge]
    C --> D[Backend Selection]

    D --> E[Mem0 Platform<br/>MEM0_API_KEY]
    D --> F[OpenSearch<br/>OPENSEARCH_HOST]
    D --> G[FAISS<br/>Default]

    E --> H[Categorized Storage]
    F --> H
    G --> H

    H --> I[category: finding]
    H --> J[category: plan]
    H --> K[category: reflection]

    L[Future Decisions] --> M[mem0_retrieve]
    M --> N[Historical Context]
    N --> A

    style C fill:#f96,stroke:#333,stroke-width:2px
    style D fill:#e3f2fd,stroke:#333,stroke-width:2px
```

**Memory Backend Selection**:
1. **Plans and Tasks**: Stored in a local SQLite database (`plan_store.db`).
2. **Semantic Memories**:
   - **Mem0 Platform** - If `MEM0_API_KEY` environment variable is set
   - **OpenSearch** - If `OPENSEARCH_HOST` environment variable is set
   - **FAISS** - Default local vector storage if neither is configured

**Evidence Storage Format**:
```
[VULNERABILITY] SQL Injection
[WHERE] /login.php?id=1
[IMPACT] Database access, credential extraction
[EVIDENCE] Request/response pairs, command outputs
[STEPS] Reproduction steps
[REMEDIATION] Use parameterized queries
[CONFIDENCE] 95% - Verified
```

## Model Providers

### Bedrock Provider (AWS)
- **Primary**: Claude Sonnet 4.5 (claude-sonnet-4-5-20250929-v1:0)
- **Embeddings**: Titan Text v2 (amazon.titan-embed-text-v2:0)
- **Region**: us-east-1 (default, configurable)
- **Benefits**: Latest models, managed infrastructure, reliable performance

### Ollama Provider (Local)
- **Primary**: qwen3-coder:30b-a3b-q4_K_M (default)
- **Embeddings**: mxbai-embed-large:latest
- **Benefits**: Privacy, offline, no API costs, local control

### LiteLLM Provider (Universal)
- **Primary**: 100+ models supported (OpenAI, Anthropic, Cohere, etc.)
- **Configuration**: Provider-specific API keys
- **Benefits**: Multi-provider flexibility, unified interface

## Event System and UI Integration

**AgentEventHandler** extends the Strands SDK's callback system to emit structured events for UI consumers:

### Event types emitted during operation

- tool_start: Tool invocation with parameters
- tool_end: Tool completion with results
- reasoning: Agent decision-making context
- metrics_update: Token usage, cost, duration, and budget progress
- progress_update: progress updates
- operation_init: Operation metadata and configuration

Events flow from the Python agent through stdout using the `__CYBER_EVENT__` protocol, enabling real-time monitoring without tight coupling between backend and frontend.

## Evaluation System

**Automated Performance Assessment** using Ragas metrics integrated with Langfuse:

| Metric                   | Range   | Purpose                                   |
|--------------------------|---------|-------------------------------------------|
| tool_selection_accuracy  | 0.0-1.0 | Strategic tool choice and sequencing      |
| evidence_quality         | 0.0-1.0 | Comprehensive vulnerability documentation |
| methodology_adherence    | 0.0-1.0 | Defensible methodology alignment          |
| penetration_test_quality | 0.0-1.0 | Holistic assessment quality               |

Evaluation triggers automatically after operation completion when `ENABLE_AUTO_EVALUATION=true`, providing continuous feedback for system improvement.

## Key Design Principles

1. **Python-Owned Orchestration**: Durable workflow state is managed by code, not by prompt instructions.
2. **Focused Role Agents**: Each agent receives a short, defined objective and restricted tools.
3. **Soft Budget Distribution**: Phase progress is evaluated against proportional budget targets.
4. **Evidence-Focused Memory**: Findings, observations, and proof artifacts are stored in mem0 for retrieval and reporting.
5. **Swarm Intelligence as a Capability**: Workers may deploy specialized sub-agents when useful, without giving up controller state authority.
6. **Tool Agnostic Execution**: Shell can access installed tools, while optional MCP/module tools are selected per task.
7. **Continuous Evaluation**: Automated performance metrics support operational improvement.

This architecture enables autonomous operation while keeping workflow control deterministic, inspectable, and resilient to context loss.
