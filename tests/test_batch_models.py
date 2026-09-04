from __future__ import annotations

import pytest

from geoskillbench.models.batch import (
    BatchRequest,
    aggregate_batch_results,
    compute_variance_metrics,
)
from geoskillbench.models.result import TestResult


def _make_result(
    *,
    run_id: str = "run_1",
    scenario_id: str = "scenario_a",
    scenario_name: str = "Scenario A",
    evaluation_verdict: str = "passed",
    operational_status: str = "succeeded",
    duration_ms: int = 1000,
    tools: list[str] | None = None,
    turns: int = 2,
    judge_score: float = 0.9,
) -> TestResult:
    tool_calls = [{"tool_name": t, "status": "success"} for t in (tools or [])]
    conversation = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}] * turns
    return TestResult(
        run_id=run_id,
        scenario_id=scenario_id,
        scenario_name=scenario_name,
        status="passed" if evaluation_verdict == "passed" and operational_status == "succeeded" else "failed",
        duration_ms=duration_ms,
        stage_results={},
        tool_calls=tool_calls,
        assertions=[],
        judge={"score": judge_score, "passed": judge_score >= 0.8},
        conversation=conversation,
        final_output={"final_response": "done"},
        loaded_skill_references=[],
        errors=[],
        operational_status=operational_status,  # type: ignore[arg-type]
        evaluation_verdict=evaluation_verdict,  # type: ignore[arg-type]
        termination_reason="completed",
        archive_status="succeeded",
        cleanup_status="succeeded",
        failures=[],
    )


def test_aggregate_empty_results() -> None:
    summary = aggregate_batch_results("batch_empty", [])
    assert summary.batch_id == "batch_empty"
    assert summary.total_runs == 0
    assert summary.status == "failed"
    assert summary.pass_rate == 0.0


def test_aggregate_single_passed_result() -> None:
    r = _make_result(tools=["create_buffer"], duration_ms=500, judge_score=1.0)
    summary = aggregate_batch_results("batch_single", [r])
    assert summary.total_runs == 1
    assert summary.passed_runs == 1
    assert summary.pass_rate == 1.0
    assert summary.status == "succeeded"
    assert summary.overall_variance.duration_ms.mean == 500.0
    assert summary.overall_variance.duration_ms.std_dev == 0.0
    assert summary.overall_variance.tool_usage_breakdown[0].tool_name == "create_buffer"
    assert summary.overall_variance.tool_usage_breakdown[0].usage_rate == 1.0


def test_aggregate_mixed_results_and_scenarios() -> None:
    r1 = _make_result(run_id="r1", scenario_id="sc_1", evaluation_verdict="passed", duration_ms=1000, tools=["t1", "t2"])
    r2 = _make_result(run_id="r2", scenario_id="sc_1", evaluation_verdict="failed", duration_ms=2000, tools=["t1"])
    r3 = _make_result(run_id="r3", scenario_id="sc_2", evaluation_verdict="passed", duration_ms=1500, tools=["t3"])

    summary = aggregate_batch_results("batch_mixed", [r1, r2, r3])
    assert summary.total_runs == 3
    assert summary.passed_runs == 2
    assert summary.failed_runs == 1
    assert round(summary.pass_rate, 2) == 0.67
    assert summary.status == "partial"

    # 检查场景分层统计
    assert "sc_1" in summary.by_scenario
    assert "sc_2" in summary.by_scenario
    sc1_sum = summary.by_scenario["sc_1"]
    assert sc1_sum.total_runs == 2
    assert sc1_sum.passed_runs == 1
    assert sc1_sum.pass_rate == 0.5
    assert sc1_sum.variance.duration_ms.min == 1000.0
    assert sc1_sum.variance.duration_ms.max == 2000.0
    assert sc1_sum.variance.duration_ms.mean == 1500.0


def test_variance_tool_usage_and_entropy() -> None:
    # 模拟两次执行走完全相同轨迹，一次走不同分支
    r1 = _make_result(run_id="r1", tools=["query_metadata", "create_buffer"])
    r2 = _make_result(run_id="r2", tools=["query_metadata", "create_buffer"])
    r3 = _make_result(run_id="r3", tools=["query_metadata", "reproject", "create_buffer"])

    variance = compute_variance_metrics([r1, r2, r3])
    assert variance.trajectory_entropy > 0.0  # 有分支差异，熵大于0
    assert len(variance.tool_usage_breakdown) == 3

    # query_metadata 出现 3 次，覆盖率 100%
    meta_stat = next(s for s in variance.tool_usage_breakdown if s.tool_name == "query_metadata")
    assert meta_stat.runs_used_in == 3
    assert meta_stat.usage_rate == 1.0

    # reproject 出现 1 次，覆盖率 33.3%
    repr_stat = next(s for s in variance.tool_usage_breakdown if s.tool_name == "reproject")
    assert repr_stat.runs_used_in == 1
    assert round(repr_stat.usage_rate, 2) == 0.33


def test_batch_request_defaults() -> None:
    req = BatchRequest(scenarios=["s1", "s2"])
    assert req.repeat_count == 1
    assert req.concurrency == 1
    assert req.executor is None
