# Interfaces Module

The interfaces module contains the React terminal and the event-processing code that connects the Python operation
runner to the user interface.

## Architecture

```text
Python runner
  -> structured stdout events
  -> AgentEventHandler / event emitters
  -> React event stream parser and normalizer
  -> terminal state and display components
```

The React implementation is under `src/modules/interfaces/react/`. Python-side React event emission is under
`src/modules/handlers/react/`.

## Event contract

Events are structured records with a discriminator, timestamp, and operation/session metadata. Tool lifecycle events
include the tool name and normalized input/result data; workflow events describe phases, tasks, findings, reports, and
completion. The TypeScript event types are defined in
`src/modules/interfaces/react/src/types/events.ts`.

Common event categories include:

| Category | Purpose |
|---|---|
| `tool_start` / `tool_end` | Tool lifecycle and outcome |
| `tool_invocation_start` / `tool_invocation_end` | SDK-native tool lifecycle compatibility |
| `reasoning_*` and `content_block_delta` | Model and reasoning stream updates |
| `progress_update` | Operation progress and budget metrics |
| `task_*` / `phase_*` | Workflow state |
| `finding_*` / `report_*` | Finding and report progress |
| `operation_complete` / `assessment_complete` | Terminal completion |
| `user_handoff` | Request for user input |

The parser accepts compatible field spellings from backend and SDK events. Normalization is implemented in
`src/modules/interfaces/react/src/services/events/normalize.ts`; stream parsing is implemented in
`src/modules/interfaces/react/src/services/events/cyberEventStreamParser.ts`.

## Display behavior

The terminal renders event metadata rather than exposing executable tool-call examples. Common capabilities receive
specialized formatting, while unknown tools use generic metadata and error rendering.

| Display area | Content |
|---|---|
| Header | Operation ID, target, module, and deployment mode |
| Main stream | Reasoning summaries, tool status, workflow activity, findings, and report progress |
| Thinking indicator | Analyzing, executing, waiting, initializing, or preparing status |
| Footer | Progress, duration, tokens, cost, and operation health |

Long values are normalized and truncated for terminal readability. Shell command details are carried by command/output
events and are not assumed by the frontend.

## React commands

The React terminal supports `/config`, `/module`, `/setup`, `/health`, `continue`, and `report`. Assessment state and
module discovery are managed by `AssessmentFlow`; deployment selection is managed by the setup and execution services.

## Source layout

```text
src/modules/interfaces/react/src/
├── components/       # Terminal, stream, setup, and configuration views
├── contexts/         # Application and configuration state
├── hooks/             # Command, operation, and execution flows
├── services/          # Execution, deployment, and event services
└── types/             # Shared TypeScript event and application types

src/modules/handlers/react/
└── agent_event_handler.py  # Backend event emission and normalization inputs
```

## Development

From `src/modules/interfaces/react/`:

```bash
npm install
npm run build
npm test
npm run test:coverage
```

When changing event handling, update both the TypeScript types/parser and the corresponding Python emitter tests.
