# Module Task Planning Contracts

Selected operation modules use declarative phase-task contracts to guide the task creator toward multiple,
independently completable workstreams. The contracts are module-owned configuration and are loaded by the generic
workflow controller; they do not change the controller's phase or budget model.

## Why contracts exist

Attack-surface mapping is broader than a single task. A task that combines entry-point discovery, crawling,
client-side analysis, and authentication mapping tends to produce uneven coverage and makes it difficult to identify
which part of the surface is still unassessed. A planning contract gives the task creator explicit, typed workstream
boundaries while leaving task execution and evidence validation under Python control.

The planning flow is:

```mermaid
flowchart LR
    A[Module manifest] --> B[Phase task contract]
    B --> C[Task creator prompt]
    C --> D[Validated TaskProposal records]
    D --> E[Independent task execution]
    E --> F[Artifacts and acceptance ledger]
    F --> G[Optional synthesis task]
```

Contracts affect task creation only. They do not authorize a tool, prove execution, or let an agent mutate phase
state.

## Contract fields

Contracts are declared under `planning.phase_task_contracts` in a module's `module.yaml`:

| Field | Meaning |
| --- | --- |
| `phase_id` | Phase to which the contract applies. |
| `mode` | `fanout` for mapping tasks, or `fanout_with_synthesis` for mapping plus one synthesis task. |
| `min_mapping_tasks` | Minimum number of distinct mapping proposals required. |
| `mapping_workstreams` | Allowlist of workstream identifiers. Each mapping proposal uses exactly one. |
| `synthesis_workstream` | Required workstream identifier for the synthesis proposal. |
| `synthesis_output_kind` | Output type required from synthesis, such as `inventory_manifest` or `artifact`. |
| `synthesis_execution` | Required for `fanout_with_synthesis`; currently must be `controller`. It makes synthesis controller-owned rather than a runtime tool capability. |
| `allow_direct_single_step` | Whether the module may use a documented direct-task exception. |
| `direct_single_step_workstreams` | Workstreams permitted for that exception. |

The validator rejects duplicate mapping workstreams, unsupported workstreams, mapping dependencies, incorrect
synthesis dependencies, and contracts with fewer declared workstreams than the configured minimum.

## Controller-owned synthesis

Use `synthesis_execution: controller` for every `fanout_with_synthesis` contract. The task creator must emit one
`task_role: synthesis` proposal that has the declared `synthesis_workstream`, declared output kind, every mapping
workstream in `depends_on_workstreams`, and `methods: []`. Do not add a `synthesis` method or a module tool merely to
satisfy task creation: synthesis has no runtime execution-capability requirement. It still must produce the required
durable artifact and pass normal evidence acceptance.

```yaml
planning:
  phase_task_contracts:
    - phase_id: 1
      mode: fanout_with_synthesis
      min_mapping_tasks: 3
      mapping_workstreams:
        - entrypoint_technology
        - bounded_crawl
        - client_side_api
      synthesis_workstream: inventory_synthesis
      synthesis_output_kind: inventory_manifest
      synthesis_execution: controller
```

The controller validates this declaration before it invokes the task creator. For the web module's
`inventory_synthesis`, `synthesis_execution: controller` also makes execution deterministic: after every declared
mapping workstream is done, Python consolidates its retained artifact evidence into one validated inventory manifest,
records acceptance, and does not construct an executor agent. An unsupported execution mode ends task creation with a
`phase_task_contract_validation` event and `contract_unsatisfiable` code, rather than spending model retries on an
impossible proposal.

Task proposals persist their planning metadata in the existing task `recovery_context` JSON. This includes
`workstream`, `task_role`, `depends_on_workstreams`, and any `inapplicability_reason`. No SQL schema migration is
required.

## Bundled module contracts

| Module | Phase 1 mode | Mapping workstreams | Synthesis |
| --- | --- | --- | --- |
| `web` | `fanout_with_synthesis` | `entrypoint_technology`, `bounded_crawl`, `client_side_api`, `auth_workflow` | Controller-owned `inventory_synthesis` producing `inventory_manifest` |
| `web_recon` | `fanout` | `service_entrypoints`, `technology_trust_boundary`, `access_context_session`, `workflow_high_value_areas`, `safe_read_only_verification` | None |
| `ctf` | `fanout_with_synthesis` | `challenge_hints`, `endpoint_capabilities`, `access_context`, `flag_path` | Controller-owned `challenge_surface_synthesis` producing an `artifact` |

All three contracts require at least three mapping tasks. `ctf` additionally permits a direct single-step task for
the `flag_path` workstream when the challenge is demonstrably a single-step case; the proposal must include an
inapplicability or scope rationale as required by the contract.

The following modules have no phase-task contract and retain the generic task-creation behavior:

- `code_security`
- `context_navigator`
- `threat_emulation`

This is intentionally opt-in. Adding a contract to another module should follow the same declarative pattern and
include validator tests for both accepted and rejected proposal sets.

## Runtime behavior and limits

The controller prioritizes mapping tasks before synthesis tasks within a phase. Synthesis proposals declare their
mapping workstream dependencies so the intended ordering is visible in durable task metadata.

The contract guarantees validated fan-out at task creation; it does not reserve execution time for every planned
task. Phase budget caps can still defer contracted tasks. Before a later phase creates work for a missing contracted
producer, the controller resumes actionable (`active` or `pending`) mapping work before synthesis work from that
earlier contract. The resumed task keeps its owning phase context while consuming the later phase's available budget,
so the old phase cap is not applied a second time.

Terminal `partial_failure` and `blocked` contract tasks are not retried automatically. They retain the normal
replacement-task workflow. If no actionable producer task remains and the required output is still unavailable, the
later phase may create bounded prerequisite work and the operation can still finish with partial failure. A pending
or failed synthesis task is never proof that a canonical inventory exists.

When reviewing an operation, verify both layers:

1. Confirm the task-creator trace contains distinct workstream metadata and the expected synthesis proposal.
2. Confirm each task has task-local tool outcomes, durable artifacts, and an accepted evidence ledger.

The contract does not replace execution-proof checks. A successful crawl, MCP call, shell command, or custom tool
result establishes evidence availability only; task acceptance still depends on the controller's execution and
provenance checks.

## Implementation reference

- Contract model and validation: `src/modules/operation_plugins/planning_contracts.py`
- Module declarations: `src/modules/operation_plugins/{web,web_recon,ctf}/module.yaml`
- Proposal metadata and persistence: `src/modules/tools/memory.py`
- Contract loading and task selection: `src/modules/agents/multi_agent_workflow.py`
- Contract tests: `tests/test_planning_contracts.py`
