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
