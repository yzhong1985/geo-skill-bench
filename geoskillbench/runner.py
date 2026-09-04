from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from geoskillbench.executors.factory import ExecutorFactory
from geoskillbench.assertions.assertion_engine import AssertionEngine
from geoskillbench.assertions.sql_result_comparator import PostgisResultComparator
from geoskillbench.data_service import DataServiceClient, DataServiceError
from geoskillbench.fixtures.fixture_manager import FixtureManager
from geoskillbench.loader.scenario_loader import ScenarioLoader
from geoskillbench.loader.skill_loader import SkillLoader
from geoskillbench.mcp.mcp_tool_adapter import MCPToolAdapter
from geoskillbench.models.result import FailureRecord, ExecutorSessionRequest, TestResult
from geoskillbench.models.scenario import AgentConfig
from geoskillbench.models.test_context import MCPToolContext, SkillContext, TestContext
from geoskillbench.recorder.execution_recorder import ExecutionRecorder
from geoskillbench.reports.report_generator import ReportGenerator
from geoskillbench.runtime.judge_runtime import JudgeEngine


STAGES = [
    "LOAD_SCENARIO",
    "PREPARE_DATA",
    "CONNECT_MCP",
    "LOAD_SKILL",
    "RUN_AGENT",
    "RUN_ASSERTIONS",
    "RUN_JUDGE",
    "GENERATE_REPORT",
    "CLEANUP",
]


def _user_config(agent: AgentConfig | None) -> dict[str, Any]:
    """从 agent 配置提取模拟用户设定（扁平 dict），executor 统一从 role_model_config['user'] 读。

    skill 场景（agent=None）也能拿到默认值，保证 skill 反问闭环可用。
    """
    agent_dict = agent.model_dump() if agent is not None else {}
    return {
        "user_enabled": agent_dict.get("user_enabled", True),
        "user_profile": agent_dict.get("user_profile", "normal_user"),
        "user_goal": agent_dict.get("user_goal", ""),
        "user_max_turns": agent_dict.get("user_max_turns", 5),
        "user_model": agent_dict.get("user_model", "rule-based-user"),
        "ask_user": agent_dict.get("ask_user", False),
    }


def _failure(
    failures: list[FailureRecord],
    *,
    phase: str,
    code: str,
    message: str,
    source: str = "platform",
    fatal: bool = True,
    retryable: bool = False,
) -> None:
    failures.append(
        FailureRecord(
            phase=phase,
            code=code,
            message=message,
            source=source,  # type: ignore[arg-type]
            fatal=fatal,
            retryable=retryable,
        )
    )


def _evaluation_verdict(
    *,
    execution_ok: bool,
    assertion_result: Any | None,
    judge_result: Any | None,
) -> str:
    if not execution_ok:
        return "failed"
    if assertion_result is None or judge_result is None:
        return "not_evaluable"
    if assertion_result.status == "skipped":
        if judge_result.judge_mode == "disabled" or judge_result.status in {"unavailable", "invalid"}:
            return "not_evaluable"
        return "passed" if judge_result.passed else "failed"
    if not assertion_result.passed:
        return "failed"
    if judge_result.status in {"unavailable", "invalid"} or judge_result.judge_mode == "error":
        return "not_evaluable"
    return "passed" if judge_result.passed else "failed"


def _top_level_status(
    evaluation_verdict: str,
    operational_status: str,
    archive_status: str,
    cleanup_status: str,
    failures: list[FailureRecord],
) -> str:
    return "passed" if (
        evaluation_verdict == "passed"
        and operational_status == "succeeded"
        and archive_status in {"succeeded", "not_attempted"}
        and cleanup_status in {"succeeded", "not_attempted"}
        and not any(failure.fatal for failure in failures)
    ) else "failed"


class TestRunner:
    def __init__(self) -> None:
        self.scenario_loader = ScenarioLoader()
        self.fixture_manager = FixtureManager()
        self.skill_loader = SkillLoader()
        self.adapter = MCPToolAdapter()
        self.assertion_engine = AssertionEngine(
            sql_comparator=PostgisResultComparator.from_env(),
            result_locator=self.adapter.get_result_location,
        )
        self.judge_engine = JudgeEngine()
        self.report_generator = ReportGenerator()

    def validate(self, scenario_path: str) -> dict:
        scenario = self.scenario_loader.load(scenario_path)
        skill = None
        if scenario.type != "agent_test":
            skill = self.skill_loader.load(scenario.skill, getattr(scenario, "_base_path", "."))
        return {
            "scenario_id": scenario.id,
            "scenario_name": scenario.name,
            "skill_id": skill.id if skill else None,
            "scenario_type": scenario.type,
            "executor": scenario.runtime.executor,
            "required_tools": [tool.name for tool in scenario.mcp.tools.required],
        }

    def list_tools(self, scenario_path: str) -> list[dict]:
        scenario = self.scenario_loader.load(scenario_path)
        self.adapter.connect_servers(scenario.mcp.servers)
        self.adapter.get_agent_tools(scenario.mcp.tools.required, scenario.mcp.tools.optional)
        return [
            {"server": tool.server, "name": tool.name, "optional": tool.optional}
            for tool in self.adapter.list_tools()
        ]

    def run(
        self,
        scenario_path: str,
        output_dir: str | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        run_config: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> TestResult:
        run_id = run_id or uuid.uuid4().hex
        base_output = Path(output_dir) if output_dir else Path("reports")
        # mock 工具真实几何产出的落盘目录（run_id 隔离，报告产物持久化）
        self.adapter.set_output_dir(base_output / "outputs" / run_id)
        # 数据库拉取临时文件目录（cleanup 时删除，不进报告产物）
        self.fixture_manager.set_work_dir(base_output / "outputs" / run_id / "_db_pull")
        stage_results = {stage: "PENDING" for stage in STAGES}
        failures: list[FailureRecord] = []
        execution_status = "running"
        termination_reason = "completed"
        start_time = time.perf_counter()
        recorder = ExecutionRecorder(scenario_id=Path(scenario_path).stem)
        test_context = None
        scenario = None
        registration = None
        data_service = None
        executor = None
        session = None

        def emit(event_type: str, **payload: Any) -> None:
            if event_callback is not None:
                event_callback({"type": event_type, **payload})

        try:
            stage_results["LOAD_SCENARIO"] = "RUNNING"
            emit("stage", stage="LOAD_SCENARIO", status="RUNNING", stage_results=dict(stage_results))
            scenario = self.scenario_loader.load(scenario_path)
            recorder.scenario_id = scenario.id
            stage_results["LOAD_SCENARIO"] = "PASSED"
            emit("stage", stage="LOAD_SCENARIO", status="PASSED", stage_results=dict(stage_results), scenario_id=scenario.id)

            stage_results["PREPARE_DATA"] = "RUNNING"
            emit("stage", stage="PREPARE_DATA", status="RUNNING", stage_results=dict(stage_results))
            if scenario.data.service:
                token = os.environ.get(scenario.data.service.credential_env, "") if scenario.data.service.credential_env else None
                data_service = DataServiceClient(scenario.data.service.url, token=token, timeout=scenario.data.service.timeout_seconds)
                logical_inputs = [fixture.catalog_id for fixture in scenario.data.fixtures if fixture.catalog_id]
                logical_references = [fixture.evaluation_id for fixture in scenario.data.reference if fixture.evaluation_id]
                registration = data_service.register_run(
                    run_id,
                    scenario_id=scenario.id,
                    inputs=logical_inputs,
                    references=logical_references,
                    idempotency_key=run_id,
                )
            datasets, reference_datasets = self.fixture_manager.prepare(scenario, registration)
            stage_results["PREPARE_DATA"] = "PASSED"
            emit("stage", stage="PREPARE_DATA", status="PASSED", stage_results=dict(stage_results))

            stage_results["CONNECT_MCP"] = "RUNNING"
            emit("stage", stage="CONNECT_MCP", status="RUNNING", stage_results=dict(stage_results))
            # 顺序：先注册数据集再连 server。mock server 启动时通过 env 注入数据映射，
            # register_datasets 必须先于 connect_servers，否则 mock 进程拿不到 fixtures。
            self.adapter.register_datasets(datasets, run_id=run_id)
            self.adapter.connect_servers(scenario.mcp.servers)
            # 只注册输入数据（fixtures）：参考数据（data.reference）不注册进 adapter，
            # agent 的工具解析不到它，避免标准答案被当作可用数据集操作。
            tools = self.adapter.get_agent_tools(scenario.mcp.tools.required, scenario.mcp.tools.optional)
            self.adapter.validate_required_tools(scenario.mcp.tools.required)
            stage_results["CONNECT_MCP"] = "PASSED"
            emit("stage", stage="CONNECT_MCP", status="PASSED", stage_results=dict(stage_results))

            stage_results["LOAD_SKILL"] = "RUNNING"
            emit("stage", stage="LOAD_SKILL", status="RUNNING", stage_results=dict(stage_results))
            if scenario.type == "agent_test":
                skill = None
                stage_results["LOAD_SKILL"] = "SKIPPED"
                emit("stage", stage="LOAD_SKILL", status="SKIPPED", stage_results=dict(stage_results), reason="agent_test 不加载 skill")
            else:
                skill = self.skill_loader.load(scenario.skill, getattr(scenario, "_base_path", "."))
                recorder.record_skill_load(skill.id)
                stage_results["LOAD_SKILL"] = "PASSED"
                emit("stage", stage="LOAD_SKILL", status="PASSED", stage_results=dict(stage_results), skill_id=skill.id)

                # fail-fast：skill 声明所需工具必须被 server 提供，否则 agent 不启动、直接 fail。
                # 工具已通过 CONNECT_MCP 阶段 tools/list 自动发现进 adapter；缺哪工具直接报出。
                skill_required = getattr(skill, "recommended_mcp_tools", None) or []
                if skill_required:
                    missing = self.adapter.missing_tools(skill_required)
                    if missing:
                        raise ValueError(
                            "Skill 所需工具在 MCP server 缺失，无法评测："
                            f"缺少 {', '.join(missing)}（skill 声明 {skill_required}，server 仅提供 "
                            f"{[t.name for t in self.adapter.list_tools()]}）。"
                        )

            test_context = TestContext(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                skill=(
                    SkillContext(
                        id=skill.id,
                        name=skill.name,
                        type=skill.type,
                        version=skill.version,
                        loaded=True,
                        description=skill.description,
                        category=skill.category,
                        entry_file=skill.entry_file,
                        base_dir=skill.base_dir,
                        base_prompt=skill.base_prompt,
                        metadata=skill.metadata,
                        references=[reference.model_dump() for reference in skill.references],
                        assumptions=skill.assumptions,
                        lazy_load_references=skill.lazy_load_references,
                        recommended_mcp_tools=skill.recommended_mcp_tools,
                    )
                    if skill is not None
                    else None
                ),
                datasets=datasets,
                reference_datasets=reference_datasets,
                mcp_tools={
                    tool.name: MCPToolContext(server=tool.server, available=True, optional=tool.optional)
                    for tool in self.adapter.list_tools()
                },
            )

            runtime_executor = (run_config or {}).get("executor", scenario.runtime.executor)
            memory_enabled = bool((run_config or {}).get("memory_enabled", scenario.runtime.memory_enabled))
            executor = ExecutorFactory.create(runtime_executor, self.adapter)
            test_context_dump = test_context.model_dump()
            test_context_dump["_recorder"] = recorder
            test_context_dump["_loaded_reference_calls"] = []
            session_request = ExecutorSessionRequest(
                scenario_id=scenario.id,
                skill_id=skill.id if skill else None,
                skill_prompt=self.skill_loader.render_prompt(skill) if skill else None,
                test_context=test_context_dump,
                tools=[tool.model_dump() if hasattr(tool, "model_dump") else tool for tool in scenario.mcp.tools.required + scenario.mcp.tools.optional],
                agent=scenario.agent.model_dump() if scenario.agent else None,
                role_model_config={
                    "model": scenario.runtime.agent_model,
                    "executor": runtime_executor,
                    # 反问闭环下沉：模拟用户设定（agent.user_*）扁平注入，executor 统一从这里读
                    "user": _user_config(scenario.agent),
                },
                max_turns=scenario.runtime.max_turns,
                timeout_seconds=scenario.runtime.timeout_seconds,
                memory_enabled=memory_enabled,
            )
            session = executor.create_session(session_request)
            emit(
                "executor_session",
                stage="RUN_AGENT",
                status="RUNNING",
                stage_results=dict(stage_results),
                executor=runtime_executor,
                session_id=session.session_id,
                runtime_mode=session.runtime_mode,
                runtime_metadata=session.runtime_metadata,
                memory_enabled=memory_enabled,
            )

            stage_results["RUN_AGENT"] = "RUNNING"
            emit("stage", stage="RUN_AGENT", status="RUNNING", stage_results=dict(stage_results))
            conversation: list[dict[str, Any]] = []
            tool_calls: list[Any] = []
            output_artifacts: dict[str, Any] = {}
            final_response = ""
            run_failed = False
            agent_finished = False
            current_message = scenario.user_task
            turn_limit = scenario.runtime.max_turns

            for turn_index in range(turn_limit):
                step_result = executor.send_message(session.session_id, current_message)
                tool_calls.extend(step_result.tool_calls)
                output_artifacts.update(step_result.artifacts)
                final_response = step_result.response or final_response
                if step_result.conversation:
                    # 反问闭环下沉后，executor 内部跑完整多轮并返回完整对话（含模拟用户回答）
                    conversation = step_result.conversation
                elif not conversation:
                    # 老 executor（http_agent/nanobot）不返回对话：自拼一问一答
                    conversation.append({"role": "user", "content": current_message})
                    conversation.append({"role": "assistant", "content": step_result.response})
                emit(
                    "executor_step",
                    stage="RUN_AGENT",
                    status="RUNNING",
                    stage_results=dict(stage_results),
                    turn_index=turn_index,
                    need_interaction=step_result.need_interaction,
                    finished=step_result.finished,
                    response=step_result.response,
                    tool_call_count=len(step_result.tool_calls),
                )

                if step_result.error_message:
                    run_failed = True
                    execution_status = "failed"
                    termination_reason = "agent_error"
                    _failure(
                        failures,
                        phase="RUN_AGENT",
                        code="agent_error",
                        message=step_result.error_message,
                        source="sut",
                    )
                    recorder.record_error(step_result.error_message)
                    break

                if step_result.finished:
                    agent_finished = True
                    break

                # 兼容：老 executor 返回 need_interaction 但反问闭环已下沉到 executor 内部，
                # runner 不再有 actor 循环 → 直接终止（保留 need_interaction 信号给前端展示）
                break

            executor.close_session(session.session_id)
            runtime_mode = session.runtime_mode
            runtime_metadata = session.runtime_metadata
            session = None
            recorder.record_conversation(conversation)
            recorder.record_tool_calls(tool_calls)
            final_datasets = self.adapter.get_dataset_store()
            recorder.record_final_output(
                {
                    "final_response": final_response,
                    "datasets": final_datasets,
                    "output_artifacts": output_artifacts,
                    "external_interactions": recorder.external_interactions,
                }
            )
            stage_results["RUN_AGENT"] = "FAILED" if run_failed or not final_response or not agent_finished else "PASSED"
            if not run_failed and not final_response:
                execution_status = "failed"
                termination_reason = "empty_response"
                _failure(failures, phase="RUN_AGENT", code="empty_response", message="Executor returned an empty response.", source="sut")
            elif not run_failed and not agent_finished:
                execution_status = "failed"
                termination_reason = "unfinished"
                _failure(failures, phase="RUN_AGENT", code="unfinished", message="Executor did not finish the run.", source="sut")
            emit(
                "agent_result",
                stage="RUN_AGENT",
                status=stage_results["RUN_AGENT"],
                stage_results=dict(stage_results),
                executor=runtime_executor,
                final_response=final_response,
                tool_call_count=len(tool_calls),
            )

            stage_results["RUN_ASSERTIONS"] = "RUNNING"
            emit("stage", stage="RUN_ASSERTIONS", status="RUNNING", stage_results=dict(stage_results))
            assertion_result = self.assertion_engine.run(scenario.assertions, recorder, test_context)
            stage_results["RUN_ASSERTIONS"] = "PASSED" if assertion_result.passed else "FAILED"
            emit(
                "assertions",
                stage="RUN_ASSERTIONS",
                status=stage_results["RUN_ASSERTIONS"],
                stage_results=dict(stage_results),
                passed=assertion_result.passed,
                score=assertion_result.score,
            )

            stage_results["RUN_JUDGE"] = "RUNNING"
            emit("stage", stage="RUN_JUDGE", status="RUNNING", stage_results=dict(stage_results))
            judge_result = self.judge_engine.evaluate(scenario, test_context, recorder, assertion_result)
            stage_results["RUN_JUDGE"] = "PASSED" if judge_result.passed else "FAILED"
            emit(
                "judge",
                stage="RUN_JUDGE",
                status=stage_results["RUN_JUDGE"],
                stage_results=dict(stage_results),
                passed=judge_result.passed,
                score=judge_result.score,
                judge_mode=judge_result.judge_mode,
                model=judge_result.model,
            )

            stage_results["CLEANUP"] = "RUNNING"
            emit("stage", stage="CLEANUP", status="RUNNING", stage_results=dict(stage_results))
            self.fixture_manager.cleanup(test_context)
            self.adapter.close()
            if data_service is not None and registration is not None:
                try:
                    data_service.release_run(run_id)
                except DataServiceError as release_error:
                    _failure(failures, phase="CLEANUP", code="release_error", message=str(release_error), source="cleanup")
            stage_results["CLEANUP"] = "PASSED"

            stage_results["GENERATE_REPORT"] = "RUNNING"
            emit("stage", stage="GENERATE_REPORT", status="RUNNING", stage_results=dict(stage_results))
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            evaluation_verdict = _evaluation_verdict(
                execution_ok=stage_results["RUN_AGENT"] == "PASSED",
                assertion_result=assertion_result,
                judge_result=judge_result,
            )
            result = TestResult(
                run_id=run_id,
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                status="failed",
                duration_ms=duration_ms,
                stage_results=dict(stage_results),
                skill=test_context.skill.model_dump() if test_context.skill else None,
                tool_calls=[call.model_dump() for call in recorder.tool_calls],
                assertions=[item.model_dump() for item in assertion_result.items],
                judge=judge_result.model_dump(),
                conversation=recorder.conversation,
                final_output={
                    "final_response": recorder.final_output["final_response"],
                    "executor": runtime_executor,
                    "runtime_mode": runtime_mode,
                    "runtime_metadata": runtime_metadata,
                    "output_artifacts": output_artifacts,
                    "external_interactions": recorder.final_output.get("external_interactions", []),
                },
                loaded_skill_references=recorder.loaded_skill_references,
                errors=recorder.errors,
                operational_status=execution_status if execution_status != "running" else "succeeded",
                evaluation_verdict=evaluation_verdict,
                termination_reason=termination_reason,
                archive_status="pending",
                cleanup_status="succeeded",
                failures=failures,
            )
            result.archive_status = "succeeded" if output_dir else "not_attempted"
            result.status = _top_level_status(
                result.evaluation_verdict,
                result.operational_status,
                result.archive_status,
                result.cleanup_status,
                result.failures,
            )
            if output_dir:
                try:
                    self.report_generator.write_reports(output_dir, result)
                except Exception as exc:
                    # 归档失败不改写 SUT 评测结论，仅把顶层状态拉为 failed
                    result.archive_status = "failed"
                    _failure(result.failures, phase="GENERATE_REPORT", code="archive_error", message=str(exc), source="archive")
                    result.errors.append(f"Archive failed: {exc}")
                    result.status = "failed"
            stage_results["GENERATE_REPORT"] = "PASSED" if result.archive_status != "failed" else "FAILED"
            result.stage_results = dict(stage_results)
            emit("stage", stage="GENERATE_REPORT", status="PASSED", stage_results=dict(stage_results))
            emit("result", stage="GENERATE_REPORT", status=result.status, stage_results=dict(stage_results), result=result.model_dump())
            return result
        except Exception as exc:
            recorder.record_error(str(exc))
            failed_stage = next((stage for stage, status in stage_results.items() if status == "RUNNING"), "RUN_AGENT")
            stage_results[failed_stage] = "FAILED"
            emit("error", stage=failed_stage, status="FAILED", stage_results=dict(stage_results), message=str(exc))
            stage_results["CLEANUP"] = "RUNNING"
            emit("stage", stage="CLEANUP", status="RUNNING", stage_results=dict(stage_results))
            if session is not None and executor is not None:
                try:
                    executor.close_session(session.session_id)
                except Exception:
                    pass  # 异常路径的关闭失败不掩盖原始错误
            self.fixture_manager.cleanup(test_context)
            self.adapter.close()
            if data_service is not None and registration is not None:
                try:
                    data_service.release_run(run_id)
                except DataServiceError as release_error:
                    _failure(failures, phase="CLEANUP", code="release_error", message=str(release_error), source="cleanup")
            stage_results["CLEANUP"] = "PASSED"
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            if scenario is None:
                scenario_id = Path(scenario_path).stem
                scenario_name = scenario_id
                skill_info = {"id": "unknown", "version": "unknown", "loaded": False}
            else:
                scenario_id = scenario.id
                scenario_name = scenario.name
                skill_info = {"id": scenario.target.skill_id or "unknown", "version": scenario.target.skill_version, "loaded": False}
            result = TestResult(
                run_id=run_id,
                scenario_id=scenario_id,
                scenario_name=scenario_name,
                status="failed",
                duration_ms=duration_ms,
                stage_results=stage_results,
                skill=skill_info,
                tool_calls=[call.model_dump() for call in recorder.tool_calls],
                assertions=[],
                judge={"score": 0.0, "passed": False, "reason": "Runner failed before judge execution.", "status": "failed"},
                conversation=recorder.conversation,
                final_output=recorder.final_output,
                loaded_skill_references=recorder.loaded_skill_references,
                errors=recorder.errors,
                operational_status="failed",
                evaluation_verdict="not_evaluable",
                termination_reason="runner_error",
                archive_status="not_attempted",
                cleanup_status="succeeded",
                failures=[FailureRecord(phase=failed_stage, code="runner_error", message=str(exc), source="platform")],
            )
            emit("result", stage="CLEANUP", status="failed", stage_results=dict(stage_results), result=result.model_dump())
            return result
