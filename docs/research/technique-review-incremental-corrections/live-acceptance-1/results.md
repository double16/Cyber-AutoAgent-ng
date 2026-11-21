# Live acceptance result: failed

The sequential muse-glimmer:30b trial used 16 of 21 provider requests across seven role
invocations. No calls remain running. Cumulative live requests are 196. Exact runtime identity
was checked before/after cases and again at export; case digests match the authorized protocol.
Provider settings and request hashes are retained in results.json; raw payloads remain private.

| Case | Human label | Result | Assessment |
| --- | --- | --- | --- |
| Network | acceptable | approved | Critic and assessor agree; no correction |
| Filesystem | needs correction | unresolved | Correct clarity finding, questionable objections, invalid correction references |
| HTTP | needs correction | rejected | Critic and assessor agree on missing entitlement boundary; no correction |

Network review conditions violation on established intended policy and identity binding. The
assessor's stale-policy counterexample remains weakly framed in terms of actual enforcement,
but it explicitly finds that counterexample does not satisfy the established prerequisites and
does not use it to invent a blocker.

HTTP review identifies the precise defect: a selector change returning another object is
legitimate when the unchanged identity is entitled to both objects. The draft explicitly rejects
the need to establish entitlement, so the assessor supplies a matching permitted counterexample
and confirms that no actual distinguishing control exists. The requested repair is actionable.

Filesystem critic initially approved. Independent assessment found the supplied file-read
clarity requirement missing, but also repeated an overly literal source objection: the excerpts
say read permission is required and access is not allowed, yet the assessor rejects linking denial
to lacking permission. This is semantic support analysis reduced to wording matching. It also
states that no permitted counterexample can satisfy the explicit policy prerequisite, then faults
the method for lacking a control excluding such a counterexample. That conclusion does not
follow from its own premise.

The correction emitted two complete issues, including file-read clarity, but its accepted
finding responses left issue_ids and interpretation_indices empty. Python rejected the response:
'Accepted findings require an issue or actual interpretation revision/withdrawal'. The single
shared correction allowance was exhausted. Thus no merged correction reached re-review.
The response also withdrew the disputed boundary interpretation rather than correcting its
description, adopting the questionable counterexample objection. See the exact captured
filesystem-correction-response.json. Matching final labels alone would not establish success.

## Implications

This trial does not establish that incremental correction contracts work reliably in live use.
It preserves unchanged rows by design, but required cross-references were still omitted. The
live filesystem failure must remain unresolved; it must not be counted as a correct rejection.

Next work should separately address reference-bookkeeping burden and semantic review errors.
A finding response can use a typed explicit relation to its generated issue; any controller
completion must use known structural IDs, not infer semantic correspondence from prose.
The assessor must distinguish a useful permitted counterexample from one contradicting the
procedure's established prerequisite, and assess support by meaning rather than lexical identity.
Do not weaken review merely to make this fixture pass. Verify changes offline, preserve this
failed trial, and request a separate bounded live allowance before another experiment.
