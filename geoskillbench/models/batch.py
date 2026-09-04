from __future__ import annotations

import math
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator

from geoskillbench.models.result import EvaluationVerdict, OperationalStatus, TestResult


class DistributionStats(BaseModel):
    """数值分布统计（均值、标准差、极值、分位数）。"""
    count: int = 0
    mean: float = 0.0
    std_dev: float = 0.0
    min: float = 0.0
    max: float = 0.0
    p50: float = 0.0
    p90: float = 0.0


class ToolUsageStats(BaseModel):
    """单工具在批次中的调用统计。"""
    tool_name: str
    total_calls: int = 0
    runs_used_in: int = 0
    usage_rate: float = 0.0  # runs_used_in / total_runs
    mean_calls_per_run: float = 0.0


class VarianceMetrics(BaseModel):
    """过程与一致性方差度量。"""
    duration_ms: DistributionStats = Field(default_factory=DistributionStats)
    tool_calls_count: DistributionStats = Field(default_factory=DistributionStats)
    conversation_turns: DistributionStats = Field(default_factory=DistributionStats)
    judge_score: DistributionStats = Field(default_factory=DistributionStats)
    tool_usage_breakdown: list[ToolUsageStats] = Field(default_factory=list)
    trajectory_entropy: float = 0.0  # 轨迹离散度（基于工具调用序列的多样性）


class ScenarioBatchSummary(BaseModel):
    """按场景聚合的统计指标。"""
    scenario_id: str
    scenario_name: str = ""
    total_runs: int = 0
    passed_runs: int = 0
    failed_runs: int = 0
    not_evaluable_runs: int = 0
    pass_rate: float = 0.0
    verdict_distribution: dict[EvaluationVerdict, int] = Field(default_factory=dict)
    operational_distribution: dict[OperationalStatus, int] = Field(default_factory=dict)
    variance: VarianceMetrics = Field(default_factory=VarianceMetrics)


class BatchSummary(BaseModel):
    """批次整体聚合统计。"""
    batch_id: str
    created_at: str = ""
    status: Literal["running", "succeeded", "failed", "partial"] = "succeeded"
    total_runs: int = 0
    passed_runs: int = 0
    failed_runs: int = 0
    not_evaluable_runs: int = 0
    pass_rate: float = 0.0
    verdict_distribution: dict[EvaluationVerdict, int] = Field(default_factory=dict)
    operational_distribution: dict[OperationalStatus, int] = Field(default_factory=dict)
    overall_variance: VarianceMetrics = Field(default_factory=VarianceMetrics)
    by_scenario: dict[str, ScenarioBatchSummary] = Field(default_factory=dict)


class BatchRequest(BaseModel):
    """创建批次请求参数。"""
    scenarios: list[str]  # 场景 ID 或相对路径列表
    repeat_count: int = 1  # 每个场景重复运行次数
    concurrency: int = 1  # 调度并发度，默认 1（串行）
    executor: str | None = None
    agent_model: str | None = None
    judge_model: str | None = None
    memory_enabled: bool | None = None
    output_dir: str | None = None
    description: str = ""


class BatchRunRecord(BaseModel):
    """批次中单次运行的摘要引用。"""
    run_id: str
    scenario_id: str
    scenario_name: str
    iteration: int
    status: str
    evaluation_verdict: str
    duration_ms: int
    tool_call_count: int
    judge_score: float = 0.0


class BatchResult(BaseModel):
    """批次执行完成后的完整产物。"""
    batch_id: str
    request: BatchRequest
    summary: BatchSummary
    runs: list[BatchRunRecord] = Field(default_factory=list)


ATTRIBUTION_CATEGORIES: tuple[str, ...] = (
    "skill_prompt_issue",
    "agent_drift",
    "env_error",
    "assertion_or_scenario",
    "harness_variance",
    "unknown",
)

DiagnosticsSource = Literal["llm", "unavailable"]
SKILL_PATCH_THRESHOLD = 0.4


class SkillPatchSuggestion(BaseModel):
    """Skill Prompt 修复建议。仅供人工复制，平台不得写回 skills/。"""
    skill_id: str
    target_file: str
    diff_content: str
    explanation: str


class BatchAIDiagnostics(BaseModel):
    """批次横向 AI 诊断。辅助分析，不影响正式 verdict / pass_rate。"""
    batch_id: str
    created_at: str
    source: DiagnosticsSource = "llm"
    model: str = ""
    summary_text: str = ""
    attribution_breakdown: dict[str, float] = Field(default_factory=dict)
    root_cause_analysis: str = ""
    suggested_patch: SkillPatchSuggestion | None = None
    error: str | None = None

    @field_validator("attribution_breakdown")
    @classmethod
    def _known_categories(cls, value: dict[str, float]) -> dict[str, float]:
        unknown_keys = [key for key in value if key not in ATTRIBUTION_CATEGORIES]
        if unknown_keys:
            raise ValueError(f"未知归因类别: {unknown_keys}")
        return value


def _compute_distribution(values: list[float | int]) -> DistributionStats:
    if not values:
        return DistributionStats()
    n = len(values)
    float_vals = [float(v) for v in values]
    mean_val = sum(float_vals) / n
    variance = sum((x - mean_val) ** 2 for x in float_vals) / n if n > 1 else 0.0
    std_dev = math.sqrt(variance)
    sorted_vals = sorted(float_vals)

    def _percentile(p: float) -> float:
        k = (n - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_vals[int(k)]
        return sorted_vals[int(f)] * (c - k) + sorted_vals[int(c)] * (k - f)

    return DistributionStats(
        count=n,
        mean=round(mean_val, 2),
        std_dev=round(std_dev, 2),
        min=round(sorted_vals[0], 2),
        max=round(sorted_vals[-1], 2),
        p50=round(_percentile(0.50), 2),
        p90=round(_percentile(0.90), 2),
    )


def compute_variance_metrics(results: list[TestResult]) -> VarianceMetrics:
    """计算一组运行结果的过程方差与工具使用统计。"""
    if not results:
        return VarianceMetrics()

    total_runs = len(results)
    durations = [r.duration_ms for r in results]
    tool_counts = [len(r.tool_calls) for r in results]
    turn_counts = [len(r.conversation) // 2 if r.conversation else 1 for r in results]
    judge_scores = [float(r.judge.get("score", 0.0)) for r in results if r.judge]

    # 工具使用分布
    tool_run_presence: dict[str, int] = {}
    tool_total_calls: dict[str, int] = {}
    trajectories: list[tuple[str, ...]] = []

    for r in results:
        calls = r.tool_calls or []
        names = [call.get("tool_name", "") for call in calls if isinstance(call, dict)]
        trajectories.append(tuple(names))
        unique_tools = set(names)
        for t in unique_tools:
            tool_run_presence[t] = tool_run_presence.get(t, 0) + 1
        for t in names:
            tool_total_calls[t] = tool_total_calls.get(t, 0) + 1

    all_tool_names = sorted(set(tool_total_calls.keys()))
    breakdown = [
        ToolUsageStats(
            tool_name=name,
            total_calls=tool_total_calls.get(name, 0),
            runs_used_in=tool_run_presence.get(name, 0),
            usage_rate=round(tool_run_presence.get(name, 0) / total_runs, 4),
            mean_calls_per_run=round(tool_total_calls.get(name, 0) / total_runs, 2),
        )
        for name in all_tool_names
        if name
    ]

    # 轨迹多样性/熵 (Shannon Entropy)
    trajectory_counts: dict[tuple[str, ...], int] = {}
    for t in trajectories:
        trajectory_counts[t] = trajectory_counts.get(t, 0) + 1
    entropy = 0.0
    for count in trajectory_counts.values():
        p = count / total_runs
        if p > 0:
            entropy -= p * math.log2(p)

    return VarianceMetrics(
        duration_ms=_compute_distribution(durations),
        tool_calls_count=_compute_distribution(tool_counts),
        conversation_turns=_compute_distribution(turn_counts),
        judge_score=_compute_distribution(judge_scores),
        tool_usage_breakdown=breakdown,
        trajectory_entropy=round(entropy, 4),
    )


def aggregate_batch_results(batch_id: str, results: list[TestResult], created_at: str = "") -> BatchSummary:
    """根据子运行列表聚合批次指标。"""
    total = len(results)
    if total == 0:
        return BatchSummary(
            batch_id=batch_id,
            created_at=created_at,
            status="failed",
            total_runs=0,
            passed_runs=0,
            failed_runs=0,
            not_evaluable_runs=0,
            pass_rate=0.0,
        )

    verdict_dist: dict[EvaluationVerdict, int] = {}
    oper_dist: dict[OperationalStatus, int] = {}
    passed_count = 0
    failed_count = 0
    not_eval_count = 0

    by_scenario_map: dict[str, list[TestResult]] = {}

    for r in results:
        v = r.evaluation_verdict
        verdict_dist[v] = verdict_dist.get(v, 0) + 1
        if v == "passed":
            passed_count += 1
        elif v == "failed":
            failed_count += 1
        else:
            not_eval_count += 1

        op = r.operational_status
        oper_dist[op] = oper_dist.get(op, 0) + 1

        by_scenario_map.setdefault(r.scenario_id, []).append(r)

    by_scenario_summaries: dict[str, ScenarioBatchSummary] = {}
    for sc_id, sc_results in by_scenario_map.items():
        sc_total = len(sc_results)
        sc_passed = sum(1 for r in sc_results if r.evaluation_verdict == "passed")
        sc_failed = sum(1 for r in sc_results if r.evaluation_verdict == "failed")
        sc_not_eval = sc_total - sc_passed - sc_failed
        sc_v_dist: dict[EvaluationVerdict, int] = {}
        sc_op_dist: dict[OperationalStatus, int] = {}
        for r in sc_results:
            sc_v_dist[r.evaluation_verdict] = sc_v_dist.get(r.evaluation_verdict, 0) + 1
            sc_op_dist[r.operational_status] = sc_op_dist.get(r.operational_status, 0) + 1

        by_scenario_summaries[sc_id] = ScenarioBatchSummary(
            scenario_id=sc_id,
            scenario_name=sc_results[0].scenario_name if sc_results else sc_id,
            total_runs=sc_total,
            passed_runs=sc_passed,
            failed_runs=sc_failed,
            not_evaluable_runs=sc_not_eval,
            pass_rate=round(sc_passed / sc_total, 4) if sc_total > 0 else 0.0,
            verdict_distribution=sc_v_dist,
            operational_distribution=sc_op_dist,
            variance=compute_variance_metrics(sc_results),
        )

    pass_rate = round(passed_count / total, 4) if total > 0 else 0.0
    status: Literal["succeeded", "failed", "partial"] = "succeeded"
    if passed_count == 0:
        status = "failed"
    elif passed_count < total:
        status = "partial"

    return BatchSummary(
        batch_id=batch_id,
        created_at=created_at,
        status=status,
        total_runs=total,
        passed_runs=passed_count,
        failed_runs=failed_count,
        not_evaluable_runs=not_eval_count,
        pass_rate=pass_rate,
        verdict_distribution=verdict_dist,
        operational_distribution=oper_dist,
        overall_variance=compute_variance_metrics(results),
        by_scenario=by_scenario_summaries,
    )
