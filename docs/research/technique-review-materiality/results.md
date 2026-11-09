# Review-materiality calibration result

All three decision labels matched human adjudication with independent agreement and no corrections,
but reasoning acceptance remains incomplete. The filesystem rejection did not identify the human's
stated loss of explicit file-access purpose. Correct labels alone do not establish correct reasons.

| Case | Human label | Model decision | Reasoning assessment |
| --- | --- | --- | --- |
| Network draft | acceptable/useful | Approve | Label matches; counterexample reasoning remains questionable |
| Last filesystem draft | needs correction | Reject | Misses human clarity concern; rejects source wording instead |
| Original HTTP selector | needs correction | Reject | Identifies missing entitlement boundary correctly |

The run used 15 of 21 requests and six role invocations, sequentially. Six requests remain unused.
Cumulative live calls are 162; none are running. No document was regenerated or published. Exact
findings, independent assessments, runtime identity, input sizes and request hashes are in
[results.json](results.json). Earlier failed generation runs remain unchanged.

## Remaining reasoning defects

Filesystem's sole blocker says the retained open(2) excerpt does not link access denial to missing
permission. It does not address why the document makes a reader search to recognize file reads.
This appears to retain literal-source over-demanding behavior, and it is not the actionable
subject/operation correction required by the prepared human-feedback test. The assessor agreed
with that objection. Do not classify the review as successful merely because its boolean label
was rejection.

Several network counterexamples describe intended legitimate observations, mark them as matching,
and then mark the same control that produces the observation as excluding it. The final approval
matches the human label, but these rows do not reliably demonstrate the intended same-observation
boundary analysis. Purpose-sensitive prompt wording helped some filesystem prerequisite/legitimate
rows use non-applicability correctly, but did not reliably prevent the network reasoning pattern.

The HTTP review correctly constructed a permitted scenario in which the unchanged identity is
entitled to both objects. The draft's selector variation does not exclude that scenario, so its
unconditional IDOR label is materially incorrect. Relaxing review did not remove this real defect.

## Next intervention

Before another generation run, make material impact concrete: a blocker should explain the false
conclusion, unusable procedure or unmet requested deliverable, not only say that wording is absent
from an excerpt. Faithful paraphrase and justified inference are distinct from an unsupported new
mechanism. Do not use deterministic keyword judgments to decide this distinction.

The filesystem brief should also explicitly incorporate the user's clarified requirement that the
read objects be identifiable as files without searching source metadata. That is additional user
intent for a repair; adding it creates a new input context and must be recorded rather than
silently retuning this frozen case. Expected labels must remain separate from reviewer inputs.

Consider the already-existing purpose-aware structured review experiment for clearer separation
of prerequisite, legitimate and boundary claims. Its schema/complexity tradeoff needs offline
validation; simply adding more forceful prose has not established reliable reasoning. No such
schema change or further live call was made in this run.
