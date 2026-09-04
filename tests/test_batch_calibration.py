from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from geoskillbench.batch_runner import BatchRunner
from geoskillbench.models.batch import (
    BatchRequest,
    aggregate_batch_results,
    compute_variance_metrics,
)
from geoskillbench.models.result import TestResult


def _create_synthetic_run(
    run_id: str,
    scenario_id: str,
    tools: list[str],
    duration_ms: int,
    judge_score: float,
    verdict: str = "passed",
) -> TestResult:
    return TestResult(
        run_id=run_id,
        scenario_id=scenario_id,
        scenario_name=f"Scenario {scenario_id}",
        status="passed" if verdict == "passed" else "failed",
        duration_ms=duration_ms,
        stage_results={},
        tool_calls=[{"tool_name": t, "status": "success"} for t in tools],
        assertions=[],
        judge={"score": judge_score, "passed": judge_score >= 0.8},
        conversation=[{"role": "user", "content": "task"}] * 2,
        final_output={"final_response": "done"},
        loaded_skill_references=[],
        errors=[],
        operational_status="succeeded",
        evaluation_verdict=verdict,  # type: ignore[arg-type]
        termination_reason="completed",
        archive_status="succeeded",
        cleanup_status="succeeded",
        failures=[],
    )


def test_harness_trajectory_stability_calibration() -> None:
    """标定验证：当 Agent 轨迹 100% 确定时，轨迹熵为 0；当出现分支漂移时，熵增加。"""
    # 1. 轨迹绝对稳定（3次运行调用相同工具顺序）
    stable_runs = [
        _create_synthetic_run("r1", "sc1", ["query_dataset_metadata", "createBuffer"], 1000, 1.0),
        _create_synthetic_run("r2", "sc1", ["query_dataset_metadata", "createBuffer"], 1050, 1.0),
        _create_synthetic_run("r3", "sc1", ["query_dataset_metadata", "createBuffer"], 980, 1.0),
    ]
    stable_metrics = compute_variance_metrics(stable_runs)
    assert stable_metrics.trajectory_entropy == 0.0
    assert len(stable_metrics.tool_usage_breakdown) == 2
    for tool in stable_metrics.tool_usage_breakdown:
        assert tool.usage_rate == 1.0
        assert tool.mean_calls_per_run == 1.0

    # 2. 轨迹出现随机漂移（发生额外工具调用或未调必要工具）
    drift_runs = [
        _create_synthetic_run("r1", "sc1", ["query_dataset_metadata", "createBuffer"], 1000, 1.0),
        _create_synthetic_run("r2", "sc1", ["query_dataset_metadata", "reproject_dataset", "createBuffer"], 1500, 0.9),
        _create_synthetic_run("r3", "sc1", ["query_dataset_metadata"], 500, 0.2, verdict="failed"),
    ]
    drift_metrics = compute_variance_metrics(drift_runs)
    assert drift_metrics.trajectory_entropy > 1.0  # 3 条完全不同轨迹，最大熵 = log2(3) ≈ 1.58
    assert drift_metrics.duration_ms.std_dev > 0.0


def test_judge_scoring_consistency_calibration() -> None:
    """标定验证：量化 LLM/Rule Judge 分数波动性与一致性。"""
    runs = [
        _create_synthetic_run("r1", "sc1", ["t1"], 1000, judge_score=0.9),
        _create_synthetic_run("r2", "sc1", ["t1"], 1000, judge_score=0.85),
        _create_synthetic_run("r3", "sc1", ["t1"], 1000, judge_score=0.95),
    ]
    summary = aggregate_batch_results("batch_judge_calib", runs)
    assert summary.overall_variance.judge_score.mean == 0.9
    assert summary.overall_variance.judge_score.min == 0.85
    assert summary.overall_variance.judge_score.max == 0.95
    assert 0.03 < summary.overall_variance.judge_score.std_dev < 0.06
    assert summary.pass_rate == 1.0
