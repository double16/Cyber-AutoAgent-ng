# Request-reference catalog revision

The failed candidate HTTP review confused request requirement IDs with acceptance-criterion IDs.
This revision publishes separate allowed-value catalogs in the shared frozen contract, explains
the distinction in critic and assessor output-schema descriptions, and supplies exact valid IDs
in the critic's controller-validation error. Existing-content issues may use an empty requirement
list when supported by draft references. Missing deliverables still need request references.
The prompt now says to return `requirement_ids=[]` rather than omit a schema-required field.

No unknown value is normalized, silently discarded or accepted. Arbitrary declared request IDs,
including IDs containing prefixes, remain exact values. Semantic issue validity stays with the
model; Python checks reference membership. Correction and author-repair limits are unchanged.

The [patch](reference-catalog-revision.patch) records both changed production files. Its pre-change
source identity was reconstructed and verified against the failed live candidate's frozen hash.
This preserves the failed revision as evidence rather than rewriting its result.

[Offline verification](reference-catalog-verification.json) records 2,205 passing affected tests,
18 skips, Ruff, fresh per-file line and branch coverage above 80%, and all 12 scripted transport
checks. Assembled initial and correction inputs were tested across HTTP, network and filesystem
subjects; native critic/assessor schemas expose the same field guidance. Both provider output
modes and independent investigation/finalization paths passed scripted preflight. These checks do
not establish live model compliance or semantic correctness.
