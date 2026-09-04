from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from geoskillbench.batch_runner import BatchRunner
from geoskillbench.models.batch import BatchRequest
from geoskillbench.models.result import TestResult


def _mock_test_result(scenario_id: str, run_id: str, passed: bool = True) -> TestResult:
    return TestResult(
        run_id=run_id,
        scenario_id=scenario_id,
        scenario_name=f"Scenario {scenario_id}",
        status="passed" if passed else "failed",
        duration_ms=500,
        stage_results={},
        tool_calls=[{"tool_name": "createBuffer", "status": "success"}],
        assertions=[],
        judge={"score": 1.0 if passed else 0.0, "passed": passed},
        conversation=[],
        final_output={"final_response": "done"},
        loaded_skill_references=[],
        errors=[],
        operational_status="succeeded",
        evaluation_verdict="passed" if passed else "failed",
        termination_reason="completed",
        archive_status="succeeded",
        cleanup_status="succeeded",
        failures=[],
    )


def test_batch_runner_repeat_execution(tmp_path: Path) -> None:
    mock_runner = MagicMock()
    # 模拟 3 次运行
    mock_runner.run.side_effect = [
        _mock_test_result("sc_1", "b1_sc_1_1", passed=True),
        _mock_test_result("sc_1", "b1_sc_1_2", passed=True),
        _mock_test_result("sc_1", "b1_sc_1_3", passed=False),
    ]

    events = []
    def on_event(e):
        events.append(e)

    batch_runner = BatchRunner(runner=mock_runner)
    req = BatchRequest(scenarios=["scenarios/sc_1.yml"], repeat_count=3, output_dir=str(tmp_path))
    batch_result = batch_runner.run_batch(req, batch_id="b1", event_callback=on_event)

    assert mock_runner.run.call_count == 3
    assert batch_result.batch_id == "b1"
    assert batch_result.summary.total_runs == 3
    assert batch_result.summary.passed_runs == 2
    assert batch_result.summary.failed_runs == 1
    assert len(batch_result.runs) == 3

    # 验证事件流
    start_events = [e for e in events if e["type"] == "batch_start"]
    item_complete_events = [e for e in events if e["type"] == "batch_item_complete"]
    batch_complete_events = [e for e in events if e["type"] == "batch_complete"]
    assert len(start_events) == 1
    assert len(item_complete_events) == 3
    assert len(batch_complete_events) == 1

    # 验证批次产物已写入
    batch_dir = tmp_path / "batches" / "b1"
    assert (batch_dir / "summary.json").exists()
    assert (batch_dir / "summary.md").exists()
