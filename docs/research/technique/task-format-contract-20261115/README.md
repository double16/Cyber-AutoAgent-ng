# Task final-format contract alignment

Investigation of the experimental Markdown response found that the synthetic task harness passed
`Decision` through the native provider/SDK schema but did not include the model-facing final
contract in system instructions. Production `TechniqueRoleRunner` already does both through
`structured_system_prompt`. The experiment was therefore not representative of that production
instruction path. This does not prove the missing text caused the failure.

This research-only candidate uses the production final-format contract, extracted without the
technique-specific semantic system policy. The neutral task instruction, frozen guidance,
fixture, output schema, model profile and grading remain unchanged. No new critic, semantic
acceptance rule or retry loop was added. No production code changed.

## Bounded live check

[Protocol](protocol.json) fixes one fresh unknown-policy guidance run, capped at the remaining
12 approved requests. It completed in eight requests with valid JSON, a correct inconclusive
classification and cited identity, unavailable-policy and cross-account observations.
[Results](results.json) include the decision and all task-local observations.

This is a separate format-alignment check, not a replacement for the earlier failed replication.
One success cannot establish general output reliability or a controlled causal effect. Historical
primary results remain guidance 3/3 and no guidance 2/3; the earlier exploratory guidance result
remains unresolved because it returned Markdown. Its text alone was not retroactively accepted.
The narrow uncertainty-handling benefit remains exploratory. Increased production use and
broader task benefit remain unmeasured.

Across the 100-request allowance and its 30-request extension, 126 requests were used. The
extension used 26 (five to finish the primary case, thirteen for exploratory repetition, eight
for this format check), leaving four unused. No calls are running.

## Verification

Six offline tests cover fixture behavior, unknown input rejection, deterministic evidence grading,
complete mocked execution, exhaustion, transport restoration and the absence of semantic policy
in the added final-format text. Combined line/branch coverage is 97%; both Python files exceed
80% line and branch coverage. Ruff and whitespace checks pass. Native investigation and
finalization use the shared controller bridge; no free-text parsing fallback was introduced.

The exact executed harness is preserved in `executed-run.py.txt`. Private model conversations
are under `outputs/technique_review_trials/task-format-contract-20261115`.
