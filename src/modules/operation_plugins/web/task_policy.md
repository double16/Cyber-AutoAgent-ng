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
Set operation_provenance=observed and cite current-operation suitability evidence; omit assertions when uncertain.

The frozen task defines required identities and comparison sides. Reuse completed current-operation baseline
prerequisites; do not add anonymous requests to account, role, or tenant comparisons unless separately assigned.
Mapping and reconnaissance may preserve candidate surfaces, but must not test controls through changed HTTP methods,
injected headers, credentials, tokens, cookies, or session state unless the assigned task authorizes that testing.
