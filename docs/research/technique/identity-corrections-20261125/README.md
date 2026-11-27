# Identity corrections

This change aligns future hypothesis identities with the shared authentication contract, preserves
related proposals during one-record corrections, and grounds controls in intended operation policy.
It does not introduce a schema version, migration, configuration variable, critic or procedure identity.

`replay.py` freezes a six-case matrix and invokes the production correction dispatcher with real
publication/revision tools and isolated SQLite persistence. `replay_structured.py` supplies typed
revision and acceptance outputs, applies them deterministically through those bound tools, and retains
read-only investigation before native structured finalization. The driver freezes both repetitions and
both arms before generation. Completed individual trials are never selectively rerun.

The overall allowance is 96 generation attempts including repairs and setup failures. The initial
sandbox batch consumed 24 connection failures. A connected tool-call batch consumed four attempts
before the user's structured-output correction, and a structured setup batch consumed two attempts.
The final full matrix therefore has a 66-request allowance. All prior attempts remain preserved; they
are not silently dropped from cost or request counts.

Run the offline driver checks with the repository environment:

```bash
KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest \
  docs/research/technique/identity-corrections-20261125/test_replay.py -q
```

The final driver refuses to overwrite an already frozen output directory. Raw prompts, source hashes,
responses, failures and databases live under `outputs/technique_review_trials/identity-corrections-*/`.
Read [results.md](results.md) for measurements, semantic adjudication and limitations. Structured JSON,
mutation success and acceptance availability are not semantic approval. No live assessment, deployment
or operation restart is part of this implementation.
