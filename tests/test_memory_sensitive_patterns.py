from modules.tools.memory import _extract_sensitive_patterns


def test_extract_sensitive_patterns_normalizes_ids():
    test_cases = [
        ("/view/?productId=1", ["/view/"]),
        ("/view/?productId=2", ["/view/"]),
        ("/view/1/details", ["/view/:id/details"]),
        ("/view/2/details", ["/view/:id/details"]),
        ("/view/1", ["/view/:id"]),
        ("/view/2", ["/view/:id"]),
        ("Check http://example.com/view/1 and http://example.com/view/2", ["http://example.com/view/:id"]),
        ("Path is /etc/config/123 vs /etc/config/456", ["/etc/config/:id"]),
        ("UUID test: /api/v1/user/550e8400-e29b-41d4-a716-446655440000/profile", ["/api/v1/user/:id/profile"]),
        ("UUID test 2: /api/v1/user/660e8400-e29b-41d4-a716-446655440001/profile", ["/api/v1/user/:id/profile"])
    ]

    for input_text, expected_output in test_cases:
        assert _extract_sensitive_patterns(input_text) == sorted(expected_output), f"Failed for input: {input_text}"


def test_extract_sensitive_patterns_mix():
    text = "Found /view/1 and /view/2, also /api/v1/resource/abc and http://example.com/page?id=99"
    expected = [
        "/api/v1/resource/abc",
        "/view/:id",
        "http://example.com/page?id=:id"
    ]
    assert _extract_sensitive_patterns(text) == sorted(expected)


def test_extract_sensitive_patterns_no_normalization_for_non_ids():
    # Ensure it doesn't normalize things that shouldn't be
    test_cases = [
        ("/view/details", ["/view/details"]),
        ("/api/v1/user/profile", ["/api/v1/user/profile"]),
        ("http://example.com/path/without/id", ["http://example.com/path/without/id"])
    ]
    for input_text, expected_output in test_cases:
        assert _extract_sensitive_patterns(input_text) == sorted(expected_output)
