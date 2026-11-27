# Baseline HTTP follow-up

The baseline correctly rejected the unchanged HTTP selector fixture, matching the human
`needs_correction` label. Its independent assessor agreed without a correction. The separate
follow-up used five provider requests and two role invocations, within its cap of ten.

The critic identified the material defect: the draft treats header-driven object selection as
proof of IDOR while explicitly dismissing whether the unchanged identity is entitled to both
objects. The actionable correction is to establish entitlement and require evidence that the
returned object is outside that identity's authorized access.

The assessor independently supplied a valid permitted counterexample: both objects may belong to
the same user or be readable by the same role. Changing the selector can then legitimately return
a different object. That scenario matches the authored observation, and the draft has no control
to exclude it. Agreement therefore reflects the actual interpretation defect, not rejection of
normal object selection itself.

The critic's interpretation fields nevertheless supplied entitlement checks absent from the draft.
Those fields are not a faithful description of the authored procedure. Its blocker explicitly
identified the missing checks, and the assessor evaluated the actual unconditional draft rather
than accepting that improved interpretation. A correct final rejection does not erase this
internal weakness.

Both scripted transport modes passed before live requests. Exact subject and runtime hashes,
complete findings, independent assessment and request provenance are in the
[machine-readable result](baseline-http-supplement-results.json). Raw provider payloads remain
private under `outputs/technique_review_trials/baseline-http-supplement-20261105-1`.

This is a separate follow-up; the original matched trial remains incomplete. Total live requests
through this result are 35. No corrected draft was authored or re-reviewed, no generation yield
was measured and this result does not establish production readiness. The user subsequently
requested a separately bounded [candidate HTTP follow-up](candidate-http-supplement-protocol.json).
