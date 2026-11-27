# Advisory technique authorship

Technique generation now uses an initial author, one advisory critic, and at most one
revision by the author. The critic returns JSON containing `summary` and
`recommendations`; it has no publication decision. An empty recommendation list accepts
the initial valid document immediately. A nonempty list goes to the revision author
together with the complete draft and the same validated source context. There is no
second critic review.

Source validation remains before authorship. Python still owns provenance, document
shape, phase compatibility, privacy checks and catalog history. Each author pass may
correct invalid output format up to three times. Unavailable advisory feedback or an
unavailable/invalid revision retains the valid initial draft, with a diagnostic event.

## Assembled prompt review

Reviewed initial author, advisory critic, revision author, format correction, text
fallback and native structured-output paths. Authors receive shared improvement goals
instead of blocker instructions. The critic receives the full decoded draft, request,
phase context and retained source passages, without author examples or a veto schema.
The revision receives all recommendations together, retains the original scope and
sources, and uses technical judgement. Shared role instructions describe guidance that
the executor adapts to its case rather than a mandatory verbatim playbook.

Tests exercise representative HTTP, network and filesystem requests, both empty and
nonempty advice, malformed/unavailable feedback, invalid revisions, and text/native
structured output. Legacy strict review APIs remain available for historical offline
experiments; production authorship does not invoke them.

## Validation

- Repository coverage run: **8,138 passed, 46 skipped, 89 deselected**.
- Repository coverage floor checker passed: every measured Python file has at least
  80% statement and branch coverage. New `authorship.py`: 100% for both.
- Ruff passed for the six affected technique source files and the technique tests.
- Isolated live smoke: **8 model requests total**, including alignment and lookup;
  initial author → advisory critic with five recommendations → one revision → active
  publication. No critic re-review or source research was performed.

The live smoke used previously retained source excerpts, an in-memory vector store and
an isolated SQLite catalog under
`outputs/technique_review_trials/advisory-authorship-20261116`. It verifies transport and
workflow behavior, not new source validation, semantic quality across subjects, or
increased useful adoption. The empty-advice branch is verified by unit tests on both
output paths.

The subsequent [adoption observation](adoption-observation-20261116/README.md) exercised fresh
source assessment, authorship, real catalog recommendations and explicit reuse. It confirms
completion and selection, while retaining semantic-quality concerns and no voluntary document
retrieval in the two natural task cases.
