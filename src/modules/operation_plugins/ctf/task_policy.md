Target interaction uses only authorized network surfaces and the assigned task scope.

During assigned endpoint or workflow mapping, attempt bounded POST discovery on relevant in-scope
endpoints when the module, objective, and assigned task permit it, even without a form or API declaration
of POST support. Use minimal benign inputs based on available endpoint evidence; avoid destructive
actions and do not execute registration or unrelated workflows. Preserve the request method, inputs,
response status, response artifact, and any coverage gap in inventory evidence. An attempted method
is not a confirmed application operation, and a response alone does not establish a vulnerability.
Use direct request tools for method discovery. Pass the selected method explicitly to
advanced_payload_coordinator for separately authorized parameter or vulnerability testing; it never
falls back to POST. Do not use method discovery to bypass restrictions on security-control testing.
