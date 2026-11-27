# Clean activation and transport-failure check

This is the user-authorized final isolated integration check after the
[38-request study](../refutation-review-20261125/results.md). It uses a new operation and new current
fixture evidence; earlier completed cases, results and request counters are not overwritten.

The retained synthetic trace has a working public control and a private-probe timeout, with no policy
decision or private-canary receipt. Both conditional future plans use that same evidence. One preserves
the prior repair's attribution of error/denial/timeout to policy blocking. Its counterpart instead
requires correlated enforcement evidence and leaves transport/resource failures inconclusive.
Expected labels are harness metadata, outside model inputs; identifiers and task instructions are neutral.

The shared interpretation now explicitly separates timeout/reset/I/O failure from policy denial. No
schema or Python semantic classifier changes. The model still judges grounding and discrimination;
Python owns task activation, durable publication, review bindings and journal capture.

`run.py` freezes the driver, fixtures and affected production sources before generation. It reviews the
complete counterpart first and then runs the ambiguous case through initial review and up to two real
production repair cycles. `perform` activates the task before evaluation and execution. Initial reviews,
revision tools, acceptance and semantic reevaluation use the production code and native structured
finalization. Actual tool success and publication-bound approval are inspected separately from status.

The new recorder uses only the original remaining allowance: **96 minus the earlier 38 = 58 requests**.
Every inspection, worker, schema repair and final evaluation call counts. The parent counter stays
historically unchanged; combined usage is its consumed count plus the clean-check counter. Frozen
hashes verify parent records are unchanged. An existing check directory or exhausted allowance is rejected.

Output is under `outputs/technique_review_trials/refutation-review-20261125/clean-check/`, with a new
SQLite/Qdrant store for `OP_REFUTATION_CLEAN_20261125`. Embeddings are deterministic fixture vectors.
Only artifact/inventory readers and bound revision/acceptance tools are configured. No real targets,
browser, shell, credential tools, deployment or live assessment continuation are part of this check.

```bash
KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest \
  docs/research/technique/refutation-clean-check-20261125 -q
KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" uv run python \
  docs/research/technique/refutation-clean-check-20261125/run.py
```

Read `results.md` for outcomes, total request costs and limitations. A clean fixture result supports
considering a bounded live continuation; it does not authorize one or establish target vulnerabilities.

The finished check passed revision, active-task acceptance and semantic reevaluation with no failed
tool calls. It used 12 new requests, for 50/96 cumulatively, and retained the complete counterpart
without mutation. Earlier results and the earlier acceptance rejection remain unchanged.
