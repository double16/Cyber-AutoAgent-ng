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
| `allow_direct_single_step` | Whether the module may use a documented direct-task exception. |
| `direct_single_step_workstreams` | Workstreams permitted for that exception. |

The validator rejects duplicate mapping workstreams, unsupported workstreams, mapping dependencies, incorrect
synthesis dependencies, and contracts with fewer declared workstreams than the configured minimum.

Task proposals persist their planning metadata in the existing task `recovery_context` JSON. This includes
`workstream`, `task_role`, `depends_on_workstreams`, and any `inapplicability_reason`. No SQL schema migration is
required.

## Bundled module contracts

| Module | Phase 1 mode | Mapping workstreams | Synthesis |
| --- | --- | --- | --- |
| `web` | `fanout_with_synthesis` | `entrypoint_technology`, `bounded_crawl`, `client_side_api`, `auth_workflow` | `inventory_synthesis` producing `inventory_manifest` |
| `web_recon` | `fanout` | `service_entrypoints`, `technology_trust_boundary`, `access_context_session`, `workflow_high_value_areas`, `safe_read_only_verification` | None |
| `ctf` | `fanout_with_synthesis` | `challenge_hints`, `endpoint_capabilities`, `access_context`, `flag_path` | `challenge_surface_synthesis` producing an `artifact` |

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

The contract currently guarantees validated fan-out at task creation; it does not reserve execution time for every
planned task. Phase budget caps can still terminate a phase while some contracted tasks remain pending. If that
happens, later phases may create prerequisite work based on the missing snapshot, and the operation can finish with
partial failure. In particular, a synthesis task that remains pending at a phase cap is not itself proof that a
canonical inventory exists.

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
