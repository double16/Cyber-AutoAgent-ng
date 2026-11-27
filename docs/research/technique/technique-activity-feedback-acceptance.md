# Activity-specific technique feedback acceptance

> Historical experiment record: contracts, counts and outcomes below describe the frozen trial,
> not necessarily the current runtime. See [Techniques](../../techniques.md) for current behavior.
> Earlier temporary artifact paths are provenance records; availability is not guaranteed.

Feedback now combines a shared rubric with a supplement for hypothesis generation, execution,
verification, reconnaissance/analysis or unknown activity. Both evaluator and reviewer receive the
same controller-selected context, preserved through corrections and prompt fitting.

## Selection and compatibility

Frozen hypothesis_set evidence requirements identify producers, even with standard task kind.
Explicit finding_validation and objective_validation kinds identify verification. Task-level signals
precede phase metadata. Conflicting task-level signals, mixed phase activities, unsupported phase
activities and missing metadata select unknown. Titles, objectives and filenames do not classify
activity. Unknown activity does not imply neutrality.

Task closure and continuation/completion reconciliation receive the current plan and select each
task's own phase. Legacy callers without task context use an explicit unknown fallback. Activity
context is internal and optional; public tools, output shapes, SQL, historical scores and scheduling
remain unchanged. The observed_use description explicitly distinguishes producing a recorded plan
from executing tests described by that plan. Supplements impose no new task deliverables.

## Automated verification

The repository coverage entry point passed **7,614 tests**, with 46 skipped and 86 deselected.
After final selector/correction tests and diagnostic changes, the focused technique suite passed
**984 tests**, with one skipped. Ruff and whitespace checks passed.

Final coverage uses the focused report for the four edited technique modules and the full run for
other files. The original full report predates the last selector tests; its 75% selector branch
measurement is superseded by final focused coverage. Every measured source file meets the 80%
line and branch floors under this per-file verification.

| Changed source | Line | Branch |
| --- | ---: | ---: |
| feedback_activity.py | 100% | 100% |
| feedback.py | 99.3% | 96.7% |
| integration.py | 94.7% | 89.3% |
| service.py | 97.9% | 90.6% |
| multi_agent_workflow.py | 90.7% | 84.9% |

Tests cover standard-kind producers, repairs, verification, phase fallbacks, unknown and conflicting
metadata, misleading titles, identical supplements in both roles, correction preservation and
continuation using a closed task's own phase instead of the currently active phase. Existing native
and schema-tool transport, evidence mapping and provenance regressions pass. Prompt fitting retains
complete authoritative inputs or fails explicitly.

## Live acceptance

Seven frozen synthetic activity controls used muse-glimmer:30b with isolated storage and sequential
calls. The selected profile is explicit fixture context; production metadata selection is covered
by controller/integration tests. These controls are not captured production replays.

| Activity/control | Result |
| --- | --- |
| Hypothesis: HTTP plan, no target requests | Pending: reviewer omitted mappings after correction |
| Hypothesis: filesystem plan, no file access | Pending: evaluator omitted mappings/recoverable IDs |
| Execution: network reachability exclusion | Helpful (+1) |
| Verification: HTTP policy-based refutation | Pending: mappings omitted after correction |
| Analysis: misleading privilege-escalation conclusion | Pending: mappings omitted after correction |
| Unknown: citation only | Neutral (0) |
| Unknown: contribution evidence unavailable | Neutral (0) |

Both hypothesis evaluators recognized useful planning contributions without requiring target
engagement. Their positive findings do not establish successful end-to-end feedback because the
mapping contracts remained unresolved. Missing mappings are distinct from semantic neutrality or
negative sentiment. No disputed scores were persisted, and historical feedback and the active
operation were not modified. No prompts were tuned during this acceptance round.

Overall live acceptance remains **unresolved**: three controls completed and four exhausted their
existing correction allowance. This change clarifies activity expectations; it does not establish
that the existing evidence-mapping reliability problem is solved or justify a production-ready claim.

The round used 16 role invocations and 16 provider input attempts, below
the maximum of 28 role invocations including corrections. Frozen inputs, outcomes, replay harness
and complete model diagnostics: `/var/folders/vs/56b5wljn23sd5f2xt3fknkcm0000gn/T/activity-feedback-acceptance-vsxg1gy_`.
