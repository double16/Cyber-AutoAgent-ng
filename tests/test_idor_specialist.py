import json
from unittest.mock import MagicMock, patch

import pytest
import modules.operation_plugins.web.tools.idor_specialist as ids


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
    monkeypatch.setattr(ids.requests, "request", lambda *args, **kwargs: FakeResponse("OK", 200))
    # We can't easily check for UA rotation without deeper mocking of requests.request, but we can check if it runs without error
    result = ids.idor_specialist(
        target_url="http://example.com/api",
        evasion=True,
        test_type="idor"
    )
    assert "findings" in json.loads(result)


def test_idor_specialist_comprehensive_flow(monkeypatch):
    monkeypatch.setattr(ids.requests, "request", lambda *args, **kwargs: FakeResponse("OK", 200))

    result_json = ids.idor_specialist(
        target_url="http://example.com/api?id=1",
        test_type="comprehensive"
    )
    result = json.loads(result_json)
    assert result["test_type"] == "comprehensive"
    assert "findings" in result


@patch("modules.operation_plugins.web.tools.idor_specialist.requests.request")
def test_perform_login_basic(mock_request):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.cookies.get_dict.return_value = {"session": "123"}
    mock_request.return_value = mock_resp

    cookies, headers = ids._perform_login(
        "http://example.com/login",
        {"user": "admin", "pass": "pass"},
        auth_type="basic"
    )

    assert cookies == {"session": "123"}
    assert "user" in mock_request.call_args.kwargs["data"]


@patch("modules.operation_plugins.web.tools.idor_specialist.requests.request")
def test_perform_login_jwt(mock_request):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"token": "jwt_token"}
    mock_request.return_value = mock_resp

    cookies, headers = ids._perform_login(
        "http://example.com/login",
        {"user": "admin"},
        auth_type="jwt"
    )

    assert headers["Authorization"] == "Bearer jwt_token"
    assert mock_request.call_args.kwargs["json"] == {"user": "admin"}


@patch("modules.operation_plugins.web.tools.idor_specialist.requests.request")
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


@patch("modules.operation_plugins.web.tools.idor_specialist.requests.request")
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


@patch("modules.operation_plugins.web.tools.idor_specialist.advanced_parameter_discovery")
@patch("modules.operation_plugins.web.tools.idor_specialist.requests.request")
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


@patch("modules.operation_plugins.web.tools.idor_specialist.requests.request")
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


@patch("modules.operation_plugins.web.tools.idor_specialist.requests.request")
def test_idor_specialist_path_id_replay(mock_request):
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

    result_json = ids.idor_specialist(
        target_url="http://example.com/api/user/1",
        alt_cookies={"session": "other"},
        test_type="comprehensive"
    )

    result = json.loads(result_json)
    assert any(f["finding_type"] == "authz_replay_match" for f in result["findings"])


@patch("modules.operation_plugins.web.tools.idor_specialist.idor_specialist")
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
    import requests

    def fake_request(*args, **kwargs):
        raise requests.exceptions.RequestException("Connection error")

    monkeypatch.setattr(requests, "request", fake_request)

    cookies, headers = ids._perform_login("http://example.com/login", {}, verbose=True)
    assert cookies is None
    assert headers is None


@patch("modules.operation_plugins.web.tools.idor_specialist.requests.request")
def test_send_request_exception(mock_request):
    mock_request.side_effect = Exception("error")

    rc = ids.RequestConfig(target_url="http://example.com")
    resp = ids._send_request(rc, "http://example.com", "GET", None, None, False)
    assert resp is None


@patch("modules.operation_plugins.web.tools.idor_specialist.requests.request")
def test_idor_specialist_baseline_failed(mock_request):
    mock_request.return_value = None

    result_json = ids.idor_specialist(target_url="http://example.com", test_type="idor")
    result = json.loads(result_json)
    assert any(f["finding_type"] == "baseline_failed" for f in result["findings"])


def test_idor_specialist_malformed_json_inputs():
    # test_values malformed
    res = ids.idor_specialist(target_url="http://example.com", test_values="not json")
    assert "findings" in json.loads(res)

    # multi_credentials malformed
    res2 = ids.idor_specialist(target_url="http://example.com", multi_credentials="not json")
    assert "findings" in json.loads(res2)


@patch("modules.operation_plugins.web.tools.idor_specialist.idor_specialist")
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
    with patch("modules.operation_plugins.web.tools.idor_specialist.advanced_parameter_discovery") as mock_adv:
        mock_adv.return_value = ["adv_param"]
        params = ids._idor_parameter_discovery(rc, None, test_type="comprehensive")

        assert "adv_param" in params
        assert mock_adv.called


def test_idor_parameter_discovery_idor_with_url_params():
    # In idor mode (not comprehensive/param_discovery), it should only return URL params if present
    rc = ids.RequestConfig(target_url="http://example.com/api/data?id=123&user=abc")

    with patch("modules.operation_plugins.web.tools.idor_specialist.advanced_parameter_discovery") as mock_adv:
        params = ids._idor_parameter_discovery(rc, None, test_type="idor")

        assert "id" in params
        assert "user" in params
        assert not mock_adv.called


def test_idor_parameter_discovery_idor_no_url_params():
    # In idor mode, if no URL params, it should fall back to advanced discovery
    rc = ids.RequestConfig(target_url="http://example.com/api/data")

    with patch("modules.operation_plugins.web.tools.idor_specialist.advanced_parameter_discovery") as mock_adv:
        mock_adv.return_value = ["adv_param"]
        params = ids._idor_parameter_discovery(rc, None, test_type="idor")

        assert "adv_param" in params
        assert mock_adv.called


def test_idor_parameter_discovery_path_id():
    # Path IDs should be considered URL params
    rc = ids.RequestConfig(target_url="http://example.com/api/user/123")

    with patch("modules.operation_plugins.web.tools.idor_specialist.advanced_parameter_discovery") as mock_adv:
        params = ids._idor_parameter_discovery(rc, None, test_type="idor")

        assert "(path_id_at_3)" in params
        assert not mock_adv.called
