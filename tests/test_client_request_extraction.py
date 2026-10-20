"""Source request extraction and inventory integration tests."""

import pytest

from modules.tools import client_request_extraction as extraction
from modules.tools.client_bundle_inventory import _bundle_inventory
from modules.tools.recon_inventory_manifest import attach_client_bundle_evidence, records_to_inventory_manifest

ENUM = """
var api = (e => {
 e.Email = "/api/email";
 e.File = "/api/file";
 return e;
})(api || {});
function send(config) { return client.request(config); }
"""


def test_composed_operations_keep_method_specific_inputs_and_source_locations():
    text = (
        ENUM
        + """
function email(name, to, subject, content) {
 return send({url: `${api.Email}/sendSupportEmail?name=${name}&to=${to}&subject=${subject}&content=${content}`,
              method: "get"});
}
function read(path) { return send({url: `${api.File}/raw?path=${path}`, method: "get"}); }
function write(path, data) { return send({url: `${api.File}/raw?path=${path}`, method: "put", data}); }
"""
    )
    # Explicit properties are recognized even when data is opaque.
    text = text.replace('method: "put", data}', 'method: "put", data: data}')
    result = extraction.extract_client_requests(text)
    assert result["request_analysis_status"] == "complete"
    assert [(o["url"], o["method"]) for o in result["operations"]] == [
        ("/api/email/sendSupportEmail", "GET"),
        ("/api/file/raw", "GET"),
        ("/api/file/raw", "PUT"),
    ]
    assert result["operations"][0]["inputs"] == [
        {"name": name, "location": "query"} for name in ["name", "to", "subject", "content"]
    ]
    assert result["operations"][1]["inputs"] == [{"name": "path", "location": "query"}]
    assert result["operations"][2]["inputs"][-1] == {"name": "body", "location": "body", "type": "opaque"}
    for operation in result["operations"]:
        location = operation["source_location"]
        snippet = text.encode()[location["start_byte"] : location["end_byte"]].decode()
        assert snippet.startswith("send(")
        assert location["start_line"] == text[: location["start_byte"]].count("\n") + 1


@pytest.mark.parametrize(
    "text,path",
    [
        ('const base="/api"; const alias=base; fetch(alias + "/x?q=1");', "/api/x"),
        ('const api={file:"/api/file"}; fetch(`${api.file}/raw?path=${x}`);', "/api/file/raw"),
        ('fetch("/api/x")', "/api/x"),
        ("window.fetch(`/api/x?unknown=${x}`)", "/api/x"),
        ('globalThis.fetch("/api/x", {method:"POST", body:{name:value}, headers:{"X-Session":token}})', "/api/x"),
        ('request({url:"/api/x", headers:{Accept:"application/json"}})', "/api/x"),
    ],
)
def test_supported_request_forms(text, path):
    result = extraction.extract_client_requests(text)
    assert result["operations"][0]["url"] == path


@pytest.mark.parametrize(
    "text",
    [
        'let path="/api/a"; path="/api/b"; fetch(path)',
        'let path="/api/a"; path+="/b"; fetch(path)',
        'let path="/api/a"; path++; fetch(path)',
        'const path="/api/a"; function f(path) { fetch(path); }',
        'const path="/api/a"; function f() { const path=unknown; fetch(path); }',
        "fetch(`/api/${runtime}/raw`)",
        'fetch("http://[bad")',
        'fetch("/api/x",{method:"BAD METHOD"})',
        "const a=b; const b=a; fetch(a)",
        'fetch("/api" + runtime)',
        "fetch(`\\n/api/x`)",
        "fetch('/api/\\'x' + unknown)",
    ],
)
def test_ambiguous_or_unsupported_requests_remain_unresolved(text):
    result = extraction.extract_client_requests(text)
    assert not result["operations"]
    assert result["unresolved_requests"]


@pytest.mark.parametrize(
    "text",
    [
        'configure({url:"/not-a-request",method:"GET"});',
        'const config={url:"/not-called",method:"GET"};',
        "fetch(); noArguments();",
        "const x=(()=>{return 1;})();",
        "const x=((a,b)=>a)(one,two);",
        "const x=(a=>{return other;})(one);",
    ],
)
def test_non_requests_do_not_become_operations(text):
    assert not extraction.extract_client_requests(text)["operations"]


@pytest.mark.parametrize(
    "limit,value,status",
    [
        ("_MAX_BYTES", 1, "size_limit"),
        ("_MAX_NODES", 1, "node_limit"),
        ("_MAX_REQUESTS", 1, "request_limit"),
    ],
)
def test_analysis_limits_are_explicit(monkeypatch, limit, value, status):
    monkeypatch.setattr(extraction, limit, value)
    assert extraction.extract_client_requests('fetch("/x"); fetch("/y")')["request_analysis_status"] == status


def test_parser_errors_preserve_literal_route_extraction():
    result = _bundle_inventory('"/api/known"; const = ; fetch("/api/x")', "https://target.test")
    assert "/api/known" in result["api_paths"]
    assert result["request_analysis_status"] == "parse_errors"


def test_inventory_merges_operations_without_claiming_execution():
    result = extraction.extract_client_requests("""
fetch("/api/file/raw?path=x");
fetch("/api/file/raw?path=x", {method:"PUT", body:content});
fetch("https://outside.test/api/x");
fetch(`/api/${dynamic}`);
""")
    payload = {
        **result,
        "target": "https://target.test",
        "target_id": "target-1",
        "analysis_artifact": "artifact:artifacts/source.js",
    }
    manifest = records_to_inventory_manifest([], target="https://target.test", target_id="target-1")
    attach_client_bundle_evidence(manifest, payload)
    endpoint = manifest["items"][0]
    interaction = endpoint["attributes"]["interaction"]
    assert endpoint["value"] == "https://target.test/api/file/raw"
    assert interaction["operations"] == ["GET", "PUT"]
    assert interaction["operation_provenance"] == "inferred"
    details = interaction["extensions"]["client_source_operations"]
    assert len(details) == 2
    assert {"name": "body", "location": "body", "type": "opaque"} not in details[0]["inputs"]
    assert {"name": "body", "location": "body", "type": "opaque"} in details[1]["inputs"]
    assert endpoint["attributes"]["implementation_refs"] == ["artifact:artifacts/source.js"]
    assert manifest["unassessed_gaps"][0]["reason"] == "unresolved_client_request"
    assert len([item for item in manifest["items"] if item["kind"] == "endpoint"]) == 1
    assert [item["value"] for item in manifest["items"] if item["kind"] == "parameter"] == ["path"]
    attach_client_bundle_evidence(manifest, payload)
    assert len(details) == 2


@pytest.mark.parametrize(
    "text",
    [
        ENUM + "function f(api) { fetch(`${api.Email}/x`); }",
        ENUM + "api = other; fetch(`${api.Email}/x`);",
        'let path="/api/a"; function change() { path="/api/b"; } fetch(path);',
    ],
)
def test_shadowed_members_and_outer_writes_are_not_resolved(text):
    assert not extraction.extract_client_requests(text)["operations"]


def test_unknown_method_and_dynamic_query_keep_known_path_without_inventing_method():
    result = extraction.extract_client_requests('request({url:"/api/x?q="+value,method:runtime})')
    assert result["operations"][0]["method"] is None
    assert result["operations"][0]["url"] == "/api/x"
    assert result["operations"][0]["inputs"] == [{"name": "q", "location": "query"}]
    assert result["unresolved_requests"][0]["reason"] == "unresolved_method"


def test_custom_http_method_and_shorthand_body_fields():
    result = extraction.extract_client_requests(
        'fetch("/api/custom",{method:"PROPFIND",body:{name,[dynamic]:value},headers:{"X-Session":token}})'
    )
    assert result["operations"][0]["method"] == "PROPFIND"
    assert {"name": "name", "location": "body"} in result["operations"][0]["inputs"]
    assert {"name": "X-Session", "location": "header"} in result["operations"][0]["inputs"]
    assert not any(i["name"] == "dynamic" for i in result["operations"][0]["inputs"])


def test_large_multiline_source_locations_use_utf8_byte_offsets():
    text = "// emoji 🐍\n" * 2000 + 'fetch("/api/example");'
    operation = extraction.extract_client_requests(text)["operations"][0]
    assert operation["source_location"]["start_line"] == 2001
    assert text.encode()[operation["source_location"]["start_byte"] :].decode().startswith("fetch(")


def test_inventory_preserves_observed_methods_and_deduplicates_analysis_gaps():
    manifest = records_to_inventory_manifest(
        [{"url": "https://target.test/api/x?p=x", "method": "HEAD", "operation_provenance": "observed"}],
        target="https://target.test",
        target_id="target-1",
    )
    payload = {
        **extraction.extract_client_requests('fetch("/api/x?p=x");'),
        "target": "https://target.test",
        "target_id": "target-1",
        "request_analysis_status": "parse_errors",
    }
    extraction.attach_request_operations(manifest, payload)
    extraction.attach_request_operations(manifest, payload)
    endpoint = next(i for i in manifest["items"] if i["kind"] == "endpoint")
    assert endpoint["attributes"]["interaction"]["operations"] == ["HEAD", "GET"]
    assert endpoint["attributes"]["interaction"]["operation_provenance_by_operation"] == {
        "HEAD": "observed", "GET": "inferred",
    }
    parameter = next(i for i in manifest["items"] if i["kind"] == "parameter")
    assert parameter["attributes"]["interaction"]["operations"] == ["HEAD", "GET"]
    assert len(manifest["unassessed_gaps"]) == 1


def test_converted_source_operations_cannot_rebind_target_or_target_id():
    payload = {
        **extraction.extract_client_requests('fetch("/api/x")'),
        "target": "https://outside.test",
        "target_id": "untrusted",
    }
    manifest = {"items": []}
    extraction.attach_request_operations(manifest, payload, target="https://target.test", target_id="target-2")
    assert manifest["items"] == []
    payload["target"] = "https://target.test"
    extraction.attach_request_operations(manifest, payload, target="https://target.test", target_id="target-2")
    assert manifest["items"][0]["target_id"] == "target-2"


def test_syntax_resolution_is_bounded_for_long_alias_chains():
    code = 'const a0="/api/x";' + "".join(f"const a{i}=a{i - 1};" for i in range(1, 25)) + "fetch(a24)"
    result = extraction.extract_client_requests(code)
    assert not result["operations"]
    assert result["unresolved_requests"]


@pytest.mark.parametrize(
    "source",
    [
        r"fetch('/api/\x78')",
        r'fetch("/api/\x78")',
    ],
)
def test_unsupported_string_escapes_do_not_create_wrong_paths(source):
    result = extraction.extract_client_requests(source)
    assert not result["operations"]
    assert result["unresolved_requests"]


def test_single_quoted_literal_paths_are_supported():
    assert extraction.extract_client_requests("fetch('/api/x')")["operations"][0]["url"] == "/api/x"


@pytest.mark.parametrize("call", ["request(alias)", "axios(alias)", "client.request(alias)",
                                 'fetch("/api/x", alias)'])
def test_constant_config_aliases_preserve_method_and_inputs(call):
    source = (
        'const headers={"X-Session":token}; const body={name:value};'
        'const config={url:"/api/x",method:"POST",headers,body}; const alias=config;'
        + call
    )
    result = extraction.extract_client_requests(source)
    assert len(result["operations"]) == 1
    operation = result["operations"][0]
    assert (operation["url"], operation["method"]) == ("/api/x", "POST")
    assert operation["inputs"] == [{"name": "X-Session", "location": "header"}, {"name": "name", "location": "body"}]
    assert not result["unresolved_requests"]


@pytest.mark.parametrize("source", [
    "request(runtime)", "request()", 'axios.post("/api/x", {name:value})',
    'const config={url:"/api/x",method:"POST"}; config.url="/api/y"; request(config)',
    'const config={url:"/api/x",method:"POST"}; const alias=config; alias.method="DELETE"; request(config)',
    'const config={url:"/api/x",method:"POST"}; Object.assign(config,{method:"DELETE"}); request(config)',
    'const config={url:"/api/x",method:"POST"}; delete config.url; request(config)',
    'const config={url:"/api/x",method:"POST"}; delete\nconfig.url; request(config)',
    'const config={url:"/api/x",method:"POST"}; config[dynamic]="DELETE"; request(config)',
    'const config={url:"/api/x",method:"POST"}; change(config); request(config)',
    'const config={url:"/api/x",method:"POST"}; config.change(); request(config)',
    'const config={url:"/api/x",method:"POST"}; let alias=config; alias=other; alias.url="/api/y"; request(config)',
    'const config={url:"/api/x",method:"POST"}; config.method+="x"; request(config)',
    'let config={url:"/api/x",method:"POST"}; config=other; request(config)',
    'const config={url:"/api/x",...runtime}; request(config)',
    'const config={url:"/api/x",method:"POST"}; function f(config){request(config)}',
    "const a=b; const b=a; request(a)",
    'const config={url:"/api/x"}; function f(){const config=unknown; request(config)}',
])
def test_unresolvable_recognized_configurations_are_not_silently_discarded(source):
    result = extraction.extract_client_requests(source)
    assert not result["operations"]
    assert result["unresolved_requests"]
    for request in result["unresolved_requests"]:
        location = request["source_location"]
        assert location["end_byte"] > location["start_byte"]


@pytest.mark.parametrize("declaration", [
    "function send(config){return client.request(config)}",
    "const send=(config)=>client.request(config);",
    "const send=function(config){return axios(config)};",
])
def test_only_unchanged_configuration_forwarders_are_extracted(declaration):
    source = declaration + 'send({url:"/api/real",method:"PUT",data:{name:value}})'
    result = extraction.extract_client_requests(source)
    assert [(operation["url"], operation["method"]) for operation in result["operations"]] == [("/api/real", "PUT")]


@pytest.mark.parametrize("declaration", [
    "function send(config){const alias=config; return client.request(alias)}",
    "function send(config){const first=config; const second=first; return client.request(second)}",
    "function send(config){const first=config, second=first; return client.request(second)}",
    'function send(config){const alias=typeof config==="string"?{url:config}:config;return client.request(alias)}',
    "function send(config){return client.request(config).then(result=>result.data)}",
    "function send(config){return client.request(config).catch(error=>({error:error.message}))}",
    "function send(config){return client.request(config).finally(()=>cleanup())}",
    ('function send(config){const alias=typeof config == "string"?{url:config}:config;'
     'return client.request(alias).then(result=>result.data).catch(error=>error).finally(()=>cleanup())}'),
    "const send=config=>client.request(config).then(result=>result.data);",
])
def test_aliases_normalization_and_response_chains_preserve_configuration(declaration):
    result = extraction.extract_client_requests(declaration + 'send({url:"/api/real?path=x",method:"PUT"})')
    actual = [operation for operation in result["operations"] if operation["url"] == "/api/real"]
    assert len(actual) == 1
    assert actual[0]["method"] == "PUT"
    assert actual[0]["inputs"] == [{"name": "path", "location": "query"}]


@pytest.mark.parametrize("declaration", [
    "function send(config){let alias=config; return client.request(alias)}",
    "function send(config){const alias=other; return client.request(alias)}",
    "function send(config){const alias={...config}; return client.request(alias)}",
    "function send(config){const alias=config; alias.url='/other';return client.request(alias)}",
    "function send(config){const alias=config; change(alias);return client.request(alias)}",
    "function send(config){const alias=change(config);return client.request(alias)}",
    "function send(config){const {url}=config;return client.request(config)}",
    'function send(config){const alias=enabled?{url:config}:config;return client.request(alias)}',
    'function send(config){const alias=typeof config==="string"?{url:"/other"}:config;return client.request(alias)}',
    "function send(config){return client.request(config).then(transform)}",
    "function send(config){return client.request(config).then(()=>config)}",
    "function send(config){return client.request(config).then(()=>({config}))}",
    "function send(config){return client.request(config).then(()=>config.url='/other')}",
    "function send(config){return client.request(config).custom(result=>result)}",
    "function send(config){}",
])
def test_uncertain_forwarding_keeps_caller_unverified(declaration):
    result = extraction.extract_client_requests(declaration + 'send({url:"/api/candidate?name=x",method:"DELETE"})')
    assert not any(operation["url"] == "/api/candidate" for operation in result["operations"])
    entry = next(request for request in result["unresolved_requests"] if request.get("callee") == "send")
    assert entry["reason"] == "unrecognized_request_wrapper"
    assert entry["candidate"]["url"] == "/api/candidate"
    assert entry["candidate"]["method"] == "DELETE"
    assert entry["candidate"]["inputs"] == [{"name": "name", "location": "query"}]
    assert entry["candidate"]["evidence_kind"] == "unverified_caller_declaration"
    location = entry["wrapper_location"]
    assert declaration.encode()[location["start_byte"]:location["end_byte"]].startswith(b"function send")


def test_normalization_string_call_remains_unresolved():
    result = extraction.extract_client_requests(
        'function send(config){const alias=typeof config==="string"?{url:config}:config;'
        'return client.request(alias)} send("/api/unknown")'
    )
    assert not result["operations"]
    assert result["unresolved_requests"]


def test_unverified_candidate_metadata_survives_inventory_without_becoming_endpoint():
    payload = {**extraction.extract_client_requests(
        'function send(config){if(enabled)return request(config)} send({url:"/api/candidate",method:"POST"})'
    ), "target": "https://target.test", "analysis_artifact": "artifact:artifacts/source.js"}
    manifest = {"items": []}
    attach_client_bundle_evidence(manifest, payload)
    assert not manifest["items"]
    entry = next(gap for gap in manifest["unassessed_gaps"] if gap.get("callee") == "send")
    assert entry["candidate"]["url"] == "/api/candidate"
    assert entry["wrapper_location"]
    assert entry["source_artifact"] == "artifact:artifacts/source.js"


@pytest.mark.parametrize("declaration", [
    'function send(config){return fetch("/api/real",{method:"POST"})}',
    'function send(config){return request({...config,url:"/api/real"})}',
    'function send(config){config.method="POST"; return request(config)}',
    'function send(config){if (enabled) return request(config)}',
    'function send(config){return another(config)}',
    'function send(config,other){return request(config)}',
    'function send({url}){return request({url})}',
    'function send(config){return fetch(config)}',
    'function send(config){}',
    'function send(config){return;}',
])
def test_unverified_wrappers_never_publish_the_callers_unforwarded_request(declaration):
    result = extraction.extract_client_requests(declaration + 'send({url:"/api/fake",method:"DELETE"})')
    assert not any(operation["url"] == "/api/fake" for operation in result["operations"])
    assert any(request["reason"] == "unrecognized_request_wrapper" for request in result["unresolved_requests"])


def test_wrapper_identity_respects_shadowing_and_reassignment():
    source = (
        'function send(config){return request(config)}'
        'function f(send){send({url:"/api/fake",method:"DELETE"})}'
        'function g(){const send=other;send({url:"/api/also-fake",method:"DELETE"})}'
        'send({url:"/api/real",method:"GET"})'
    )
    assert [operation["url"] for operation in extraction.extract_client_requests(source)["operations"]] == ["/api/real"]
    result = extraction.extract_client_requests(
        'let send=(config)=>request(config); send=other; send({url:"/api/fake",method:"DELETE"})'
    )
    assert not result["operations"]


@pytest.mark.parametrize("name", ["request", "axios", "fetch"])
def test_local_functions_named_like_transports_cannot_invent_forwarding(name):
    source = (
        f'function {name}(config){{return window.fetch("/api/real",{{method:"POST"}})}}'
        f'{name}({{url:"/api/fake",method:"DELETE"}})'
    )
    result = extraction.extract_client_requests(source)
    assert [operation["url"] for operation in result["operations"]] == ["/api/real"]
    assert any(request["reason"] == "unrecognized_request_wrapper" for request in result["unresolved_requests"])


def test_dynamic_calls_to_unverified_wrappers_remain_coverage_gaps():
    result = extraction.extract_client_requests(
        'function send(config){if(enabled){return client.request(config)}} send(runtime)'
    )
    assert not result["operations"]
    assert any(request["reason"] == "unrecognized_request_wrapper" for request in result["unresolved_requests"])


def test_unresolved_configurations_survive_inventory_conversion():
    payload = {**extraction.extract_client_requests("request(dynamic)"), "target": "https://target.test"}
    manifest = {"items": []}
    attach_client_bundle_evidence(manifest, payload)
    assert manifest["unassessed_gaps"][0]["reason"] == "unresolved_client_request"
    assert manifest["unassessed_gaps"][0]["source_location"]["start_byte"] == 0


def test_empty_return_and_nonmember_enum_statements_are_safe():
    for source in [
        "const api=(e=>{return;})(x);",
        'const api=(e=>{e.x="/api/x"; log(e); local="value"; return e;})(x); fetch(`${api.x}/raw`)',
    ]:
        extraction.extract_client_requests(source)


def test_unknown_request_shaped_calls_are_available_for_agent_review():
    result = extraction.extract_client_requests('custom({url:"/api/x",method:"GET"})')
    assert not result["operations"]
    assert result["unresolved_requests"][0]["reason"] == "unrecognized_request_wrapper"


def test_unrecognized_wrapper_candidates_respect_request_limit(monkeypatch):
    monkeypatch.setattr(extraction, "_MAX_REQUESTS", 1)
    result = extraction.extract_client_requests('custom({url:"/api/x",method:"GET"});fetch("/api/y")')
    assert len(result["unresolved_requests"]) == 1
    assert result["request_analysis_status"] == "request_limit"
    assert not result["operations"]


@pytest.mark.parametrize(
    "source",
    [
        'const base=""; fetch(base + "/api/x")',
        'const base=""; fetch(`${base}/api/x`)',
    ],
)
def test_known_empty_string_components_are_not_treated_as_unknown(source):
    assert extraction.extract_client_requests(source)["operations"][0]["url"] == "/api/x"


def test_known_transport_without_explicit_method_preserves_unknown_method():
    operation = extraction.extract_client_requests('axios.request({url:"/api/x"})')["operations"][0]
    assert operation["url"] == "/api/x"
    assert operation["method"] is None


@pytest.mark.parametrize("method", ["PUT", None])
def test_source_parameters_replace_inferred_get_with_declared_or_unknown_method(method):
    manifest = records_to_inventory_manifest(
        [{"url": "https://target.test/api/x?p=1"}], target="https://target.test", target_id="target-1"
    )
    expression = '"PUT"' if method else "unknown"
    payload = {
        **extraction.extract_client_requests(f'request({{url:"/api/x?p=1",method:{expression}}})'),
        "target": "https://target.test",
        "target_id": "target-1",
    }
    extraction.attach_request_operations(manifest, payload)
    for item in manifest["items"]:
        if item["kind"] in {"endpoint", "parameter"}:
            assert item["attributes"]["interaction"]["operations"] == ([method] if method else [])


@pytest.mark.parametrize(
    "source",
    [
        'const api={path:"/api/x",...options};fetch(api.path)',
        'const api={path:"/api/x",[key]:value};fetch(api.path)',
        'request({url:"/api/x",method:"GET",...options})',
    ],
)
def test_dynamic_object_properties_do_not_produce_concrete_operations(source):
    result = extraction.extract_client_requests(source)
    assert not result["operations"]
    assert result["unresolved_requests"]


@pytest.mark.parametrize(
    "source",
    [
        'fetch("/api/x",options)',
        'fetch("/api/x",{method:"GET",...options})',
        'fetch("/api/x",{method:"GET",[key]:value})',
        'fetch("/api/x",{get method(){return "POST"}})',
    ],
)
def test_dynamic_fetch_configuration_does_not_invent_get(source):
    result = extraction.extract_client_requests(source)
    assert result["operations"][0]["method"] is None
    assert result["unresolved_requests"][0]["reason"] == "unresolved_method"
