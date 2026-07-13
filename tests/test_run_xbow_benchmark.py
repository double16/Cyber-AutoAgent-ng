"""Tests for XBOW benchmark process lifecycle handling."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from benchmark_harness import run_xbow_benchmark


def test_run_agent_process_returns_completed_process(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = Mock(returncode=0)
    run = Mock(return_value=completed)
    stop = Mock()
    monkeypatch.setattr(run_xbow_benchmark, "_run", run)
    monkeypatch.setattr(run_xbow_benchmark, "stop_benchmark", stop)

    result = run_xbow_benchmark._run_agent_process(
        ["agent"],
        xbow_root=Path("/benchmarks"),
        bench_id="XBEN-001-24",
        keep_running=False,
        cwd=Path("/ui"),
    )

    assert result is completed
    run.assert_called_once_with(["agent"], cwd=Path("/ui"), capture=False)
    stop.assert_not_called()


def test_run_agent_process_stops_benchmark_on_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_xbow_benchmark, "_run", Mock(side_effect=KeyboardInterrupt))
    stop = Mock(return_value=True)
    monkeypatch.setattr(run_xbow_benchmark, "stop_benchmark", stop)

    with pytest.raises(KeyboardInterrupt):
        run_xbow_benchmark._run_agent_process(
            ["agent"],
            xbow_root=Path("/benchmarks"),
            bench_id="XBEN-001-24",
            keep_running=False,
        )

    stop.assert_called_once_with(Path("/benchmarks"), "XBEN-001-24")


def test_run_agent_process_respects_keep_running_on_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_xbow_benchmark, "_run", Mock(side_effect=KeyboardInterrupt))
    stop = Mock()
    monkeypatch.setattr(run_xbow_benchmark, "stop_benchmark", stop)

    with pytest.raises(KeyboardInterrupt):
        run_xbow_benchmark._run_agent_process(
            ["agent"],
            xbow_root=Path("/benchmarks"),
            bench_id="XBEN-001-24",
            keep_running=True,
        )

    stop.assert_not_called()


def test_cleanup_failure_does_not_mask_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_xbow_benchmark, "_run", Mock(side_effect=KeyboardInterrupt))
    monkeypatch.setattr(run_xbow_benchmark, "stop_benchmark", Mock(side_effect=RuntimeError("Docker unavailable")))

    with pytest.raises(KeyboardInterrupt):
        run_xbow_benchmark._run_agent_process(
            ["agent"],
            xbow_root=Path("/benchmarks"),
            bench_id="XBEN-001-24",
            keep_running=False,
        )
