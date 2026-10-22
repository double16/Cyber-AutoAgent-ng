"""Source-backed relationships work for legacy bundles without remapping or executing code."""

import json

import pytest

from modules.tools.client_request_extraction import extract_client_requests
from modules.tools.client_route_extraction import saved_route_index


def source(compiled=False):
    element = "jsx(R, {path: paths.Panel, element: who ? jsx(Q, {}) : jsx(Go, {})})" if compiled else (
        "<R path={paths.Panel} element={who ? <Q/> : <Go/>}/>"
    )
    return """
const paths = (p => {p.Panel = "/control"; return p;} )({});
function check() { return fetch("/api/permission"); }
function search(value) { return fetch("/api/catalog?q=" + value); }
const alias = search;
function unused() { return fetch("/api/unrelated"); }
const Q = () => {
  const who = sessionStorage.getItem("identity");
  const [query, setQuery] = useState("");
  useEffect(() => {check().then(result => show(result.allowed)); alias(query);}, [who, query]);
  const never = () => fetch("/api/unused-local");
  return <input onChange={e => setQuery(e.target.value)}/>;
};
const Router = () => ELEMENT;
""".replace("ELEMENT", element)


@pytest.mark.parametrize("compiled", [False, True])
def test_route_guard_component_callbacks_and_aliased_helpers(compiled):
    result = extract_client_requests(source(compiled))
    assert result["route_analysis_status"] == "complete"
    link = result["route_links"][0]
    assert link["path"] == "/control"
    assert link["runtime_execution"] == "unestablished"
    definitions = {entry["symbol"]: entry for entry in link["definitions"]}
    assert {"Q", "check", "alias"} <= definitions.keys()
    urls = [operation["url"] for entry in definitions.values() for operation in entry["request_declarations"]]
    assert "/api/permission" in urls
    assert "/api/catalog" in urls
    assert "/api/unrelated" not in urls and "/api/unused-local" not in urls
    assert any(entry["symbol"] == "Go" for entry in link["unresolved_links"])


@pytest.mark.parametrize("declaration", [
    "function mount(Q) { return <R path=\"/control\" element={<Q/>}/>; }",
    ("let Q = () => fetch(\"/api/first\"); Q = () => fetch(\"/api/second\"); "
     "const mount = <R path=\"/control\" element={<Q/>}/>;"),
    "const mount = <R path=\"/control\" element={<Lazy/>}/>;",
])
def test_ambiguous_shadowed_and_external_components_are_not_guessed(declaration):
    result = extract_client_requests(declaration)
    link = result["route_links"][0]
    assert link["definitions"] == []
    assert link["unresolved_links"]


def test_dynamic_route_is_unresolved_and_no_name_based_link_is_created():
    result = extract_client_requests("const Q = () => fetch(\"/api/data\"); const routes = "
                                     "<><R path={unknown} element={<Q/>}/><R path=\"/static\" element={<div/>}/></>;")
    assert [link["path"] for link in result["route_links"]] == ["/static"]
    assert result["route_links"][0]["definitions"] == []


def test_cycles_and_depth_limits_remain_explicit():
    result = extract_client_requests("""
function A() { B(); } function B() { C(); A(); } function C() { D(); }
function D() { E(); } function E() { F(); } function F() { fetch("/api/deep"); }
const router = <R path="/control" element={<A/>}/>;
""")
    link = result["route_links"][0]
    reasons = {entry["reason"] for entry in link["unresolved_links"]}
    assert {"cycle", "traversal_limit"} <= reasons
    assert "F" not in {entry["symbol"] for entry in link["definitions"]}


def test_definition_limit_and_ambiguous_aliases_are_bounded():
    helpers = "\n".join(f'function F{index}() {{ return fetch("/api/{index}"); }}' for index in range(40))
    calls = ";".join(f"F{index}()" for index in range(40))
    result = extract_client_requests(helpers + f'\nfunction Q() {{{calls}}}\nconst router = <R path="/x" element={{<Q/>}}/>;')
    assert len(result["route_links"][0]["definitions"]) == 32
    assert any(entry["reason"] == "traversal_limit" for entry in result["route_links"][0]["unresolved_links"])
    result = extract_client_requests("const A = B; const B = A; const R = <Route path=\"/x\" element={<A/>}/>;")
    assert result["route_links"][0]["unresolved_links"]


def test_saved_index_keys_source_and_version_and_recovers_bad_cache(tmp_path):
    content = source().encode()
    first = saved_route_index(content, tmp_path)
    assert saved_route_index(content, tmp_path) == first
    path = next(tmp_path.glob("*.json"))
    path.write_text("broken")
    assert saved_route_index(content, tmp_path) == first
    path.write_text(json.dumps({**first, "extractor_version": -1}))
    assert saved_route_index(content, tmp_path) == first
    assert saved_route_index(source().replace("/control", "/different").encode(), tmp_path) != first


def test_unresolved_request_in_reachable_helper_is_preserved():
    result = extract_client_requests("""
function helper(value) { return fetch("/api/data/" + value); }
function Q() { helper(input); return <div/>; }
const router = <Route path="/x" element={<Q/>}/>;
""")
    definition = next(entry for entry in result["route_links"][0]["definitions"] if entry["symbol"] == "helper")
    assert definition["unresolved_request_declarations"]
