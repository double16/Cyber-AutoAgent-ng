# Research and experiments

Keep durable experiment records here so they can be reviewed, committed and compared over time.
An experiment directory should contain its question, frozen protocol, input fixtures, adjudications,
code provenance, results and limitations. Mark prepared and incomplete work explicitly. Never
replace a failed trial with a successful rerun; give follow-up trials separate identities.

Store portable paths relative to the repository, content hashes and exact model/settings provenance.
A model call completing does not establish correctness. Keep fixture failures, contract failures,
semantic disagreements, budget exhaustion and accepted decisions distinct. Record both role
invocations and actual provider request attempts when available.

Commit shareable fixtures and results here. Sensitive raw operation context, credentials and private
provider payloads stay in operation-local storage under outputs; use redacted records and references
in research reports. Public-source fixtures should identify the source URL and retained bounds.

## Current work

- [Technique forensics reference](technique/forensics-reference.md): current event map,
  interpretation boundaries and links to detailed technique lifecycle and calibration records.
- [Technique review calibration](technique-review-calibration/README.md): new-policy baseline,
  bounded matched-review protocol and pending fixture adjudication.

## Earlier reports

These reports retain their original locations and dispositions. Some raw evidence referenced in
older reports was in temporary storage lost on reboot; those raw trials cannot be recreated from
the reports alone.

- [Technique generation acceptance](technique/technique-generation-acceptance.md)
- [Feedback schema-order acceptance](technique/technique-feedback-order-acceptance.md)
- [Activity-specific feedback acceptance](technique/technique-activity-feedback-acceptance.md)
- [Purpose-aware correctness evaluation](technique/review-correctness-evaluation.md)
- [Critique verdict evaluation](technique/critique-verdict-evaluation.md)

- [Conditional generation-yield pilot](technique-generation-yield-pilot/README.md): three frozen,
  draft-free requests; harness and new live allowance pending.
