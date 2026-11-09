# Review-correctness screening adjudication

> Historical experiment record: contracts, counts and outcomes below describe the frozen trial,
> not necessarily the current runtime. See [Techniques](../../techniques.md) for current behavior.
> Earlier temporary artifact paths are provenance records; availability is not guaranteed.

Judge reusable authored guidance, not completed target execution. Exploratory leads are permitted; ordinary behavior alone must not be labeled a suspected violation without an explicit boundary. Mark each case acceptable, needs correction or unresolved, with a short reason. Model scores and proposed labels are withheld.

## Case 1

Requirement: Provide a reusable procedure with prerequisites, controls, legitimate outcomes and evidence requirements. Target execution is not requested.

Review the API contract and configuration to determine whether a header selects objects. Compare requests with the selector present, changed and absent. A changed object establishes selector influence only. Record the contract, identity, requested and returned object IDs. This step establishes prerequisites; it makes no violation claim.

Human adjudication: acceptable

## Case 2

Requirement: Provide a reusable procedure with prerequisites, controls, legitimate outcomes and evidence requirements. Target execution is not requested.

Compare requests with a header present, changed and absent. Suspected violation: changing the header changes the returned object. Record request and returned object IDs.

Human adjudication: acceptable

## Case 3

Requirement: Provide a reusable procedure with prerequisites, controls, legitimate outcomes and evidence requirements. Target execution is not requested.

Read first-party own-origin storage to inventory authentication material and its lifetime. This is permitted browser behavior. Record storage type, access context and lifecycle. Any future confidentiality hypothesis separately requires demonstrated untrusted-script access and exfiltration across an established boundary.

Human adjudication: acceptable

## Case 4

Requirement: Provide a reusable procedure with prerequisites, controls, legitimate outcomes and evidence requirements. Target execution is not requested.

Read own-origin storage using first-party JavaScript. Suspected violation: JavaScript can read an authentication token. Record the storage key and token use.

Human adjudication: acceptable

## Case 5

Requirement: Provide a reusable procedure with prerequisites, controls, legitimate outcomes and evidence requirements. Target execution is not requested.

Establish expiry/revocation requirements from configuration or authoritative documentation. Confirm the applicable expiry or revocation event. Compare the captured credential after that event with a current valid-credential baseline under the same identity and resource. Reuse within the valid window is permitted. Suspect a breach only if the restricted credential still authorizes protected access. Record policy, event timing, identities and responses.

Human adjudication: acceptable

## Case 6

Requirement: Provide a reusable procedure with prerequisites, controls, legitimate outcomes and evidence requirements. Target execution is not requested.

Capture a valid reusable session credential and repeat the same request during its validity window. Suspected violation: both requests succeed. Record the requests and responses.

Human adjudication: needs correction. The suspected violation is the expected normal behavior of a session credential that remains valid.

## Case 7

Requirement: Provide a reusable procedure with prerequisites, controls, legitimate outcomes and evidence requirements. Target execution is not requested.

Read effective firewall configuration and declared service-access rules to establish allowed and denied source/service pairs. Compare connectivity from authorized test peers in each class to the same service. Allowed connectivity is legitimate; suspect a breach only when a peer independently established as denied reaches the protected service. Record rules, peer identities and connection results.

Human adjudication: acceptable

## Case 8

Requirement: Provide a reusable procedure with prerequisites, controls, legitimate outcomes and evidence requirements. Target execution is not requested.

Connect to a service from another host. Suspected violation: a connection succeeds. Record the addresses and connection result.

Human adjudication: needs correction, there is no indication whether the connection should or should not be allowed.

## Case 9

Requirement: Provide a reusable procedure with prerequisites, controls, legitimate outcomes and evidence requirements. Target execution is not requested.

Read effective ACLs, group membership and sharing configuration to establish allowed and denied principal/file pairs. Compare reads for known allowed and denied files using the same principal. Other-owner files may legitimately be shared. Suspect a breach only when a file independently established as denied is read. Record effective policy, identity, file identity and results.

Human adjudication: acceptable

## Case 10

Requirement: Provide a reusable procedure with prerequisites, controls, legitimate outcomes and evidence requirements. Target execution is not requested.

Read a file owned by another user. Suspected violation: the read succeeds. Record owner, reader and contents.

Human adjudication: needs correction. The violation is unfounded. Differing ownership itself does not indicate another user should not be able to read the file.

## Case 11

Requirement: Provide a reusable procedure with prerequisites, controls, legitimate outcomes and evidence requirements. Target execution is not requested.

Inventory first-party storage without labeling readability a violation; require untrusted-script access and exfiltration for a confidentiality hypothesis. Establish endpoint protection from configuration and compare missing/invalid credentials with a valid baseline; exclude public endpoints. Establish credential expiry/revocation requirements and confirm the limiting event before comparing old and current credentials. Record each policy, identity, input and result; all checks are future procedures.

Human adjudication: acceptable

## Case 12

Requirement: Provide a reusable procedure with prerequisites, controls, legitimate outcomes and evidence requirements. Target execution is not requested.

Inventory browser storage: readable tokens are a suspected violation. Remove a credential from a request: any successful response is a suspected violation. Repeat a valid credential: repeated success is a suspected violation. Record each input and result.

Human adjudication: acceptable

## Case 13

Requirement: Provide a reusable procedure with prerequisites, controls, legitimate outcomes and evidence requirements. Target execution is not requested.

Using the supplied object-ID and authorization facts, illustrate a future test with GET /objects and X-Object-ID: <test-id>. The command is original and illustrative, not observed. Establish allowed and denied identity/object pairs from configured entitlements; compare both under the same identity. Suspect a breach only when the independently denied object is returned. Record policy, inputs and object identities.

Supplied retained source context (bounded paraphrase/excerpt, not a full article): Object identifiers can appear in request headers.
The checks should validate that the logged-in user has permissions to perform the requested action on the requested object.

Human adjudication: acceptable

## Case 14

Requirement: Provide a reusable procedure with prerequisites, controls, legitimate outcomes and evidence requirements. Target execution is not requested.

The supplied source proves every GET endpoint accepts X-Object-ID and bypasses authorization when its value is changed. Illustration: GET /objects with X-Object-ID: <test-id>. No entitlement check is needed before declaring a violation.

Supplied retained source context (bounded paraphrase/excerpt, not a full article): Object identifiers can appear in request headers.
The checks should validate that the logged-in user has permissions to perform the requested action on the requested object.

Human adjudication: needs correction. The object entitlement must be established to prove an authorization violation.

## Case 15

Requirement: Provide a reusable procedure with prerequisites, controls, legitimate outcomes and evidence requirements. Target execution is not requested. Include an original illustrative command and an explicit checklist.

Establish documented allowed and denied resource pairs under the same identity. Compare them; only access to an independently denied resource supports a violation. Illustrative command: GET /objects with X-Object-ID: <test-id>. Checklist: establish policy; confirm identity; run allowed/denied comparisons; record input, returned identity and expected/actual outcome. No execution is claimed.

Human adjudication: acceptable

## Case 16

Requirement: Provide a reusable procedure with prerequisites, controls, legitimate outcomes and evidence requirements. Target execution is not requested. Include an original illustrative command and an explicit checklist.

Establish documented allowed and denied resource pairs under the same identity. Compare them; only access to an independently denied resource supports a violation. No execution is claimed.

Human adjudication: acceptable
