# Production-path isolated continuation

This follow-up tests the production path omitted by the earlier native-output benchmark. It uses
`MultiAgentWorkflowController._run_task`, default prompt building/review and semantic evaluation,
`create_agent`, the CLI maximum-token/terminal loop, and real revision and acceptance tools.
The CLI and fixture share `workflow_executor_session`; no benchmark adapter applies model decisions.

The user selected an **isolated fixture operation**. Its ID is `OP_FIXTURE_INTEGRATION_20261125`.
Three current-operation fixtures cover the observed selector-route assumption, mixed rendering
mechanisms and incomplete private-probe refutation. The prior proposals are advisory seeds copied
into fresh, explicitly defined fixture evidence and a current-operation publication.

Only retained-artifact tools and task-bound mutation/acceptance tools are available. No shell,
browser, target-request, credential-provisioning or MCP tools are configured. The fixture uses actual
SQLite/Qdrant storage with deterministic stand-in vectors, so memory persistence/scoping is real but
embedding quality is outside this test. No real assessment target is contacted.

The first frozen attempt reproduced a runtime serialization defect: the model generated the corrected
selector plan but submitted a JSON-encoded replacement string. Strands rejected it before mutation.
The attempt was stopped at ten requests and preserved. The production revision boundary now accepts
one JSON encoding of an object/list, validates through the existing strict model and atomic writer,
and still advertises the canonical object/list/null schema. Encoded null and primitives are rejected.
The pre-normalization source matches the prior frozen source and the exact inverse of this patch.

`resume.py` backs up the interrupted SQLite database, archives prior results, opens the same operation
without reseeding, and writes separate continuation audit records. The original 96-request ceiling is
shared: ten consumed before the fix, at most 86 during continuation. Completed tasks are not rerun.
Each phase has frozen source snapshots, raw requests/responses, real tool outcomes and review records.

Run offline tests with the repository environment:

```bash
KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest \
  tests/test_workflow_executor.py \
  docs/research/technique/identity-corrections-production-20261125 -q
```

The original `run.py` refuses an existing operation directory. The continuation likewise refuses an
existing continuation audit directory. Read `results.md` for actual outcomes and limitations; neither
availability acceptance nor successful mutation alone establishes semantic adequacy.

The completed run used 36/96 requests overall. Selector and rendering repairs passed inspection;
the evaluator approved the unchanged refutation case despite its missing negative private-probe
outcome. Results preserve this miss and distinguish it from the controller's three done statuses.
