# External technique authoring prompt: web

Copy the prompt below into an external assistant. Review its sources and output before saving the
result under this module's `techniques/` directory. Import occurs when this module is selected.

> Context: authorized web application testing. Distinguish future hypothesis design from live verification, and require evidence-backed controls for findings.
>
> Produce one self-contained Markdown security technique for the `web` module concerning
> **[describe the method]**. Research a target of three actual source documents, prefer primary
> sources, and independently judge which apply. One authoritative source may suffice if its limits
> are explicit. Use an author/critic review and revise before returning the final document.
>
> Start with YAML frontmatter containing `schema_version: 1`, `title`, `purpose`, `applicability`
> (list), `phases` (list), `active: true`, `triggers` (list), `sources` (list), and `source_coverage`.
> Contexts include reconnaissance, hypothesis_generation, vulnerability_verification, execution,
> and analysis. Each trigger needs `phases`, semantic `when`, a generic lookup `request`, and
> optional `signals_any`. Use `http.jwt` for a compact token observation where relevant; unknown
> signals still need a clear semantic condition. Sources need `url`, `title`, `retrieved_at`, and
> `selection_reason`. Do not invent source details or lifecycle IDs and sentiment.
>
> The body must contain prerequisites, applicability, a complete original procedure, positive and
> negative controls, interpretation, failure cases, and limitations. Keep it independent of targets
> and operations. Treat existing successful guidance as advisory and supplied deactivated guidance
> as negative context, distinguishing supersession from poor feedback. Code informs reasoning but
> must never be copied verbatim. Examples must be original Python 3.13+ or browser JavaScript.
> Available tools vary: preferred command options may be included, but named tools are optional.
> Do not claim a vulnerability or ATT&CK behavior from technology/version presence alone. Return
> only the final Markdown document and include all necessary metadata and source references in it.
