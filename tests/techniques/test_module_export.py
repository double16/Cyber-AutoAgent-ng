from types import SimpleNamespace

import pytest

from modules.techniques.module_export import export_bundle


def test_export_preserves_authored_bytes_and_existing_files(scripted_service, tmp_path):
    state = scripted_service
    revision = state.publish()
    store = state.service.store
    bundle = SimpleNamespace(delivered=[SimpleNamespace(revision_id=revision)] * 2)
    phases = ["analysis", "hypothesis_generation", "analysis"]
    paths = export_bundle(store, bundle, tmp_path, phases)
    assert len(paths) == 1
    assert paths[0].name.startswith("hypothesis-generation+analysis--")
    assert paths[0].read_text() == store.document(revision).markdown
    assert export_bundle(store, bundle, tmp_path, phases) == paths
    paths[0].write_text("Existing authored work")
    alternate = export_bundle(store, bundle, tmp_path, phases)
    assert alternate != paths
    assert paths[0].read_text() == "Existing authored work"
    assert export_bundle(store, bundle, tmp_path, phases) == alternate


def test_export_rejects_escaping_directory(tmp_path):
    module = tmp_path / "module"
    module.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (module / "techniques").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes"):
        export_bundle(None, SimpleNamespace(delivered=[]), module, [])


def test_export_rejects_file_symlink(scripted_service, tmp_path):
    revision = scripted_service.publish()
    bundle = SimpleNamespace(delivered=[SimpleNamespace(revision_id=revision)])
    store = scripted_service.service.store
    path = export_bundle(store, bundle, tmp_path, [])[0]
    path.unlink()
    path.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="symlink"):
        export_bundle(store, bundle, tmp_path, [])


@pytest.mark.parametrize("identical", [True, False])
def test_export_handles_publication_race(scripted_service, tmp_path, monkeypatch, identical):
    from pathlib import Path

    from modules.techniques import module_export

    revision = scripted_service.publish()
    store = scripted_service.service.store
    bundle = SimpleNamespace(delivered=[SimpleNamespace(revision_id=revision)])
    original_link = module_export.os.link
    attempted = []

    def race(source, target):
        if not attempted:
            attempted.append(target)
            Path(target).write_bytes(Path(source).read_bytes() if identical else b"Other document")
            raise FileExistsError()
        return original_link(source, target)

    monkeypatch.setattr(module_export.os, "link", race)
    paths = export_bundle(store, bundle, tmp_path, [])
    assert paths[0].read_text() == store.document(revision).markdown
    assert (paths[0] == attempted[0]) == identical
    assert not list((tmp_path / "techniques").glob(".technique-*"))
