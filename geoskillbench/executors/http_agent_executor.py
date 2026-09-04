from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import httpx

from geoskillbench.executors.base import Executor
from geoskillbench.mcp.mcp_tool_adapter import MCPToolAdapter
from geoskillbench.models.result import ExecutorSession, ExecutorSessionRequest, ExecutorStepResult, ToolCallRecord


class HttpAgentExecutor(Executor):
    """把外部 HTTP 智能体包成平台 Executor 的黑盒适配器（契约见 docs/design/01-Agent接入契约.md）。

    契约要点：
    - 一问一答：每次 send_message 发一个 HTTP 请求，拿到完整响应即 finished。
    - need_interaction 恒为 False：外部 agent 不遵守平台 [NEED_INTERACTION]/[FINAL] 协议。
    - 多轮上下文通过 session_id 维持（每次响应更新，下次请求回传）。
    - 真实 Workflow Studio 会上报工具调用事件（SSE 的 tool_event），executor 解析为
      tool_calls 并写入报告；接口不上报工具调用时 tool_calls 为空、流程不受影响（黑盒兼容）。
    """

    executor_type = "http_agent"

    def __init__(self, adapter: MCPToolAdapter) -> None:
        self.adapter = adapter
        self.sessions: dict[str, dict[str, Any]] = {}

    def create_session(self, request: ExecutorSessionRequest) -> ExecutorSession:
        agent = request.agent or {}
        endpoint = agent.get("endpoint")
        if not endpoint:
            raise ValueError("agent 配置缺少 endpoint，无法创建 HTTP agent 会话")
        headers = dict(agent.get("headers") or {})
        api_key_env = agent.get("api_key_env")
        if api_key_env:
            api_key = os.environ.get(api_key_env)
            if api_key:
                headers.setdefault("Authorization", f"Bearer {api_key}")
        # 评测要求每次 run 都是"全新会话"：场景未显式配固定 session_id 时，
        # 生成随机 id 注入，避免外部 agent 服务端按 flow_id 复用上次会话/缓存，
        # 复读上一次的回答而污染评测结果。想测持久会话可显式配置 session_id。
        initial_session_id = agent.get("session_id") or str(uuid.uuid4())
        state = {
            "endpoint": endpoint,
            "query_params": dict(agent.get("query_params") or {}),
            "headers": headers,
            "body": dict(agent.get("body") or {}),
            "stream_response": bool(agent.get("stream_response", False)),
            "timeout_seconds": float(agent.get("timeout_seconds", 120)),
            "session_id": initial_session_id,
        }
        session_id = str(uuid.uuid4())
        self.sessions[session_id] = state
        return ExecutorSession(
            session_id=session_id,
            executor_type=self.executor_type,
            scenario_id=request.scenario_id,
            skill_id=request.skill_id,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            runtime_mode="compatibility",
            runtime_metadata={"agent_type": agent.get("type", "http")},
        )

    def send_message(self, session_id: str, message: str) -> ExecutorStepResult:
        state = self.sessions[session_id]
        body = dict(state["body"])
        body["input_value"] = message
        if state.get("session_id"):
            body["session_id"] = state["session_id"]
        try:
            response = httpx.post(
                state["endpoint"],
                params=state["query_params"],
                headers=state["headers"],
                json=body,
                timeout=state["timeout_seconds"],
            )
            response.raise_for_status()
            if state["stream_response"]:
                text, tool_calls = self._parse_sse(response)
            else:
                text, new_session_id = self._parse_json(response)
                tool_calls = []  # 非流式 JSON 响应未上报工具调用事件
                if new_session_id:
                    state["session_id"] = new_session_id
            return ExecutorStepResult(
                response=text,
                finished=True,
                need_interaction=False,
                tool_calls=tool_calls,
            )
        except Exception as exc:  # 网络/HTTP/解析错误统一转 error_message
            return ExecutorStepResult(response="", finished=False, need_interaction=False, error_message=str(exc))

    def close_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)

    # ---- 文本提取（契约文档第 4 节，待实测项集中在此两处修正）----

    def _parse_json(self, response: httpx.Response) -> tuple[str, str | None]:
        data = response.json()
        text = self._extract_text(data)
        session_id = data.get("session_id") if isinstance(data, dict) else None
        return text, session_id

    def _parse_sse(self, response: httpx.Response) -> tuple[str, list[ToolCallRecord]]:
        """聚合 SSE 流为最终回答文本 + 工具调用记录。

        兼容两种帧格式：
        1) 标准 SSE（mock 用）：`data: {...}` 行，遇 `[DONE]` 结束。
        2) 真实 Workflow Studio：每行是一个完整 JSON，
           `{"event": "token", "data": {"chunk": "..."}}`，
           最终回答由 `event: token` 的 `data.chunk` 逐块拼接（实测 2026-08）。
        工具调用来自 `event: tool_event`（真实接口上报）；不上报时返回空列表，
        不影响文本提取与整体流程。
        """
        chunks: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        pending: dict[str, dict[str, Any]] = {}  # run_id -> {tool_name, arguments}
        has_text = False
        fallback_message = ""  # add_message.text 兜底，仅当流里没有任何文本片段时采用
        for line in response.iter_lines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("data:"):
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                chunks.append(self._coerce_chunk(payload))
                has_text = True
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            event_type = event.get("event")
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            if event_type == "token":
                chunk = data.get("chunk")
                if isinstance(chunk, str):
                    has_text = True
                    chunks.append(chunk)
            elif event_type == "add_message":
                # 兜底：某些实现用 add_message.text 承载 AI 完整回答。只取 AI 消息
                # （避免拼入 User 输入回显），且只在无 token 流时采用，防止回答重复。
                sender = f"{data.get('sender')} {data.get('sender_name')}".lower()
                if "ai" in sender or "bot" in sender or "assistant" in sender:
                    text = data.get("text")
                    if isinstance(text, str) and text.strip() and not fallback_message:
                        fallback_message = text
            elif event_type == "tool_event":
                self._consume_tool_event(data, pending, tool_calls)
        if has_text:
            return "".join(chunks), tool_calls
        return fallback_message, tool_calls

    def _consume_tool_event(
        self,
        data: dict[str, Any],
        pending: dict[str, dict[str, Any]],
        calls: list[ToolCallRecord],
    ) -> None:
        """把真实接口的 tool_event 解析为 ToolCallRecord。

        - tool_start：记录工具名与入参（input），以 run_id 暂存。
        - tool_end：按 run_id 配对，输出（output，可能是 JSON 字符串）作为 result。
        - 不上报、字段缺失或格式异常时静默跳过，不抛异常、不破坏文本提取。
        """
        name = data.get("name")
        event_type = data.get("event_type")
        if not isinstance(name, str) or not name or event_type not in ("tool_start", "tool_end"):
            return
        run_id = str(data.get("run_id") or name)
        if event_type == "tool_start":
            arguments = data.get("input")
            pending[run_id] = {
                "tool_name": name,
                "arguments": arguments if isinstance(arguments, dict) else {},
            }
        elif event_type == "tool_end":
            record = pending.pop(run_id, None)
            tool_name = record["tool_name"] if record else name
            arguments = record["arguments"] if record else {}
            output = data.get("output")
            if isinstance(output, str):
                try:
                    parsed = json.loads(output)
                    if isinstance(parsed, dict):
                        output = parsed
                except json.JSONDecodeError:
                    pass
            public = output if isinstance(output, dict) else None
            if public is not None:
                public = self.adapter.register_result_payload(public)
            calls.append(
                ToolCallRecord(
                    tool_name=tool_name,
                    arguments=arguments,
                    result=public,
                    status="success",
                )
            )

    def _coerce_chunk(self, payload: str) -> str:
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            return payload
        return self._extract_text(obj)

    def _extract_text(self, obj: Any) -> str:
        """从响应 JSON 提取最终回答文本。

        覆盖两种已知结构：
        - mock：outputs[0].text
        - 真实 Workflow Studio（实测 2026-08）：outputs[0].outputs[0].results.message.data.text
        优先走明确路径，最后才受限遍历（跳过 inputs 等元数据，避免误提取用户输入）。
        """
        if isinstance(obj, str):
            return obj
        if isinstance(obj, list):
            return "".join(self._extract_text(item) for item in obj)
        if isinstance(obj, dict):
            results = obj.get("results")
            if isinstance(results, dict):
                extracted = self._extract_text(results)
                if extracted:
                    return extracted
            for key in ("text", "message", "content"):
                if isinstance(obj.get(key), str):
                    return obj[key]
            data = obj.get("data")
            if isinstance(data, dict) and isinstance(data.get("text"), str):
                return data["text"]
            outputs = obj.get("outputs")
            if isinstance(outputs, (list, dict)):
                return self._extract_text(outputs)
            # 受限遍历兜底：跳过元数据/输入回显，避免提取到用户输入或调试字段
            skipped = {
                "inputs", "session_id", "context_id", "timestamp", "sender",
                "sender_name", "id", "run_id", "properties", "files", "edit",
                "error", "category", "event_type", "event",
            }
            for key, value in obj.items():
                if key in skipped:
                    continue
                if isinstance(value, (dict, list)):
                    extracted = self._extract_text(value)
                    if extracted:
                        return extracted
        return ""
