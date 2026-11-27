"""Corrected fixture study, separately bounded and frozen before model requests."""

import json

import review_trials
from selection_trials import BASE, ORIGINAL, ROOT

DESTINATION = ROOT / "review-v2"


def definitions():
    rows = review_trials.cases()
    for row in rows:
        row["source"] = "Recorded source inspection and declared policy:\n" + row["publication"]["subjects"][0]["hypotheses"][0]["observed_basis"]
        if row["name"] == "http_valid_anonymous":
            row["admissible_verdicts"] = ["adequate", "not_applicable"]
    for row in rows[:8]:
        h = row["publication"]["subjects"][0]["hypotheses"][0]
        name = row["name"]
        if name == "http_valid_anonymous":
            h.update(
                observed_basis="Public source passes query name into response header X-Label. Declared policy permits one header value only; untrusted input must not create additional headers.",
                preconditions=["Use the assigned public endpoint and harmless unique header markers only."],
                safe_test_steps=[
                    "Send an anonymous name=plain-marker baseline and record raw response headers.",
                    "Change only name to an encoded CRLF plus X-Probe-Marker: harmless; compare raw headers with the baseline and an inert encoded delimiter control.",
                ],
                positive_expectations=["The controlled variation creates a separate X-Probe-Marker response header absent from the baseline and inert control, beyond ordinary value reflection."],
                negative_expectations=["The variation is rejected or encoded within X-Label without an additional header. A transport error without baseline is inconclusive."],
            )
        elif name.startswith("network_"):
            h.update(
                observed_basis="Diagnostic protocol takes a destination address. Declared policy permits operator diagnostics only to an owned allowed receiver; an owned excluded receiver must not be contacted. Observer cannot invoke diagnostics.",
                safe_test_steps=[
                    "Establish one future operator session. Send a unique inert marker to the owned allowed diagnostic receiver and verify its receipt as baseline.",
                    "In the same session vary only destination to the owned policy-excluded receiver; compare receiver receipts and an inert nonexistent-destination control. No new network scope is introduced.",
                ],
                positive_expectations=["The excluded receiver records this session's unique diagnostic marker beyond the declared allowed-destination policy; success status alone is insufficient."],
                negative_expectations=["Allowed baseline receipt succeeds, but the excluded receiver records no marker and service denies that destination. Missing baseline or attribution is inconclusive."],
            )
        elif name in {"filesystem_wrong_comparison", "filesystem_valid_role_comparison"}:
            h.update(
                observed_basis="Export function accepts a path and principal. Policy permits maintainer access to /exports/protected-marker.txt and denies reader access. Both roles may read /exports/public-marker.txt.",
                safe_test_steps=[
                    "Establish future reader and maintainer contexts; verify both can read the owned public marker and maintainer can read the protected marker.",
                    "Request that identical protected path as reader with all other inputs fixed; compare exact marker bytes and a nonexistent-path control. Do not copy maintainer context into the reader request.",
                ],
                positive_expectations=["Reader obtains the protected marker bytes also verified by maintainer, although the declared policy denies reader that same-path operation."],
                negative_expectations=["Both public baselines and maintainer protected baseline work, while reader protected access is denied. Missing identity or baseline is inconclusive."],
            )
        elif name == "filesystem_valid_single_reader":
            h.update(
                observed_basis="Export source joins an input filename to /exports. Policy permits reader only in /exports. Fixture owner provides a harmless readable marker at /owned-outside/marker.txt for this boundary test.",
                safe_test_steps=[
                    "Establish one reader context and read the owned /exports/public-marker.txt baseline.",
                    "With that same context vary only filename to the owned outside marker via a relative path; compare exact bytes with a nonexistent-path control. Do not access arbitrary files.",
                ],
                positive_expectations=["Reader obtains the known outside marker bytes beyond the permitted export root, with a successful in-root baseline and no bytes for the nonexistent control."],
                negative_expectations=["In-root baseline works but outside access is denied or normalized inside the root. Missing baseline or marker identity is inconclusive."],
            )
        row["source"] = "Recorded source inspection and declared policy:\n" + h["observed_basis"]
    fixtures = {
        "dom": {
            "source": 'Public page source: const t = new URLSearchParams(location.search).get("title"); '
                      'document.getElementById("slot").outerHTML = "<div>" + t + "</div>"; '
                      'The page uses browser location directly; server reflection is not part of this flow.',
            "basis": "Saved page source reads title from location.search and concatenates it into outerHTML on a public page.",
            "steps": [
                "Open the assigned public page with an alphanumeric title marker; record the DOM slot.",
                "Change only title to a harmless custom element marker <probe-marker>text</probe-marker> and inspect the resulting DOM without executing scripts.",
                "Compare the created element against literal escaped text; repeat with the original marker. Server reflection is not a prerequisite.",
            ],
            "positive": "The controlled title creates a probe-marker DOM element through the recorded HTML sink, demonstrating unescaped markup interpretation rather than plain text insertion.",
            "negative": "The title remains escaped literal text with no probe-marker element; an unavailable sink or missing slot is inconclusive.",
            "roles": [],
        },
        "csrf": {
            "source": 'Client source: fetch("/preferences", {method:"POST", credentials:"include", '
                      'headers:{"Content-Type":"application/x-www-form-urlencoded"}, body:"theme=dark"}); '
                      'GET /preferences reads theme. Declared policy: a user may change their own theme only by deliberate '
                      'same-site action. Cookie attributes, Origin checks and anti-CSRF token validation are unobserved.',
            "basis": "Saved client source submits a cookie-bearing form POST that changes theme and reads it back. Declared policy requires deliberate same-site user action; browser and server defenses remain unobserved.",
            "steps": [
                "Establish one authorized disposable user session and record its own theme with GET /preferences; verify a deliberate same-site POST changes it, then restore it.",
                "From an authorized separate-origin browser fixture submit the same form-encoded theme POST; record browser cookie attachment and whether the request is sent or blocked, including any preflight or token requirements.",
                "Compare theme readback with an inert form control that submits no mutation. Restore the original theme using the same user session. No second authenticated identity is compared.",
            ],
            "positive": "The browser cross-origin form changes this user's theme under ambient cookies without deliberate same-site action; inert control leaves it unchanged and the same-site baseline establishes the operation.",
            "negative": "The same-site baseline works but the cross-origin request is blocked or leaves theme unchanged; failure to establish the baseline or cookie context is inconclusive.",
            "roles": ["user"],
        },
        "property": {
            "source": 'Client source submits PATCH /profile JSON {displayName: value}; GET /profile reads state. '
                      'Declared property policy: user may change their own displayName; managedAccount is an admin-only '
                      'boolean. Whether the server permits extra properties is unobserved.',
            "basis": "Saved client source updates displayName and reads profile state. Declared policy reserves managedAccount writes to admin; extra-property enforcement is unknown.",
            "steps": [
                "Establish one authorized disposable user session and record its own displayName and managedAccount state.",
                "Change displayName through PATCH /profile as permitted baseline, then restore it.",
                "Under the same user session add only managedAccount to that PATCH with a reversible value; separately send an inert unknown property. Read authoritative profile state after each and restore authorized fixture state.",
            ],
            "positive": "The user changes the policy-reserved managedAccount boolean, beyond the permitted displayName baseline, and readback confirms that protected state change; ordinary displayName persistence is not sufficient.",
            "negative": "The permitted displayName baseline works but managedAccount is rejected or unchanged. Unknown-property acceptance without a protected state change is not positive; unavailable readback is inconclusive.",
            "roles": ["user"],
        },
        "parser": {
            "source": 'Documented PATCH /settings supports application/json and application/x-www-form-urlencoded. '
                      'GET /settings reads state. Policy: user may change theme; only admin may change lockedMode. '
                      'Both parsers must enforce that same property policy; branch enforcement is unobserved.',
            "basis": "Documented JSON and form parsers accept the same settings operation. Declared policy permits user theme changes but reserves lockedMode to admin; enforcement for each branch is unknown.",
            "steps": [
                "Establish one disposable authorized user session and record theme and lockedMode.",
                "Verify the permitted theme change with each parser as baseline and restore it; keep the same user and object.",
                "Submit the same policy-reserved lockedMode change once per parser, varying only encoding/content type. Read authoritative state after each; use an inert unknown property control and restore state through the fixture owner.",
            ],
            "positive": "One parser permits the user's prohibited lockedMode state change while the other denies the equivalent operation; permitted theme baselines and inert controls rule out parser availability alone.",
            "negative": "Both parsers enforce lockedMode policy despite accepting permitted theme changes. A parser error, status difference or encoding switch without protected state change is not positive; missing baseline is inconclusive.",
            "roles": ["user"],
        },
    }
    for row in rows[8:]:
        name = row["name"].split("-")[0]
        fixture = fixtures[name]
        row["source"] = fixture["source"]
        h = row["publication"]["subjects"][0]["hypotheses"][0]
        h.update(observed_basis=fixture["basis"],
                 authentication_requirement={"comparison": "none", "roles": fixture["roles"]},
                 preconditions=["Use only the assigned authorized disposable fixture. Establish declared user context when required; session readiness is not yet known. Preserve unknown server enforcement and browser defenses."],
                 required_evidence=["Save source and policy, exact controlled inputs, actor context, baseline and control outcomes, authoritative state or DOM readback, and restoration evidence."],
                 supporting_refs=["artifact:form.html"])
        if row["name"].endswith("valid"):
            h.update(safe_test_steps=fixture["steps"], positive_expectations=[fixture["positive"]],
                     negative_expectations=[fixture["negative"]])
    return rows


async def counted(client, *args, **kwargs):
    path = DESTINATION / "requests.json"
    counter = json.loads(path.read_text())
    if counter["requests"] >= counter["limit"]:
        raise RuntimeError("Corrected review study's 80-request cap exhausted")
    counter["requests"] += 1
    path.write_text(json.dumps(counter))
    if not isinstance(kwargs.get("format"), dict):
        raise RuntimeError("Native output schema required")
    return await ORIGINAL(client, *args, **kwargs)


def freeze():
    if (BASE / "review-inputs-v2.json").exists():
        raise RuntimeError("Corrected study is already frozen")
    review_trials.freeze("review-inputs-v2.json", definitions())


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    path = DESTINATION / "requests.json"
    if not path.exists():
        path.write_text(json.dumps({"requests": 0, "limit": 80}))
    review_trials.main("review-inputs-v2.json", DESTINATION, counted)


if __name__ == "__main__":
    main()
