import json
from unittest.mock import MagicMock, patch

import pytest

import modules.tools.idor_specialist as ids

# -------------------------
# Mocking helpers
# -------------------------

class FakeResponse:
    def __init__(self, text, status_code):
        self.text = text
        self.status_code = status_code
        self.headers = {}


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# -------------------------
# Helper function tests
# -------------------------

def test_add_or_replace_query_param():
    url = "http://example.com/api?a=1&b=2"
    new_url = ids._add_or_replace_query_param(url, "b", "3")
    assert "b=3" in new_url
    assert "a=1" in new_url

    new_url2 = ids._add_or_replace_query_param(url, "c", "4")
    assert "c=4" in new_url2


def test_extract_path_ids():
    assert ids._extract_path_ids("/api/user/123/order/456") == [(3, 123), (5, 456)]
    assert ids._extract_path_ids("/api/user/abc") == []
    assert ids._extract_path_ids("") == []


def test_replace_path_id():
    path = "/api/user/123/order/456"
    assert ids._replace_path_id(path, 3, "789") == "/api/user/789/order/456"
    assert ids._replace_path_id(path, 5, "000") == "/api/user/123/order/000"


def test_pick_candidate_params():
    qs = {"id": ["1"], "name": ["test"], "user_id": ["2"]}
    # If focus provided
    assert ids._pick_candidate_params(qs, ["id"]) == ["id"]
    # If focus NOT provided, should pick ID-ish keys
    candidates = ids._pick_candidate_params(qs, None)
    assert "id" in candidates
    assert "user_id" in candidates
    assert "name" not in candidates


def test_default_test_values_from_url():
    url = "http://example.com/api?id=100"
    vals = ids._default_test_values_from_url(url)
    assert 101 in vals
    assert 99 in vals
    assert 110 in vals

    # Test with path IDs
    url_path = "http://example.com/api/user/500"
    vals_path = ids._default_test_values_from_url(url_path)
    assert 501 in vals_path
    assert 499 in vals_path
    assert 510 in vals_path


def test_build_id_mutations():
    qs = {"id": ["100"]}
    muts = ids._build_id_mutations(qs)
    assert 101 in muts
    assert 99 in muts
    assert 0 in muts
    assert 1337 in muts

    # Test with range
    muts_range = ids._build_id_mutations(qs, num_range="1000-1010")
    assert 1000 in muts_range
    assert 1010 in muts_range


def test_compare_responses_json():
    base = '{"id": 1, "name": "alice"}'
    test = '{"id": 2, "name": "alice"}'
    res = ids._compare_responses(base, test)
    assert res["text_similarity"] < 1.0
    assert res["structure_similarity"] == 1.0
    assert res["content_similarity"] == 0.5


def test_compare_responses_covers_invalid_empty_and_list_payloads():
    assert ids._compare_responses("", "")["text_similarity"] == 0.0
    assert ids._compare_responses("not-json", "also-not-json")["structure_similarity"] == 0.0

    result = ids._compare_responses("[1, 2]", "[3]")
    assert result["structure_similarity"] == 0.5
    assert result["content_similarity"] == result["text_similarity"]


def test_send_request_selects_query_json_graphql_and_evasion_options(monkeypatch):
    calls = []

    def fake_request(**kwargs):
        calls.append(kwargs)
        return FakeResponse("ok", 200)

    monkeypatch.setattr(ids.requests, "request", fake_request)
    monkeypatch.setattr(ids.time, "sleep", lambda _duration: None)
    monkeypatch.setattr(ids.random, "choice", lambda choices: choices[0])

    query_config = ids.RequestConfig(
        target_url="http://example.com",
        headers={"X-Primary": "one"},
        alt_headers={"X-Alt": "two"},
        cookies={"primary": "one"},
        alt_cookies={"alternate": "two"},
    )
    assert ids._send_request(query_config, "http://example.com/a", "GET", {"id": "1"}, {"x": "y"}, True, True)
    assert calls[-1]["params"] == {"id": "1"}
    assert calls[-1]["data"] == {"x": "y"}
    assert calls[-1]["headers"]["X-Alt"] == "two"
    assert calls[-1]["headers"]["User-Agent"]
    assert calls[-1]["cookies"] == {"alternate": "two"}

    json_config = ids.RequestConfig(target_url="http://example.com", request_type="json")
    ids._send_request(json_config, "http://example.com/json", "POST", None, {"id": "2"}, False)
    assert calls[-1]["json"] == {"id": "2"}

    graphql_config = ids.RequestConfig(target_url="http://example.com", request_type="graphql")
    ids._send_request(graphql_config, "http://example.com/graphql", "GET", {"id": "3"}, None, False)
    assert calls[-1]["method"] == "POST"
    assert 'resource(id: "3")' in calls[-1]["json"]["query"]


def test_evaluate_mutation_covers_identical_candidate_and_vulnerability_signals():
    baseline = '{"id": 1, "name": "alice"}'
    baseline_hash = ids._hash_text(baseline)
    identical = ids._evaluate_mutation(
        "http://example.com", "GET", "query", "id", "1", "1", 200, len(baseline), baseline_hash, baseline,
        FakeResponse(baseline, 200),
    )
    assert identical is None

    bypass = ids._evaluate_mutation(
        "http://example.com", "GET", "query", "id", "1", "2", 403, 9, ids._hash_text("forbidden"), "forbidden",
        FakeResponse("allowed", 200),
    )
    assert bypass["finding_type"] == "authz_bypass_candidate"
    assert bypass["vulnerable"] is True

    likely = ids._evaluate_mutation(
        "http://example.com", "GET", "query", "id", "1", "2", 200, len(baseline), baseline_hash, baseline,
        FakeResponse('{"id": 2, "name": "bob"}', 200),
    )
    assert likely["finding_type"] == "idor_likely"

    candidate = ids._evaluate_mutation(
        "http://example.com", "GET", "query", "id", "1", "2", 200, 100, ids._hash_text("a" * 100), "a" * 100,
        FakeResponse("b" * 100, 200),
    )
    assert candidate["finding_type"] == "idor_candidate"
    assert candidate["vulnerable"] is False


def test_evaluate_authz_replay_handles_missing_errors_matches_and_role_inversion():
    assert ids._evaluate_authz_replay("url", "GET", "id", "query", None, FakeResponse("ok", 200)) is None
    assert ids._evaluate_authz_replay(
        "url", "GET", "id", "query", FakeResponse("denied", 403), FakeResponse("denied", 401)
    ) is None

    match = ids._evaluate_authz_replay(
        "url", "GET", "id", "query", FakeResponse("sensitive", 200), FakeResponse("sensitive", 200)
    )
    assert match["finding_type"] == "authz_replay_match"
    assert match["vulnerable"] is True

    inversion = ids._evaluate_authz_replay(
        "url", "GET", "id", "query", FakeResponse("denied", 403), FakeResponse("allowed", 201)
    )
    assert inversion["finding_type"] == "role_inversion_signal"


def test_signal_mutation_and_intelligence_helpers_cover_edge_cases():
    assert ids._signals_are_close({"similarity": 0.5, "mutated_len": 0}, {"similarity": 0.55, "mutated_len": 0})
    assert not ids._signals_are_close({"similarity": 0.5}, {"similarity": 0.7})
    assert not ids._signals_are_close({"mutated_len": 0}, {"mutated_len": 5})
    assert not ids._signals_are_close({"mutated_len": 10}, {"mutated_len": 20})

    assert 110 in ids._default_test_values_from_url("http://example.com/api/user/100")
    mutations = ids._build_id_mutations({"id": ["2001"]}, num_range="bad-range")
    assert 1501 in mutations
    assert all(value >= 0 for value in mutations)

    results = {
        "parameters_discovered": ["id"],
        "findings": [
            {"vulnerable": True, "finding_type": "authz_replay_match"},
            {"vulnerable": True, "finding_type": "idor_likely"},
        ],
    }
    intelligence = ids._analyze_idor_intelligence(results, has_alt=True)
    assert intelligence["attack_vectors"] == ["authz_bypass", "idor"]
    assert intelligence["exploitation_chains"] == ["authz_bypass=>data_access", "idor=>horizontal_data_access"]
    assert "confirm_with_role_matrix_requests" in ids._generate_idor_recommendations("idor", {
        "findings": results["findings"], "intelligence": intelligence,
    })
    assert ids._generate_idor_recommendations("param_discovery", {"parameters_discovered": ["id"]}) == []
    assert "provide_low_priv_context_for_replay" in ids._generate_idor_recommendations("idor", {"findings": []})


def test_perform_login_supports_oauth_failed_status_and_invalid_json(monkeypatch):
    successful = MagicMock(status_code=200)
    successful.cookies.get_dict.return_value = {"session": "oauth"}
    successful.json.return_value = {"access_token": "access"}
    failed = MagicMock(status_code=401)
    invalid_json = MagicMock(status_code=200)
    invalid_json.cookies.get_dict.return_value = {}
    invalid_json.json.side_effect = ValueError("not json")
    responses = iter([successful, failed, invalid_json])
    monkeypatch.setattr(ids.requests, "request", lambda **_kwargs: next(responses))

    cookies, headers = ids._perform_login("http://example.com/login", {"name": "user"}, auth_type="oauth")
    assert cookies == {"session": "oauth"}
    assert headers["Authorization"] == "Bearer access"
    assert ids._perform_login("http://example.com/login", {}) == (None, None)
    assert ids._perform_login("http://example.com/login", {}) == ({}, {})




# -------------------------
# Main tool tests
# -------------------------

def test_idor_specialist_param_discovery(monkeypatch):
    def fake_discovery(*args, **kwargs):
        return ["user_id", "id"]

    monkeypatch.setattr(ids, "advanced_parameter_discovery", fake_discovery)
    monkeypatch.setattr(ids.requests, "request", lambda *args, **kwargs: FakeResponse("baseline", 200))

    result_json = ids.idor_specialist(
        target_url="http://example.com/api/view?id=123",
        test_type="param_discovery"
    )
    result = json.loads(result_json)
    assert "user_id" in result["parameters_discovered"]
    assert "id" in result["parameters_discovered"]


def test_idor_specialist_python_engine_idor(monkeypatch):
    def fake_request(method, url, **kwargs):
        if "id=123" in url:
            return FakeResponse('{"user": "alice", "id": 123}', 200)
        else:
            # Different content but same structure
            return FakeResponse('{"user": "bob", "id": 456}', 200)

    monkeypatch.setattr(ids.requests, "request", fake_request)

    result_json = ids.idor_specialist(
        target_url="http://example.com/api/view?id=123",
        parameters="id",
        test_type="idor"
    )
    result = json.loads(result_json)
    assert any(f["finding_type"] == "idor_likely" for f in result["findings"])
    assert len(result["vulnerabilities"]) > 0


def test_idor_specialist_authz_replay(monkeypatch):
    def fake_request(method, url, **kwargs):
        return FakeResponse("Sensitive Data", 200)

    monkeypatch.setattr(ids.requests, "request", fake_request)

    result_json = ids.idor_specialist(
        target_url="http://example.com/api/view?id=123",
        parameters="id",
        test_type="authz_replay",
        alt_cookies={"session": "lowpriv"}
    )
    result = json.loads(result_json)
    assert any(f["finding_type"] == "authz_replay_match" for f in result["findings"])


def test_idor_specialist_path_id_mutation(monkeypatch):
    def fake_request(method, url, **kwargs):
        if "/api/user/123" in url:
            return FakeResponse('{"profile": 123}', 200)
        else:
            return FakeResponse('{"profile": 456}', 200)

    monkeypatch.setattr(ids.requests, "request", fake_request)

    result_json = ids.idor_specialist(
        target_url="http://example.com/api/user/123",
        test_type="idor"
    )
    result = json.loads(result_json)
    assert any("(path_id_at_3)" in p for p in result["parameters_discovered"])
    assert any(f["param_location"] == "path" for f in result["findings"])


def test_idor_specialist_error_handling(monkeypatch):
    # Mocking something to raise an error inside the try-except block
    monkeypatch.setattr(ids, "_idor_parameter_discovery", lambda *args, **kwargs: 1 / 0)
    monkeypatch.setattr(ids.requests, "request", lambda *args, **kwargs: FakeResponse("OK", 200))

    result_json = ids.idor_specialist(target_url="http://example.com")
    result = json.loads(result_json)
    assert len(result["errors"]) > 0
    assert "division by zero" in result["errors"][0]


def test_idor_specialist_no_target_url():
    with pytest.raises(ValueError, match="target_url is required"):
        ids.idor_specialist(target_url="")


def test_idor_specialist_custom_test_values(monkeypatch):
    captured_urls = []

    def fake_request(method, url, **kwargs):
        captured_urls.append(url)
        return FakeResponse("OK", 200)

    monkeypatch.setattr(ids.requests, "request", fake_request)

    ids.idor_specialist(
        target_url="http://example.com/api?id=1",
        parameters="id",
        test_values='[999, 888]',
        test_type="idor"
    )

    # Check if custom values were used in URLs
    assert any("id=999" in url for url in captured_urls)
    assert any("id=888" in url for url in captured_urls)


def test_idor_specialist_evasion_flag(monkeypatch):
    sleep_calls = []

    def fake_sleep(duration):
        sleep_calls.append(duration)

    monkeypatch.setattr(ids.requests, "request", lambda *args, **kwargs: FakeResponse("OK", 200))
    monkeypatch.setattr(ids, "advanced_parameter_discovery", lambda *args, **kwargs: [])
    monkeypatch.setattr(ids.time, "sleep", fake_sleep)

    result = ids.idor_specialist(
        target_url="http://example.com/api",
        evasion=True,
        test_type="idor"
    )
    assert "findings" in json.loads(result)
    assert sleep_calls


def test_idor_specialist_comprehensive_flow(monkeypatch):
    monkeypatch.setattr(ids.requests, "request", lambda *args, **kwargs: FakeResponse("OK", 200))
    monkeypatch.setattr(ids, "advanced_parameter_discovery", lambda *args, **kwargs: [])

    result_json = ids.idor_specialist(
        target_url="http://example.com/api?id=1",
        test_type="comprehensive"
    )
    result = json.loads(result_json)
    assert result["test_type"] == "comprehensive"
    assert "findings" in result


@patch("modules.tools.idor_specialist.requests.request")
def test_perform_login_basic(mock_request):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.cookies.get_dict.return_value = {"session": "123"}
    mock_request.return_value = mock_resp

    cookies, _headers = ids._perform_login(
        "http://example.com/login",
        {"user": "admin", "pass": "pass"},
        auth_type="basic"
    )

    assert cookies == {"session": "123"}
    assert "user" in mock_request.call_args.kwargs["data"]


@patch("modules.tools.idor_specialist.requests.request")
def test_perform_login_jwt(mock_request):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"token": "jwt_token"}
    mock_request.return_value = mock_resp

    _cookies, headers = ids._perform_login(
        "http://example.com/login",
        {"user": "admin"},
        auth_type="jwt"
    )

    assert headers["Authorization"] == "Bearer jwt_token"
    assert mock_request.call_args.kwargs["json"] == {"user": "admin"}


@patch("modules.tools.idor_specialist.requests.request")
def test_idor_specialist_json_mode(mock_request):
    # Mock baseline
    baseline_resp = MagicMock()
    baseline_resp.status_code = 200
    baseline_resp.text = '{"id": 1, "data": "orig"}'

    # Mock mutation
    mutated_resp = MagicMock()
    mutated_resp.status_code = 200
    mutated_resp.text = '{"id": 2, "data": "other"}'

    mock_request.side_effect = [baseline_resp, mutated_resp] + [mutated_resp] * 100

    result_json = ids.idor_specialist(
        target_url="http://example.com/api/data?id=1",
        request_type="json",
        parameters="id",
        test_type="idor"
    )

    result = json.loads(result_json)
    assert any(f["finding_type"] == "idor_likely" for f in result["findings"])

    # Verify requests were JSON
    json_calls = [c for c in mock_request.call_args_list if c.kwargs.get("json") is not None]
    assert len(json_calls) > 0
    assert json_calls[0].kwargs["json"] == {"id": "1"}


@patch("modules.tools.idor_specialist.requests.request")
def test_evaluate_authz_replay_inversion(mock_request):
    auth_resp = MagicMock()
    auth_resp.status_code = 403
    auth_resp.text = "Forbidden"

    alt_resp = MagicMock()
    alt_resp.status_code = 200
    alt_resp.text = "Success"

    finding = ids._evaluate_authz_replay(
        "http://example.com", "GET", "id", "query", auth_resp, alt_resp
    )

    assert finding["finding_type"] == "role_inversion_signal"
    assert finding["vulnerable"] is False


@patch("modules.tools.idor_specialist.advanced_parameter_discovery")
@patch("modules.tools.idor_specialist.requests.request")
def test_idor_specialist_multi_creds_full(mock_request, mock_discovery):
    mock_discovery.return_value = ["id"]

    # 2 logins
    l1 = MagicMock()
    l1.status_code = 200
    l1.cookies.get_dict.return_value = {"s": "1"}

    l2 = MagicMock()
    l2.status_code = 200
    l2.cookies.get_dict.return_value = {"s": "2"}

    # Baseline
    b = MagicMock()
    b.status_code = 200
    b.text = "OK"

    mock_request.side_effect = [l1, l2, b] + [b] * 200

    ids.idor_specialist(
        target_url="http://example.com/api?id=1",
        login_url="http://example.com/login",
        multi_credentials='[{"u": "1"}, {"u": "2"}]',
        test_type="comprehensive"
    )

    # Check if both sessions were used
    calls = mock_request.call_args_list
    sessions = [c.kwargs.get("cookies", {}).get("s") for c in calls if c.kwargs.get("cookies")]
    assert "1" in sessions
    assert "2" in sessions


@patch("modules.tools.idor_specialist.requests.request")
def test_idor_specialist_graphql_mode(mock_request):
    # Mock baseline
    baseline_resp = MagicMock()
    baseline_resp.status_code = 200
    baseline_resp.text = '{"data": {"user": {"id": 1}}}'

    # Mock mutation
    mutated_resp = MagicMock()
    mutated_resp.status_code = 200
    mutated_resp.text = '{"data": {"user": {"id": 2}}}'

    mock_request.side_effect = [baseline_resp, mutated_resp] + [mutated_resp] * 100

    result_json = ids.idor_specialist(
        target_url="http://example.com/graphql?id=1",
        request_type="graphql",
        parameters="id",
        test_type="idor"
    )

    result = json.loads(result_json)
    assert any(f["finding_type"] == "idor_likely" for f in result["findings"])


@patch("modules.tools.idor_specialist.requests.request")
def test_idor_specialist_path_id_replay(mock_request, monkeypatch):
    # Mock baseline
    b = MagicMock()
    b.status_code = 200
    b.text = '{"id": 1}'

    # Mock mutation
    m = MagicMock()
    m.status_code = 200
    m.text = '{"id": 2}'

    # Mock alt (matches mutation -> IDOR)
    alt = MagicMock()
    alt.status_code = 200
    alt.text = '{"id": 2}'

    mock_request.side_effect = [b, m, alt] + [b] * 100

    monkeypatch.setattr(ids, "advanced_parameter_discovery", lambda *args, **kwargs: [])

    result_json = ids.idor_specialist(
        target_url="http://example.com/api/user/1",
        alt_cookies={"session": "other"},
        test_type="comprehensive"
    )

    result = json.loads(result_json)
    assert any(f["finding_type"] == "authz_replay_match" for f in result["findings"])


@patch("modules.tools.idor_specialist.idor_specialist")
def test_main_cli(mock_tool, monkeypatch):
    mock_tool.return_value = "{}"
    monkeypatch.setattr("sys.argv", [
        "idor_specialist.py",
        "http://example.com",
        "--header", "X-Custom: value",
        "--cookie", "session=123",
        "--test-type", "idor",
    ])

    ret = ids.main()

    assert ret == 0
    assert mock_tool.called
    args, kwargs = mock_tool.call_args
    assert args[0] == "http://example.com"
    assert kwargs["headers"] == {"X-Custom": "value"}
    assert kwargs["cookies"] == {"session": "123"}
    assert kwargs["test_type"] == "idor"


def test_perform_login_error_handling(monkeypatch):
    def fake_request(*args, **kwargs):
        raise Exception("Connection error")

    monkeypatch.setattr(ids.requests, "request", fake_request)

    cookies, headers = ids._perform_login("http://example.com/login", {}, verbose=True)
    assert cookies is None
    assert headers is None


@patch("modules.tools.idor_specialist.requests.request")
def test_send_request_exception(mock_request):
    mock_request.side_effect = Exception("error")

    rc = ids.RequestConfig(target_url="http://example.com")
    resp = ids._send_request(rc, "http://example.com", "GET", None, None, False)
    assert resp is None


@patch("modules.tools.idor_specialist.requests.request")
def test_idor_specialist_baseline_failed(mock_request, monkeypatch):
    mock_request.return_value = None
    monkeypatch.setattr(ids, "advanced_parameter_discovery", lambda *args, **kwargs: [])

    result_json = ids.idor_specialist(target_url="http://example.com", test_type="idor")
    result = json.loads(result_json)
    assert any(f["finding_type"] == "baseline_failed" for f in result["findings"])


def test_idor_specialist_malformed_json_inputs(monkeypatch):
    monkeypatch.setattr(ids.requests, "request", lambda *args, **kwargs: FakeResponse("OK", 200))
    monkeypatch.setattr(ids, "advanced_parameter_discovery", lambda *args, **kwargs: [])

    # test_values malformed
    res = ids.idor_specialist(target_url="http://example.com", test_values="not json")
    assert "findings" in json.loads(res)

    # multi_credentials malformed
    res2 = ids.idor_specialist(target_url="http://example.com", multi_credentials="not json")
    assert "findings" in json.loads(res2)


@patch("modules.tools.idor_specialist.idor_specialist")
def test_main_cli_malformed_inputs(mock_tool, monkeypatch):
    mock_tool.return_value = "{}"
    monkeypatch.setattr("sys.argv", [
        "idor_specialist.py",
        "http://example.com",
        "--header", "malformed",
        "--cookie", "malformed"
    ])

    ids.main()
    assert mock_tool.called
    _, kwargs = mock_tool.call_args
    assert kwargs["headers"] is None
    assert kwargs["cookies"] is None


def test_idor_parameter_discovery_comprehensive():
    rc = ids.RequestConfig(target_url="http://example.com/api/data?id=123")

    # In comprehensive mode, it should run advanced discovery
    with patch("modules.tools.idor_specialist.advanced_parameter_discovery") as mock_adv:
        mock_adv.return_value = ["adv_param"]
        params = ids._idor_parameter_discovery(rc, None, test_type="comprehensive")

        assert "adv_param" in params
        assert mock_adv.called


def test_idor_parameter_discovery_idor_with_url_params():
    # In idor mode (not comprehensive/param_discovery), it should only return URL params if present
    rc = ids.RequestConfig(target_url="http://example.com/api/data?id=123&user=abc")

    with patch("modules.tools.idor_specialist.advanced_parameter_discovery") as mock_adv:
        params = ids._idor_parameter_discovery(rc, None, test_type="idor")

        assert "id" in params
        assert "user" in params
        assert not mock_adv.called


def test_idor_parameter_discovery_idor_no_url_params():
    # In idor mode, if no URL params, it should fall back to advanced discovery
    rc = ids.RequestConfig(target_url="http://example.com/api/data")

    with patch("modules.tools.idor_specialist.advanced_parameter_discovery") as mock_adv:
        mock_adv.return_value = ["adv_param"]
        params = ids._idor_parameter_discovery(rc, None, test_type="idor")

        assert "adv_param" in params
        assert mock_adv.called


def test_idor_parameter_discovery_path_id():
    # Path IDs should be considered URL params
    rc = ids.RequestConfig(target_url="http://example.com/api/user/123")

    with patch("modules.tools.idor_specialist.advanced_parameter_discovery") as mock_adv:
        params = ids._idor_parameter_discovery(rc, None, test_type="idor")

        assert "(path_id_at_3)" in params
        assert not mock_adv.called
