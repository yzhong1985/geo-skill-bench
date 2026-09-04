from __future__ import annotations

import datetime
import uuid
from pathlib import Path
from typing import Any, Callable

from geoskillbench.models.batch import (
    BatchRequest,
    BatchResult,
    BatchRunRecord,
    aggregate_batch_results,
)
from geoskillbench.models.result import TestResult
from geoskillbench.reports.report_generator import ReportGenerator
from geoskillbench.runner import TestRunner


class BatchRunner:
    """批次评测执行器：支持单场景 repeat 重复与多场景批量调度。"""

    def __init__(self, runner: TestRunner | None = None) -> None:
        self.runner = runner or TestRunner()
        self.report_generator = ReportGenerator()

    def run_batch(
        self,
        request: BatchRequest,
        batch_id: str | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> BatchResult:
        batch_id = batch_id or f"batch_{uuid.uuid4().hex[:12]}"
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        output_dir = request.output_dir or "reports"

        def emit(event_type: str, **payload: Any) -> None:
            if event_callback is not None:
                event_callback({"type": event_type, "batch_id": batch_id, **payload})

        emit("batch_start", total_scenarios=len(request.scenarios), repeat_count=request.repeat_count)

        run_results: list[TestResult] = []
        run_records: list[BatchRunRecord] = []
        total_runs_planned = len(request.scenarios) * request.repeat_count
        current_run_index = 0

        # 构建运行配置覆写
        run_config: dict[str, Any] = {}
        if request.executor:
            run_config["executor"] = request.executor
        if request.memory_enabled is not None:
            run_config["memory_enabled"] = request.memory_enabled

        for scenario_path_str in request.scenarios:
            scenario_path = Path(scenario_path_str)
            for iteration in range(1, request.repeat_count + 1):
                current_run_index += 1
                run_id = f"{batch_id}_{scenario_path.stem}_{iteration}"

                emit(
                    "batch_item_start",
                    scenario=scenario_path_str,
                    iteration=iteration,
                    run_id=run_id,
                    current_index=current_run_index,
                    total_runs=total_runs_planned,
                )

                # 将子运行的事件包装后向外转发
                def _forward_event(evt: dict[str, Any]) -> None:
                    emit("batch_item_event", run_id=run_id, iteration=iteration, scenario=scenario_path_str, event=evt)

                test_result = self.runner.run(
                    scenario_path=str(scenario_path),
                    output_dir=output_dir,
                    event_callback=_forward_event,
                    run_config=run_config,
                    run_id=run_id,
                )

                run_results.append(test_result)
                record = BatchRunRecord(
                    run_id=test_result.run_id,
                    scenario_id=test_result.scenario_id,
                    scenario_name=test_result.scenario_name,
                    iteration=iteration,
                    status=test_result.status,
                    evaluation_verdict=test_result.evaluation_verdict,
                    duration_ms=test_result.duration_ms,
                    tool_call_count=len(test_result.tool_calls),
                    judge_score=float(test_result.judge.get("score", 0.0)) if test_result.judge else 0.0,
                )
                run_records.append(record)

                emit(
                    "batch_item_complete",
                    run_id=run_id,
                    iteration=iteration,
                    scenario=scenario_path_str,
                    status=test_result.status,
                    verdict=test_result.evaluation_verdict,
                    current_index=current_run_index,
                    total_runs=total_runs_planned,
                )

        summary = aggregate_batch_results(batch_id, run_results, created_at=created_at)
        batch_result = BatchResult(
            batch_id=batch_id,
            request=request,
            summary=summary,
            runs=run_records,
        )

        # 写入批次产物至 reports/batches/<batch_id>/
        try:
            self.report_generator.write_batch_reports(output_dir, batch_result)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Failed to write batch reports: %s", exc)

        emit(
            "batch_complete",
            status=summary.status,
            pass_rate=summary.pass_rate,
            total_runs=summary.total_runs,
            passed_runs=summary.passed_runs,
            summary=summary.model_dump(),
        )
        return batch_result
