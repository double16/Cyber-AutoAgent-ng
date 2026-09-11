from modules.utils.redaction import REDACTED, bounded_redacted_text, redact, redact_text


def test_redact_text_removes_credential_forms_without_losing_context():
    value = (
        "Authorization: Bearer token-value, api_key=secret-value "
        "https://alice:password@example.test/path AKIA1234567890ABCDEF"
    )

    result = redact_text(value)

    assert "token-value" not in result
    assert "secret-value" not in result
    assert "alice:password" not in result
    assert "AKIA1234567890ABCDEF" not in result
    assert result.count(REDACTED) == 4


def test_redact_recursively_handles_mappings_lists_tuples_and_scalar_values():
    value = {
        "api-key": "preserved only as redacted",
        "nested": ["Bearer exposed-token", ("safe", {"cookie": "session"})],
        "count": 3,
    }

    assert redact(value) == {
        "api-key": REDACTED,
        "nested": [f"Bearer {REDACTED}", ["safe", {"cookie": REDACTED}]],
        "count": 3,
    }


def test_bounded_redacted_text_returns_full_or_truncated_safe_text():
    assert bounded_redacted_text("token=secret", limit=30) == f"token={REDACTED}"

    result = bounded_redacted_text("x" * 10, limit=5)

    assert result == "xxxxx…[truncated]"
