# Technique authorship formatting and truncation follow-up

## Recorded failure boundary

The final-authorship pilot produced network and filesystem documents with single-line Markdown
bodies and literal `<|message|>` tokens. The native author output recorded in the diagnostic
conversation already contained those strings. `validate_structured_json` validates provider JSON;
the native finalizer serializes the validated object, and `assemble_document` adds frontmatter
and trims the body edges. Neither layer reconstructs or collapses internal line breaks.

The available records locate the defect before document assembly. They do not retain the
successful provider's raw text before decoding, so they cannot distinguish model generation
from behavior inside the compatible server's schema decoder or chat parser.

HTTP failed during native structured author output at 12,288 output tokens and again at 24,576.
The same frozen user input was retained for recovery. Both attempts reported the output limit;
there was no complete authored document or semantic publication rejection. Failed raw provider
text was not captured in this isolated runner, so repetition, reasoning consumption and an
overlong document cannot be distinguished. Increasing the allowance alone is not an evidenced fix.

## Candidate change

The structured author schema now advertises `body` as an ordered array of Markdown lines.
Blank entries represent blank lines. Python joins the entries with newline characters before
the unchanged document assembly and publication stages. This moves line-break serialization
out of one long model-authored JSON string. Metadata and acceptance rules are unchanged.

Legacy string inputs are normalized to one item without altering their contents, including
line endings and escapes. Empty bodies and non-string items are invalid. The controller does
not reconstruct headings from prose, remove arbitrary text, modify examples or judge meaning.
The change does not guarantee that the model will stop emitting message tokens or truncating.
Array syntax also adds output overhead; its effectiveness requires measurement.

## Assembled instruction review

The shared role instructions still request JSON metadata and a Markdown body. The canonical
array representation and its line/blank-line descriptions appear in the complete final schema
in system instructions and in native/schema-tool contracts. Legacy text runners keep their
string-body schema. Author schema correction receives the rejected output and field error;
truncation recovery retains the original request and the same array schema. Prompt fitting
counts the complete schema before dispatch. No new reviewer, rejection rubric or repair loop
was added.

Scripted SDK tests cover native and schema-tool arrays and legacy strings, preserved code
fences/quotes/backslashes, invalid and empty bodies, schema correction, native truncation recovery
and prompt-fit rejection before dispatch. These verify transport and rendering, not authorship
quality. The focused suite passed 241 tests with seven skips before the repository-wide run.

Final repository validation passed 8,112 tests, with 46 skips and 89 deselections. Repository
coverage was 91%; every selected Python file met the 80% line and branch floors. Ruff and
whitespace checks passed. The initial sandboxed run could not open local test servers or write
an unrelated cache; rerunning the repository coverage entry point outside the sandbox resolved
those environment failures without changing code.

## Technical quality of completed historical documents

Both exact documents remain unchanged in the previous experiment folder. Their collapsed
formatting prevents normal Markdown rendering. The network document's illustrative Python
loads JSON and then queries a dictionary with tuple keys without defining normalization; a
normal JSON object cannot encode those tuple keys. Its classifier also omits identity and time
checks described in the surrounding procedure, although the draft explicitly calls the code
illustrative. The filesystem procedure describes policy, identity, controls and outcome evidence,
but its `noexec`/`nosuid` examples are not sufficient explanations of read-access semantics.
These are assistant observations, not human adjudication or measured task benefit.

## Focused live check

The remaining allowance from the user-approved 30-request experiment permits a bounded check
of the three original author user prompts with the new schema. At most six requests are used;
18 requests were consumed previously. No retrieval, target execution or publication occurs.
The current shared system instructions supply the array schema instead of the historical string
schema. This checks candidate output formatting and completion, not full-pipeline delivery,
independent correctness or a controlled causal effect. Results are retained privately under
`outputs/technique_review_trials/author-lines-20261115`.

All three author calls completed on their first attempt, consuming three requests. Total usage
across the pilot and this check is 21 of the approved 30; nine remain unused. Network returned
47 line items, filesystem 56, and HTTP 76. None contained the observed message token. HTTP had
75 rendered lines because its final empty item is a trailing newline. No heuristic formatting
repair was applied. [Results and exact bodies](author-lines-live-20261115/results.json) bind the
frozen user-prompt hashes, candidate code identity and body hashes.

This small sequential check supports the line-oriented representation as a practical formatting
improvement, including successful HTTP author completion under the normal allocation. It does
not establish a universal truncation fix, independent correctness or increased technique use.
The new HTTP example is an explicitly illustrative stub; successful serialization does not make
it executable test automation. The original 2/3 full-pipeline result is unchanged.
