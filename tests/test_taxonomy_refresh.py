"""Unit tests for the taxonomy refresh command-line wrapper."""

import runpy
from pathlib import Path

import pytest

import modules.config.taxonomy_refresh as taxonomy_refresh


_REFRESHED = {
    "cwe": [{"id": "CWE-79"}],
    "attack": [{"id": "T1190"}, {"id": "T1059"}],
}


class _Catalog:
    def __init__(self, refreshed):
        self.refreshed = refreshed
        self.refresh_calls = []

    def _refresh(self):
        self.refresh_calls.append("local")
        return self.refreshed

    def refresh_snapshot(self):
        self.refresh_calls.append("snapshot")
        return self.refreshed


@pytest.mark.parametrize(
    ("arguments", "expected_call", "destination"),
    [
        ([], "local", "local cache"),
        (["--write-snapshot"], "snapshot", "bundled snapshot"),
    ],
)
def test_main_refreshes_requested_destination_and_reports_counts(
    monkeypatch, capsys, arguments, expected_call, destination
):
    catalog = _Catalog(_REFRESHED)
    monkeypatch.setattr(taxonomy_refresh, "TaxonomyCatalog", lambda: catalog)
    monkeypatch.setattr("sys.argv", ["taxonomy_refresh", *arguments])

    assert taxonomy_refresh.main() == 0
    assert catalog.refresh_calls == [expected_call]
    assert capsys.readouterr().out == (
        f"Refreshed {destination}: 1 CWE, 2 ATT&CK records\n"
    )


@pytest.mark.parametrize("arguments", [[], ["--write-snapshot"]])
def test_main_returns_failure_without_reporting_when_refresh_fails(monkeypatch, capsys, arguments):
    catalog = _Catalog(None)
    monkeypatch.setattr(taxonomy_refresh, "TaxonomyCatalog", lambda: catalog)
    monkeypatch.setattr("sys.argv", ["taxonomy_refresh", *arguments])

    assert taxonomy_refresh.main() == 1
    assert capsys.readouterr().out == ""


def test_main_rejects_unknown_arguments(monkeypatch):
    monkeypatch.setattr("sys.argv", ["taxonomy_refresh", "--unknown"])

    with pytest.raises(SystemExit) as error:
        taxonomy_refresh.main()

    assert error.value.code == 2


def test_module_entry_point_executes_main(monkeypatch):
    catalog = _Catalog(_REFRESHED)
    monkeypatch.setattr(
        "modules.config.taxonomy_catalog.TaxonomyCatalog",
        lambda: catalog,
    )
    monkeypatch.setattr("sys.argv", ["taxonomy_refresh", "--write-snapshot"])

    with pytest.raises(SystemExit) as error:
        runpy.run_path(Path(taxonomy_refresh.__file__), run_name="__main__")

    assert error.value.code == 0
    assert catalog.refresh_calls == ["snapshot"]
