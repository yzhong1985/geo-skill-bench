from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from geoskillbench.executors.base import Executor
from geoskillbench.mcp.mcp_tool_adapter import MCPToolAdapter
from geoskillbench.models.result import ExecutorSession, ExecutorSessionRequest, ExecutorStepResult
from geoskillbench.models.result import ToolCallRecord
from geoskillbench.models.skill import AgentSkill
from geoskillbench.runtime.user_simulator import UserSimulator
from geoskillbench.skills.reference_tool import SkillReferenceTool


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class HeuristicSessionState:
    request: ExecutorSessionRequest
    skill: AgentSkill
    reference_tool: SkillReferenceTool | None = None
    conversation: list[dict[str, str]] = field(default_factory=list)
    resolved_dataset: str | None = None
    resolved_distance: float | None = None
    latest_metadata: dict[str, Any] | None = None
    finished: bool = False
    final_response: str = ""
    # 反问闭环（下沉）：缺数据集/距离时问模拟用户，解析其回答填入 resolved_*，不再返回 need_interaction
    user_simulator: UserSimulator | None = None


class HeuristicSessionExecutor(Executor):
    executor_type = "skill"

    def __init__(self, adapter: MCPToolAdapter, executor_type: str = "skill", compatibility_note: str | None = None) -> None:
        self.adapter = adapter
        self.executor_type = executor_type
        self.compatibility_note = compatibility_note
        self.sessions: dict[str, HeuristicSessionState] = {}

    def create_session(self, request: ExecutorSessionRequest) -> ExecutorSession:
        session_id = uuid4().hex
        skill = AgentSkill.model_validate(request.test_context["skill"])
        reference_tool = SkillReferenceTool(skill, request.test_context["_recorder"]) if skill.type == "prompt_skill_package" else None
        # 反问闭环：skill 降级到启发式时也内置模拟用户，缺信息内部闭环，不依赖 runner
        user_cfg = request.role_model_config.get("user") or {}
        user_simulator = None
        if user_cfg.get("user_enabled", True):
            user_simulator = UserSimulator(
                goal=str(user_cfg.get("user_goal") or ""),
                profile=str(user_cfg.get("user_profile") or "normal_user"),
                model=str(user_cfg.get("user_model") or "rule-based-user"),
                datasets=request.test_context.get("datasets", {}),
            )
        self.sessions[session_id] = HeuristicSessionState(
            request=request,
            skill=skill,
            reference_tool=reference_tool,
            user_simulator=user_simulator,
        )
        return ExecutorSession(
            session_id=session_id,
            executor_type=self.executor_type,
            scenario_id=request.scenario_id,
            skill_id=request.skill_id,
            created_at=now_iso(),
        )

    def send_message(self, session_id: str, message: str) -> ExecutorStepResult:
        state = self.sessions[session_id]
        state.conversation.append({"role": "user", "content": message})
        internal_calls: list[ToolCallRecord] = []

        datasets = state.request.test_context.get("datasets", {})
        if state.resolved_dataset is None:
            state.resolved_dataset = self._infer_dataset_alias(message, datasets)
            if state.resolved_dataset is None and state.user_simulator is not None:
                # 反问闭环：问模拟用户并解析回答；解析不到再取第一个数据集兜底
                state.conversation.append({"role": "assistant", "content": "[NEED_INTERACTION]\n请确认要使用哪个数据集。"})
                reply = state.user_simulator.reply("请确认要使用哪个数据集。")
                state.conversation.append({"role": "user", "content": reply})
                state.resolved_dataset = self._infer_dataset_alias(reply, datasets)
                if state.resolved_dataset is None and datasets:
                    state.resolved_dataset = next(iter(datasets.keys()))
            if state.resolved_dataset is None:
                response = "[NEED_INTERACTION]\n请确认要使用哪个数据集。"
                state.conversation.append({"role": "assistant", "content": response})
                return ExecutorStepResult(
                    response=response,
                    need_interaction=True,
                    finished=False,
                    conversation=list(state.conversation),
                )

        if state.resolved_distance is None:
            state.resolved_distance = self._infer_distance(message)
            if state.resolved_distance is None and state.user_simulator is not None:
                # 反问闭环：问模拟用户并解析"多少米"；规则回答必有数字，100% 可解析
                state.conversation.append({"role": "assistant", "content": "[NEED_INTERACTION]\n请确认缓冲距离是多少米。"})
                reply = state.user_simulator.reply("请确认缓冲距离是多少米。")
                state.conversation.append({"role": "user", "content": reply})
                state.resolved_distance = self._infer_distance(reply)
            if state.resolved_distance is None:
                response = "[NEED_INTERACTION]\n请确认缓冲距离是多少米。"
                state.conversation.append({"role": "assistant", "content": response})
                return ExecutorStepResult(
                    response=response,
                    need_interaction=True,
                    finished=False,
                    conversation=list(state.conversation),
                )

        available_tools = state.request.test_context.get("mcp_tools", {})
        tool_calls = list(internal_calls)
        tool_calls.extend(self._load_required_references(state, ["plan", "data", "metadata", "buffer", "result", "output"]))

        source_dataset = self._source_dataset_name(state)
        if "createBuffer" in available_tools:
            buffer_call = self.adapter.invoke(
                "createBuffer",
                {
                    "sourceDataset": source_dataset,
                    "bufferDistance": str(int(state.resolved_distance) if float(state.resolved_distance).is_integer() else state.resolved_distance),
                    "bufferRadiusUnit": "米",
                    "asyncExecution": False,
                },
            )
        else:
            buffer_call = self.adapter.invoke(
                "create_buffer",
                {
                    "dataset": source_dataset,
                    "distance": state.resolved_distance,
                    "distance_unit": "meter",
                    "output_alias": "buffer_result",
                },
            )
        tool_calls.append(buffer_call)
        if buffer_call.status != "success" or (buffer_call.result or {}).get("success") is False:
            error_message = (
                (buffer_call.result or {}).get("error")
                or buffer_call.error_message
                or "unknown error"
            )
            response = f"缓冲区分析失败：{error_message}"
            state.conversation.append({"role": "assistant", "content": response})
            return ExecutorStepResult(
                response=response,
                finished=True,
                tool_calls=tool_calls,
                error_message=error_message,
                conversation=list(state.conversation),
            )

        handle = (buffer_call.result or {}).get("handle") or (buffer_call.result or {}).get("bufferResult")
        if not handle:
            error_message = (buffer_call.result or {}).get("note") or "createBuffer did not return a result handle"
            response = f"缓冲区分析失败：{error_message}"
            state.conversation.append({"role": "assistant", "content": response})
            return ExecutorStepResult(
                response=response,
                finished=True,
                tool_calls=tool_calls,
                error_message=error_message,
                conversation=list(state.conversation),
            )
        prefix = "[FINAL]\n"
        response = (
            f"{prefix}已完成 {state.resolved_dataset} 数据的 {state.resolved_distance:g} 米缓冲区分析。"
            f" 结果数据句柄为 {handle}。"
        )
        artifacts = {"result_dataset": buffer_call.result}
        if self.compatibility_note:
            artifacts["executor_note"] = self.compatibility_note
        state.final_response = response
        state.finished = True
        state.conversation.append({"role": "assistant", "content": response})
        return ExecutorStepResult(
            response=response,
            need_interaction=False,
            finished=True,
            tool_calls=tool_calls,
            artifacts=artifacts,
            conversation=list(state.conversation),
        )

    def close_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)

    def _source_dataset_name(self, state: HeuristicSessionState) -> str:
        datasets = state.request.test_context.get("datasets", {})
        dataset = datasets.get(state.resolved_dataset) or {}
        if isinstance(dataset, dict):
            logical_id = (dataset.get("metadata") or {}).get("logical_id")
            if logical_id:
                return str(logical_id)
            source_alias = dataset.get("source_alias")
            if source_alias:
                return str(source_alias)
            name = dataset.get("name")
            if name:
                return str(name)
        return str(state.resolved_dataset)

    def _infer_dataset_alias(self, text: str, datasets: dict[str, Any]) -> str | None:
        lowered = text.lower()
        for alias in datasets:
            if alias.lower() in lowered:
                return alias
        if len(datasets) == 1:
            return next(iter(datasets.keys()))
        return None

    def _infer_distance(self, text: str) -> float | None:
        match = re.search(r"(\d+(?:\.\d+)?)\s*米", text)
        if match:
            return float(match.group(1))
        return None

    def _load_required_references(self, state: HeuristicSessionState, tags_or_keywords: list[str]) -> list[ToolCallRecord]:
        if state.reference_tool is None or not state.skill.lazy_load_references:
            return []
        loaded_paths = {call.arguments.get("path") for call in state.request.test_context["_loaded_reference_calls"]}
        selected: list[ToolCallRecord] = []
        for reference in state.skill.references:
            if reference.path in loaded_paths:
                continue
            haystack = " ".join([reference.title, reference.path, *reference.tags, *reference.trigger_keywords]).lower()
            if reference.required or any(token.lower() in haystack for token in tags_or_keywords):
                content = state.reference_tool.load_skill_reference(reference.path)
                record = ToolCallRecord(
                    tool_name="load_skill_reference",
                    arguments={"path": reference.path},
                    result={"path": reference.path, "title": reference.title, "excerpt": content[:160]},
                    status="success",
                    tool_type="skill_internal",
                )
                state.request.test_context["_loaded_reference_calls"].append(record)
                selected.append(record)
        return selected
