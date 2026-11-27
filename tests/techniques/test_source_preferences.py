"""Advisory source inheritance contracts."""
from types import SimpleNamespace

import pytest

from modules.techniques.source_preferences import resolve_sources


def loader(rows):
    return SimpleNamespace(
        _find_module_dir=lambda name: name if name in rows else None,
        _get_extend_list=lambda name: rows[name].get("extend", []),
        _read_module_yaml=lambda name: rows[name],
    )


def test_branch_replacement_and_deduplication():
    rows = {
        "root": {"technique_sources": {"sites": ["https://example.org/"]}},
        "a": {"extend": ["root"], "technique_sources": {"mode": "replace", "sites": []}},
        "b": {"extend": ["root"]},
        "child": {"extend": ["a", "b"], "technique_sources": {"sites": ["https://EXAMPLE.org"]}},
    }
    assert resolve_sources(loader(rows), "child") == ["https://example.org"]
    assert resolve_sources(loader(rows), "missing") == []
    rows["root"]["extend"] = ["child"]
    assert resolve_sources(loader(rows), "child") == ["https://example.org"]


@pytest.mark.parametrize("config", [
    {"mode": "bad"}, {"unknown": True}, {"sites": ["ftp://example.org"]},
    {"sites": ["https://u:p@example.org"]}, {"sites": ["https://example.org?q=x"]},
    {"sites": ["https://example.org/#x"]}, {"sites": ["nonsense"]},
])
def test_invalid_configuration(config):
    with pytest.raises(ValueError, match="Module web: invalid technique_sources"):
        resolve_sources(loader({"web": {"technique_sources": config}}), "web")


def test_contributors_and_path_preservation():
    contributors = []
    rows = {
        "base": {"technique_sources": {"sites": ["https://Example.org/docs/"]}},
        "child": {"extend": ["base"], "technique_sources": {"sites": ["https://example.org/other"]}},
    }
    assert resolve_sources(loader(rows), "child", contributors=contributors) == [
        "https://example.org/docs", "https://example.org/other",
    ]
    assert contributors == ["base", "child"]


@pytest.mark.parametrize("site", ["https://example.org:99999", "https://example.org:bad", "https://bad host/", "https://example.org?", "https://example.org#"])
def test_invalid_url_components(site):
    with pytest.raises(ValueError, match="Module web"):
        resolve_sources(loader({"web": {"technique_sources": {"sites": [site]}}}), "web")

