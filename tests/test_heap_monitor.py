import builtins
import importlib
import sys
from types import SimpleNamespace


def _import_heap_monitor(monkeypatch):
    monkeypatch.setenv("CYBER_HEAP_MONITOR_AUTOSTART", "0")
    sys.modules.pop("modules.utils.heap_monitor", None)
    return importlib.import_module("modules.utils.heap_monitor")


def test_take_dumps_writes_snapshot_and_traceback(monkeypatch, tmp_path):
    mod = _import_heap_monitor(monkeypatch)
    dumps = []
    traces = []

    monkeypatch.setattr(
        mod.tracemalloc,
        "take_snapshot",
        lambda: SimpleNamespace(dump=lambda path: dumps.append(path)),
    )
    monkeypatch.setattr(mod.faulthandler, "dump_traceback", lambda file: traces.append(file.name))

    prefix = tmp_path / "heap"
    mod.take_dumps(str(prefix))

    assert dumps == [f"{prefix}_tracemalloc.snapshot"]
    assert traces == [f"{prefix}_traceback.txt"]


def test_monitor_warns_when_no_memory_limit(monkeypatch, capsys):
    mod = _import_heap_monitor(monkeypatch)
    import resource

    def fake_open(path, *args, **kwargs):
        raise FileNotFoundError(path)

    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setattr(mod, "psutil", SimpleNamespace(Process=lambda: object()))
    monkeypatch.setattr(
        "resource.getrlimit",
        lambda limit: (resource.RLIM_INFINITY, resource.RLIM_INFINITY),
    )

    mod.monitor(max_iterations=1)

    assert "heap monitoring disabled" in capsys.readouterr().out


def test_monitor_uses_cgroup_limit_and_takes_dump_when_threshold_exceeded(monkeypatch):
    mod = _import_heap_monitor(monkeypatch)
    sleeps = []
    dumps = []

    class FakeFile:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return "100"

    class FakeProcess:
        def memory_info(self):
            return SimpleNamespace(rss=90)

    def fake_open(path, *args, **kwargs):
        if path.endswith("memory.limit_in_bytes"):
            return FakeFile()
        raise FileNotFoundError(path)

    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setattr(mod, "psutil", SimpleNamespace(Process=lambda: FakeProcess()))
    monkeypatch.setattr(mod, "take_dumps", lambda: dumps.append("dumped"))
    monkeypatch.setattr(mod.time, "sleep", sleeps.append)

    mod.monitor(interval=0.25, threshold_ratio=0.8, max_iterations=1)

    assert dumps == ["dumped"]
    assert sleeps == [30, 0.25]
