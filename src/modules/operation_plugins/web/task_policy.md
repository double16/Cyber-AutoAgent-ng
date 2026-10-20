Target interaction uses authorized external network protocols.
For assigned mapping tasks, preserve endpoints, concrete routes, and workflows returned by inventory
tools as inventory evidence. Preserving these outputs does not authorize executing the workflows or
investigating adjacent tasks. Registration discovery belongs to the assigned provisioning worker.

Declare authentication requirements when proposing tasks: ordinary authenticated coverage uses comparison
`none`; authorization comparison tasks specify `account`, `role`, or `tenant` and the required roles.
Unauthenticated versus authenticated checks require one authenticated account per required role.

Authorization comparison criteria require evidence that the intended application operation was exercised
with its inventory-defined method and required inputs. Matching invalid-input responses or generic errors alone
are inconclusive; establish suitable parameter values within scope and use a bounded correction when
operation inputs were wrong. Legitimate denial or resource concealment can support a negative result
when the evidence establishes that behavior.

Controller captures may await semantic acceptance. Assess the retained request and response evidence before
requesting new execution. Pending acceptance alone is not an evidence gap. Refresh authentication only for a
stale context or failed session validation; redirect restrictions, transport failures, and resource denials
alone do not establish a session failure.

SPA shell delivery establishes page accessibility only. Generic Not Found/error responses require evidence
of a suitable operation or established denial/concealment policy before supporting an authorization comparison.
An inferred HTTP method is a candidate, not observed application behavior.
For observed interactions, inventory producers may preserve explicit expected response assertions under
attributes.interaction.extensions.http_comparison: version=1, concrete url, method=GET|HEAD,
comparison=none|account|role, prerequisite_evidence_refs, and assertions with identity, status,
content_type, body_contains. Identity selectors are anonymous, account:1, account:2, or role:ROLE.
For mixed provenance, set operation_provenance_by_operation for every listed operation to
observed, inferred, or unknown. The assertion's method must be observed in that map. Legacy
operation_provenance=observed applies only when the map is absent. Cite current-operation suitability
evidence; omit assertions when uncertain.

The frozen task defines required identities and comparison sides. Reuse completed current-operation baseline
prerequisites; do not add anonymous requests to account, role, or tenant comparisons unless separately assigned.
Mapping and reconnaissance may preserve candidate surfaces, but must not test controls through changed HTTP methods,
injected headers, credentials, tokens, cookies, or session state unless the assigned task authorizes that testing.

For client-side/API mapping, inspect relevant `unresolved_requests` and incomplete-analysis gaps
from bundle extraction using focused reads/searches of the full saved analysis artifact. Inspect related
call sites and their shared wrapper together. Caller candidates are unverified declarations, not resolved
operations. Preserve resolved child operations, methods, and inputs in a validated supplemental inventory
manifest before inventory synthesis. Cite source locations; client-source declarations do not prove server execution.
Analysis status `complete` means analysis finished, not that all requests resolved. Explain limits only
when structured analysis status records them. Retain justified unresolved surfaces as coverage gaps;
exhaustive bundle inspection and resolving every library call are not required. During review, use the
recorded extraction counts, status, and retained gaps to assess the assigned mapping objective. Request
focused repair when unexamined relevant calls leave that objective unsupported; zero resolved operations
alone is not failure.

During assigned endpoint or workflow mapping, attempt bounded POST discovery on relevant in-scope
endpoints when the module, objective, and assigned task permit it, even without a form or API declaration
of POST support. Use minimal benign inputs based on available endpoint evidence; avoid destructive
actions and do not execute registration or unrelated workflows. Preserve the request method, inputs,
response status, response artifact, and any coverage gap in inventory evidence. An attempted method
is not a confirmed application operation, and a response alone does not establish a vulnerability.
Use direct request tools for method discovery. Pass the selected method explicitly to
advanced_payload_coordinator for separately authorized parameter or vulnerability testing; it never
falls back to POST. Do not use method discovery to bypass restrictions on security-control testing.
