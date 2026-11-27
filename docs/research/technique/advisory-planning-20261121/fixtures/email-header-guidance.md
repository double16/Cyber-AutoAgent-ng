
# Email header injection via GET query parameters hypothesis generation

## Phase alignment
**Phase:** hypothesis_generation
**Objective:** Form plausible hypotheses with known prerequisites, explicit assumptions and concrete future supporting or refuting checks. Unknown target conditions become future validation needs. No execution is performed.

## Scope definition

### Evidence present for hypothesis generation
- Observation of a GET endpoint that triggers email sending.
- Query parameters are used to supply recipient, subject, body or other mail fields.
- Server-side mail generation is implied.

### Evidence absent
- Whether query parameter values are reflected into SMTP headers.
- Whether CRLF sequences are decoded or preserved in parameter handling.
- Parameter names and validation logic are unknown.
- Mail generation library and sanitization behavior are unknown.

## Attribution and source limitations
- PortSwigger SMTP header injection describes SMTP header injection arising when user input is placed into email headers without adequate sanitization, allowing injection of additional headers via CRLF. This supports the general mechanism.
- HackTricks CRLF injection describes CRLF mechanics and HTTP header injection via CRLF sequences, including URL-encoded forms %0d %0a. This is HTTP-focused and not SMTP-specific.
- Neither source provides target-specific prerequisites, parameter handling, or validation checks for a GET email send API. Those remain future checks.

## Hypothesis formulation template

For each hypothesis document:

1. Hypothesis statement
2. Known technical prerequisites summary with status [Supported/Refuted/Unknown]
3. Prerequisite status table with Category; Status; Evidence; Future Check
4. Explicit assumptions checklist with Unverified tag and rationale
5. Unknowns requiring future observation
6. Baseline vs controlled variation and distinguishing evidence
7. Future supporting checks
8. Future refuting checks

## Plausible hypotheses

### H1: SMTP header injection via unsanitized header fields from query parameters

**Hypothesis:** User input from GET query parameters is placed into SMTP headers without adequate sanitization, allowing injection of additional headers with arbitrary values.

**Known technical prerequisites summary:**
- User input reaches SMTP header construction: Unknown
- Input not validated against whitelist of safe characters: Unknown
- Newlines/carriage returns accepted in parameter values: Unknown
- Mail library does not auto-sanitize headers: Unknown

**Prerequisite status:**
| Category | Status | Evidence | Future Check |
|---|---|---|---|
| User input reaches SMTP header construction | Unknown | GET email send API observed | Map parameter names to mail fields via source review or request mapping |
| Input is not validated against whitelist of safe characters | Unknown | No validation observed | Test parameter with newline characters and observe handling |
| Newlines/carriage returns are accepted in parameter values | Unknown | No observation | Send request with %0d%0a encoded in parameter and check server response |
| Mail library does not auto-sanitize headers | Unknown | Library unknown | Identify mail library via error messages or source and review sanitization behavior |

**Explicit assumptions:**
- Parameter names such as to, subject, body, cc, bcc are mapped directly to SMTP headers. Unverified: parameter names not confirmed.
- Server decodes URL-encoded query parameters before header construction. Unverified: decoding order not observed.
- No server-side stripping of CR/LF characters. Unverified: sanitization not observed.

**Unknowns:**
- Exact parameter names.
- Whether headers are built via string concatenation.
- Whether the API is GET only or also accepts POST.

**Baseline vs controlled variation:**
- Baseline: Legitimate send with normal parameter values produces email with expected To/Subject/Body headers only.
- Controlled variation: Same request with %0d%0a injected in a header-controlling parameter.
- Distinguishing evidence: Presence of attacker-controlled header in sent mail logs or received message vs rejection or unchanged headers.

**Future supporting checks:**
- Capture a legitimate email send request in browser devtools or proxy and record query parameter names and values used for headers.
- Send test request with parameter value containing %0d%0a and inspect sent mail logs, mailbox, or bounce messages for additional headers.
- Review server source or configuration for mail generation code path and header assembly.

**Future refuting checks:**
- Parameter values are rejected or truncated when containing CR/LF, e.g., 400 response.
- Mail library automatically neutralizes CRLF sequences before header creation.
- Input is validated against a whitelist of safe characters.

### H2: CRLF-based header injection via query parameter reflection

**Hypothesis:** CRLF sequences injected via query parameters terminate the intended header and start a new header, enabling header injection.

**Known technical prerequisites summary:**
- Query parameter value is reflected into raw email header: Unknown
- CRLF characters are preserved through URL decoding: Unknown
- Server does not normalize line endings: Unknown

**Prerequisite status:**
| Category | Status | Evidence | Future Check |
|---|---|---|---|
| Query parameter value is reflected into raw email header | Unknown | API observed | Inspect mail generation code or capture raw SMTP traffic |
| CRLF characters are preserved through URL decoding | Unknown | No observation | Send %0d%0a and %0a%0d variants and check server handling |
| Server does not normalize line endings | Unknown | No observation | Test with mixed encoding and observe normalization |

**Explicit assumptions:**
- Server uses CRLF as line delimiter in email headers per SMTP conventions. Unverified: convention assumed, not confirmed.
- URL decoding occurs before header assembly. Unverified: processing order not observed.
- No output encoding applied to header values. Unverified: encoding not observed.

**Unknowns:**
- Whether the application framework strips control characters.
- Whether the mail library enforces RFC 5322 header formatting.

**Baseline vs controlled variation:**
- Baseline: Parameter value `test` results in header `X-Custom: test`.
- Controlled variation: Parameter value `test%0d%0aBcc:%20attacker@example.com` .
- Distinguishing evidence: Received message contains Bcc header with attacker@example.com vs header rejected or value kept literal.

**Future supporting checks:**
- Send request with parameter value `test%0d%0aBcc:%20attacker@example.com` and verify if Bcc header is added in sent mail via controlled mailbox.
- Check server logs for raw header values after decoding.
- Test with double encoding %250d%250a to assess decoding depth.

**Future refuting checks:**
- Server rejects request with 400 Bad Request when CRLF detected.
- Parameter value is HTML-escaped or stripped before mail construction.
- Mail library rejects header with embedded newlines.

### H3: Header injection enabling email content manipulation

**Hypothesis:** Injection of additional headers allows alteration of email content, e.g., adding Reply-To, adding malicious attachments, or changing recipient.

**Known technical prerequisites summary:**
- Injected headers are honored by mail transfer agent: Unknown
- API allows attacker-controlled recipient fields: Unknown
- Sensitive information may be included in email: Unknown

**Prerequisite status:**
| Category | Status | Evidence | Future Check |
|---|---|---|---|
| Injected headers are honored by mail transfer agent | Unknown | No observation | Send test mail to controlled mailbox and inspect headers |
| API allows attacker-controlled recipient fields | Unknown | No observation | Identify which parameters control To, Cc, Bcc |
| Sensitive information may be included in email | Unknown | No observation | Review email template and data sources |

**Explicit assumptions:**
- Attacker can control at least one header field via query parameter. Unverified: control not confirmed.
- Injected headers are not filtered by downstream mail system. Unverified: filtering not observed.

**Unknowns:**
- Whether the API enforces recipient allowlist.
- Whether email body contains sensitive tokens.

**Baseline vs controlled variation:**
- Baseline: Email sent to intended recipient with expected headers.
- Controlled variation: Inject Reply-To header via CRLF in controllable parameter.
- Distinguishing evidence: Received message shows Reply-To set to attacker-controlled address vs header absent.

**Future supporting checks:**
- Attempt header injection to add Reply-To pointing to attacker-controlled address and verify presence in received message.
- Verify if injected headers appear in received message headers.
- Check if email contains password reset tokens or other sensitive data via template review.

**Future refuting checks:**
- Recipient fields are server-controlled and not user-supplied.
- Injected headers are stripped by mail library.
- Emails are sent to fixed internal addresses only.

## Controls and interpretation

- Distinguish legitimate behavior: normal email send with expected headers only.
- Suspected security consequence: unauthorized header addition leading to email redirection, disclosure or phishing.
- Distinguishing evidence: presence of attacker-controlled header in sent mail versus absence or rejection.

## Limitations

- Source material is general SMTP header injection and HTTP CRLF mechanics. It does not provide target-specific prerequisites, parameter handling or validation checks for a GET email send API. Those remain future checks.
- Unknown target conditions are recorded as future checks rather than established facts.
