from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from geoskillbench.models.batch import BatchResult, BatchSummary
from geoskillbench.models.result import TestResult
from geoskillbench.security.redaction import redact


class ReportGenerator:
    def generate_json(self, result: TestResult) -> str:
        return json.dumps(redact(result.model_dump()), ensure_ascii=False, indent=2)

    def generate_markdown(self, result: TestResult) -> str:
        lines = [
            f"# {result.scenario_name}",
            "",
            f"- Scenario ID: `{result.scenario_id}`",
            f"- Run ID: `{result.run_id}`",
            f"- Status: `{result.status}`",
            f"- Evaluation verdict: `{result.evaluation_verdict}`",
            f"- Operational status: `{result.operational_status}`",
            f"- Termination reason: `{result.termination_reason}`",
            f"- Archive: `{result.archive_status}`; Cleanup: `{result.cleanup_status}`",
            f"- Duration: `{result.duration_ms} ms`",
            f"- Judge Score: `{result.judge.get('score', 0)}` (mode: `{result.judge.get('judge_mode', '')}`)",
            "",
            "## Stage Results",
        ]
        for stage, status in result.stage_results.items():
            lines.append(f"- `{stage}`: `{status}`")
        lines.extend(["", "## Assertions"])
        for item in result.assertions:
            backend = f" [{item['backend']}]" if item.get("backend") else ""
            lines.append(f"- `{item['type']}`{backend}: `{'passed' if item['passed'] else 'failed'}` - {item['message']}")
        lines.extend(["", "## Judge"])
        judge = result.judge or {}
        lines.append(f"- Mode: `{judge.get('judge_mode', '')}`")
        lines.append(f"- Model: `{judge.get('model', '') or '(规则判定)'}`")
        lines.append(f"- Score: `{judge.get('score', 0)}`")
        lines.append(f"- Passed: `{judge.get('passed', False)}`")
        if judge.get("reason"):
            lines.append(f"- Reason: {judge['reason']}")
        if judge.get("issues"):
            lines.append("- Issues:")
            lines.extend(f"  - {item}" for item in judge["issues"])
        if judge.get("suggestions"):
            lines.append("- Suggestions:")
            lines.extend(f"  - {item}" for item in judge["suggestions"])
        lines.extend(["", "## Tool Calls"])
        if result.tool_calls:
            for index, call in enumerate(result.tool_calls, start=1):
                lines.append(f"### {index}. `{call['tool_name']}` (`{call['status']}`)")
                lines.append("入参:")
                lines.append(f"```json\n{json.dumps(call.get('arguments') or {}, ensure_ascii=False, indent=2)}\n```")
                if call.get("result"):
                    lines.append("出参:")
                    lines.append(f"```json\n{json.dumps(call['result'], ensure_ascii=False, indent=2)}\n```")
        else:
            lines.append("- (无工具调用)")
        if result.loaded_skill_references:
            lines.extend(["", "## Loaded Skill References"])
            for reference in result.loaded_skill_references:
                path = reference["path"] if isinstance(reference, dict) else reference.path
                loaded_at = reference["loaded_at"] if isinstance(reference, dict) else reference.loaded_at
                lines.append(f"- `{path}` at `{loaded_at}`")
        lines.extend(["", "## Conversation"])
        if result.conversation:
            for index, message in enumerate(result.conversation, start=1):
                role = message.get("role", "message")
                content = message.get("content", "")
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False)
                lines.append(f"### {index}. {role}")
                lines.append(f"```text\n{content}\n```")
        else:
            lines.append("- (无对话记录)")
        external = result.final_output.get("external_interactions", []) if isinstance(result.final_output, dict) else []
        if external:
            lines.extend(["", "## External Agent Interactions"])
            for interaction in external:
                lines.append(f"### 指令 {interaction.get('turn', '?')}")
                lines.append("发给外部智能体:")
                lines.append(f"```text\n{interaction.get('instruction', '')}\n```")
                lines.append("外部智能体回答:")
                lines.append(f"```text\n{interaction.get('response', '')}\n```")
                for call in interaction.get("tool_calls") or []:
                    lines.append(f"- 外部工具: `{call.get('tool_name')}` (`{call.get('status')}`)")
        lines.extend(["", "## Final Response", "", result.final_output.get("final_response", "")])
        lines.extend(["", "## Errors"])
        if result.errors:
            lines.extend(f"- {error}" for error in result.errors)
        else:
            lines.append("- (无)")
        return "\n".join(lines)

    def generate_batch_json(self, batch_result: BatchResult) -> str:
        return json.dumps(redact(batch_result.model_dump()), ensure_ascii=False, indent=2)

    def generate_batch_markdown(self, batch_result: BatchResult) -> str:
        summary = batch_result.summary
        req = batch_result.request
        lines = [
            f"# 批次评测报告：`{batch_result.batch_id}`",
            "",
            f"- Status: `{summary.status}`",
            f"- Total Runs: `{summary.total_runs}` (Passed: `{summary.passed_runs}`, Failed: `{summary.failed_runs}`, Not Evaluable: `{summary.not_evaluable_runs}`)",
            f"- Pass Rate: `{summary.pass_rate * 100:.1f}%`",
            f"- Repeat Count per Scenario: `{req.repeat_count}`",
            f"- Created At: `{summary.created_at}`",
            "",
            "## 1. 总体分布与方差指标",
            "",
            "| 指标 | 均值 (Mean) | 标准差 (StdDev) | 最小值 (Min) | 中位数 (P50) | P90 | 最大值 (Max) |",
            "|---|---|---|---|---|---|---|",
            f"| 耗时 (ms) | {summary.overall_variance.duration_ms.mean} | {summary.overall_variance.duration_ms.std_dev} | {summary.overall_variance.duration_ms.min} | {summary.overall_variance.duration_ms.p50} | {summary.overall_variance.duration_ms.p90} | {summary.overall_variance.duration_ms.max} |",
            f"| 工具调用次数 | {summary.overall_variance.tool_calls_count.mean} | {summary.overall_variance.tool_calls_count.std_dev} | {summary.overall_variance.tool_calls_count.min} | {summary.overall_variance.tool_calls_count.p50} | {summary.overall_variance.tool_calls_count.p90} | {summary.overall_variance.tool_calls_count.max} |",
            f"| 对话交互轮次 | {summary.overall_variance.conversation_turns.mean} | {summary.overall_variance.conversation_turns.std_dev} | {summary.overall_variance.conversation_turns.min} | {summary.overall_variance.conversation_turns.p50} | {summary.overall_variance.conversation_turns.p90} | {summary.overall_variance.conversation_turns.max} |",
            f"| Judge 打分 | {summary.overall_variance.judge_score.mean} | {summary.overall_variance.judge_score.std_dev} | {summary.overall_variance.judge_score.min} | {summary.overall_variance.judge_score.p50} | {summary.overall_variance.judge_score.p90} | {summary.overall_variance.judge_score.max} |",
            "",
            f"- **轨迹多样性熵 (Trajectory Entropy)**: `{summary.overall_variance.trajectory_entropy}` (值越低代表 Agent 执行路径越确定/稳定)",
            "",
            "### 工具使用频次与覆盖率",
            "",
            "| 工具名 | 总调用次数 | 出现的 Run 数 | Run 覆盖率 | 单 Run 平均调用 |",
            "|---|---|---|---|---|",
        ]
        for t in summary.overall_variance.tool_usage_breakdown:
            lines.append(f"| `{t.tool_name}` | {t.total_calls} | {t.runs_used_in} | {t.usage_rate * 100:.1f}% | {t.mean_calls_per_run} |")

        lines.extend(["", "## 2. 按场景明细汇总", ""])
        lines.append("| 场景 ID | 场景名称 | 总运行 | 通过数 | 通过率 | 平均耗时 (ms) | 工具均值 | Judge 均分 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for sc_id, sc in summary.by_scenario.items():
            lines.append(
                f"| `{sc_id}` | {sc.scenario_name} | {sc.total_runs} | {sc.passed_runs} | {sc.pass_rate * 100:.1f}% | {sc.variance.duration_ms.mean} | {sc.variance.tool_calls_count.mean} | {sc.variance.judge_score.mean} |"
            )

        lines.extend(["", "## 3. 子运行记录", ""])
        lines.append("| Run ID | 场景 ID | 迭代轮次 | 最终状态 | 评测结论 | 耗时 (ms) | 工具数 | Judge 得分 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for r in batch_result.runs:
            lines.append(
                f"| `{r.run_id}` | `{r.scenario_id}` | #{r.iteration} | `{r.status}` | `{r.evaluation_verdict}` | {r.duration_ms} | {r.tool_call_count} | {r.judge_score} |"
            )
        return "\n".join(lines)

    def write_reports(self, output_dir: str, result: TestResult) -> tuple[Path, Path]:
        """统一按 reports/runs/<run_id>/ 保存运行产物，并在 legacy 目录下保留 latest 视图（向后兼容）。"""
        base_dir = Path(output_dir)
        run_dir = base_dir / "runs" / result.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        json_path = run_dir / "result.json"
        md_path = run_dir / "report.md"
        json_text = self.generate_json(result)
        md_text = self.generate_markdown(result)
        json_path.write_text(json_text, encoding="utf-8")
        md_path.write_text(md_text, encoding="utf-8")

        # 兼容旧路径: reports/json/{scenario_id}.json & reports/markdown/{scenario_id}.md
        legacy_json_dir = base_dir / "json"
        legacy_md_dir = base_dir / "markdown"
        legacy_json_dir.mkdir(parents=True, exist_ok=True)
        legacy_md_dir.mkdir(parents=True, exist_ok=True)
        (legacy_json_dir / f"{result.scenario_id}.json").write_text(json_text, encoding="utf-8")
        (legacy_md_dir / f"{result.scenario_id}.md").write_text(md_text, encoding="utf-8")

        self._persist_to_db(result, json_text, md_text)
        return json_path, md_path

    def write_batch_reports(self, output_dir: str, batch_result: BatchResult) -> tuple[Path, Path]:
        """保存批次聚合产物至 reports/batches/<batch_id>/ 并持久化至 DB。"""
        base_dir = Path(output_dir)
        batch_dir = base_dir / "batches" / batch_result.batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)

        json_path = batch_dir / "summary.json"
        md_path = batch_dir / "summary.md"
        json_text = self.generate_batch_json(batch_result)
        md_text = self.generate_batch_markdown(batch_result)
        json_path.write_text(json_text, encoding="utf-8")
        md_path.write_text(md_text, encoding="utf-8")

        self._persist_batch_to_db(batch_result, json_text)
        return json_path, md_path

    def _persist_batch_to_db(self, batch_result: BatchResult, json_text: str) -> None:
        from geoskillbench.api import db

        try:
            summary = batch_result.summary
            db.save_batch(
                {
                    "batch_id": batch_result.batch_id,
                    "created_at": summary.created_at,
                    "status": summary.status,
                    "total_runs": summary.total_runs,
                    "passed_runs": summary.passed_runs,
                    "failed_runs": summary.failed_runs,
                    "pass_rate": summary.pass_rate,
                    "summary_json": json.dumps(summary.model_dump(), ensure_ascii=False),
                    "result_json": json_text,
                }
            )
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("Failed to persist batch report to DB: %s", redact(str(exc)))

    def _persist_to_db(self, result: TestResult, json_text: str, md_text: str) -> None:
        """阶段二：把报告全文写入 reports 表（SQLite 本地 / PostGIS 服务器，由 DATABASE_URL 决定）。"""
        from geoskillbench.api import db

        safe_result = redact(result.model_dump())
        executor = ""
        if isinstance(safe_result.get("final_output"), dict):
            executor = safe_result["final_output"].get("executor", "") or ""
        try:
            db.save_report(
                {
                    "run_id": result.run_id,
                    "scenario_id": result.scenario_id,
                    "scenario_name": result.scenario_name,
                    "executor": executor,
                    "status": result.status,
                    "json": json_text,
                    "md": md_text,
                }
            )
        except Exception as exc:  # pragma: no cover - DB 故障不应中断评测
            import logging

            logging.getLogger(__name__).warning("Failed to persist report to DB: %s", redact(str(exc)))
