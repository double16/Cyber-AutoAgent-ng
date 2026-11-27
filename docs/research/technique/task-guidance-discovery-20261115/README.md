# Guidance comparison with discoverable policy and identity

This follow-up uses the remaining 38 requests from the user-approved 100. The earlier study
used 62. The same frozen, uncorrected HTTP document is supplied directly in the guidance arm;
its hash is recorded in [protocol.json](protocol.json). Behavior labels and grading are hidden.

Six runs were planned: three synthetic cases with and without guidance, one run per arm/case.
Identity and policy are available only through a context tool. Every request returns status 200
and an error label, including when object data is disclosed. The cases are unauthorized disclosure,
legitimate shared access and unavailable policy. Shared access reverses arm order. This is not
the production operation executor and does not measure retrieval or voluntary adoption.

## Results at the original allowance

| Case | Guidance | No guidance | Requests, guidance / none |
| --- | --- | --- | --- |
| Unauthorized disclosure | Correct violation | Correct violation | 8 / 8 |
| Policy permits shared access | Correct no violation observed | Correct no violation observed | 5 / 5 |
| Policy unavailable | Correct inconclusive | Budget-incomplete | 5 / 7 |

[Decisions and observations](results.json) retain every planned run. Completed decisions cite valid
identity, policy and cross-account request records. Grading checks classification against fixture
truth and those cited records; it does not semantically grade every explanatory sentence.
The last run exhausted the global request allowance before structured completion. It is not an
incorrect classification and must not be counted as a guidance win. An additional allowance was
requested; no extra provider call is made without approval.

Both completed pairs tie in correctness and request count. The unavailable-policy pair is
incomplete. No accuracy advantage is established. Together with the earlier explicit-policy
study, this supports treating generic methodology as advisory, while leaving task benefit in
more complex environments unproven. Single runs, nonrandom order, no seed control and no
context-length control further limit inference.

## Separate corrections

[The corrected HTTP body proposal](corrected-http-proposal.md) fixes the invalid-selector
stopping rule, removes response-shape/status equality as a requirement and separates invalid-value
behavior from a known policy-denied control. It is not published, independently validated or used
in the frozen comparison. Historical documents and experiment stores are unchanged.

The production publication instruction now explicitly directs reviewer citations to
`issue.passage_ids`. The existing criterion about body-placement requirements remains unchanged.
The assembled-input test checks both together. This is a wording clarification at the instruction
source, not a new critic, acceptance rule or guarantee that reviewer contradictions disappear.

## Verification

Five offline tests exercise the fixtures, unknown inputs, policy-sensitive grading, complete mocked
runs, cap exhaustion and restoration of the provider transport. Combined coverage is 99%; both
Python files exceed 80% line and branch coverage. Ruff and whitespace checks pass. The exact
executed harness is retained in `executed-run.py.txt`. Private model conversations are under
`outputs/technique_review_trials/task-guidance-discovery-20261115`.

Repository validation for the publication wording clarification passed 8,112 tests, with 46 skips
and 89 deselections. Coverage was 91%, and all selected Python files passed the 80% line and
branch floors. These checks establish code/contract consistency, not semantic reviewer reliability.

## Approved continuation and final primary outcome

The user approved up to 30 additional requests. Only the interrupted no-guidance unknown-policy
case was restarted, with fresh state and unchanged inputs. It completed in five requests and
claimed a violation despite receiving an unavailable-policy record. It cited identity and object
responses but omitted that policy record from its conclusion. The error text was also present
on matched own-account responses, so it did not independently establish the entitlement policy.

[Completed primary results](completed-primary-results.json) therefore show guidance correct in
3/3 cases and no guidance correct in 2/3. All completed attempts used five or eight requests;
successful-attempt totals were 18 per arm. The original seven-request budget interruption stays
recorded separately. This is one observed benefit in handling uncertainty, not a reliable general
effect estimate from three small synthetic pairs.

## Exploratory repetition

Because the difference appeared in one case, a separately frozen [replication protocol](replication-protocol.json)
repeated only that pair with reversed arm order, within the same additional allowance. It does
not replace any primary outcome. [Replication results](replication-results.json) show:

| Arm | Outcome | Requests |
| --- | --- | --- |
| No guidance | Incorrect violation claim despite unavailable policy | 5 |
| Guidance | Structured-output failure: Markdown rather than required JSON | 8 |

The [rejected text](replication-rejected-response.txt) says inconclusive and cites the missing-policy
record. That is a qualitative observation only: the predefined automated result remains unresolved.
No post-hoc parser, schema repair or retry-until-success was applied. Both semantic judgment and
contract reliability matter to operational usefulness.

The narrow conclusion is that guidance may help preserve uncertainty when policy is missing.
It did not improve the already-solvable cases, did not remove a defective rule in its own text,
and did not guarantee structured completion. Increased production use remains unmeasured.

Across the original comparison, discovery study and this continuation/replication, 118 requests
were used. The latest 30-request extension used 18, leaving 12 unused. No model calls are running.
The five fixture/harness tests passed again; whitespace checks passed. This continuation changed
research records only, with no production code, prompt, score or catalog changes.

[A subsequent format-alignment check](../task-format-contract-20261115/README.md) found that this
experimental task harness omitted the model-facing JSON contract that the production technique
runner already supplies. A separate fresh run with that contract completed correctly in eight
requests. It does not replace the failed replication or change the primary comparison. Final
usage under the 100+30 allowance is 126 requests, leaving four unused.
