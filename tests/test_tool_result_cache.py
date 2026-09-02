"""Tests for filesystem-backed web-tool result caching."""

import json

from modules.tools import result_cache


def test_cache_round_trip_and_expiry(monkeypatch, tmp_path):
    monkeypatch.setattr(result_cache, "RESULT_CACHE_DIR", tmp_path)
    monkeypatch.setattr(result_cache.time, "time", lambda: 100.0)
    cache_key = result_cache.build_result_cache_key(target="https://target.test", headers={"X-Test": "1"})

    result_cache.cache_result("tool", cache_key, '{"status":"ok"}')
    assert result_cache.get_cached_result("tool", cache_key) == '{"status":"ok"}'

    monkeypatch.setattr(result_cache.time, "time", lambda: 100.0 + result_cache.RESULT_CACHE_TTL_SECONDS + 1)
    assert result_cache.get_cached_result("tool", cache_key) is None


def test_cache_ignores_malformed_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(result_cache, "RESULT_CACHE_DIR", tmp_path)
    cache_key = result_cache.build_result_cache_key(target="https://target.test")
    cache_file = tmp_path / "tool" / f"{cache_key}.json"
    cache_file.parent.mkdir()
    cache_file.write_text("not json", encoding="utf-8")

    assert result_cache.get_cached_result("tool", cache_key) is None


def test_cache_ignores_non_mapping_and_non_string_results(monkeypatch, tmp_path):
    monkeypatch.setattr(result_cache, "RESULT_CACHE_DIR", tmp_path)
    cache_key = result_cache.build_result_cache_key(target="https://target.test")
    cache_file = tmp_path / "tool" / f"{cache_key}.json"
    cache_file.parent.mkdir()

    cache_file.write_text("[]", encoding="utf-8")
    assert result_cache.get_cached_result("tool", cache_key) is None

    cache_file.write_text('{"expires_at": 9999999999, "result": {}}', encoding="utf-8")
    assert result_cache.get_cached_result("tool", cache_key) is None


def test_cache_key_normalizes_mapping_order():
    first = result_cache.build_result_cache_key(headers={"A": "1", "B": "2"})
    second = result_cache.build_result_cache_key(headers={"B": "2", "A": "1"})

    assert first == second
    assert first != result_cache.build_result_cache_key(headers={"A": "different", "B": "2"})


def test_cache_entry_is_json_on_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(result_cache, "RESULT_CACHE_DIR", tmp_path)
    cache_key = result_cache.build_result_cache_key(target="https://target.test")

    result_cache.cache_result("tool", cache_key, '{"status":"ok"}')

    entry = json.loads((tmp_path / "tool" / f"{cache_key}.json").read_text(encoding="utf-8"))
    assert entry["result"] == '{"status":"ok"}'
    assert entry["expires_at"] > 0


def test_cache_ignores_missing_expired_and_invalid_expiry_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(result_cache, "RESULT_CACHE_DIR", tmp_path)
    monkeypatch.setattr(result_cache.time, "time", lambda: 100.0)
    cache_key = result_cache.build_result_cache_key(target="https://target.test")

    assert result_cache.get_cached_result("missing", cache_key) is None
    cache_file = tmp_path / "tool" / f"{cache_key}.json"
    cache_file.parent.mkdir()
    cache_file.write_text('{"expires_at": "not-a-number", "result": "value"}', encoding="utf-8")
    assert result_cache.get_cached_result("tool", cache_key) is None

    cache_file.write_text('{"expires_at": 100, "result": "value"}', encoding="utf-8")
    assert result_cache.get_cached_result("tool", cache_key) is None


def test_cache_write_removes_temporary_file_after_write_error(monkeypatch, tmp_path):
    monkeypatch.setattr(result_cache, "RESULT_CACHE_DIR", tmp_path)
    temporary = tmp_path / "tool" / "temporary.json"

    class FailingTemporaryFile:
        name = str(temporary)

        def __enter__(self):
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text("partial", encoding="utf-8")
            return self

        def __exit__(self, *_args):
            return None

        def write(self, _value):
            return 0

    monkeypatch.setattr(result_cache.tempfile, "NamedTemporaryFile", lambda *_args, **_kwargs: FailingTemporaryFile())
    monkeypatch.setattr(result_cache.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("disk full")))

    result_cache.cache_result("tool", "cache-key", "value")

    assert not temporary.exists()


def test_cache_write_ignores_directory_creation_errors(monkeypatch):
    class FailingDirectory:
        def mkdir(self, **_kwargs):
            raise OSError("read-only cache")

    class FailingRoot:
        def __truediv__(self, _namespace):
            return FailingDirectory()

    monkeypatch.setattr(result_cache, "RESULT_CACHE_DIR", FailingRoot())

    result_cache.cache_result("tool", "cache-key", "value")
