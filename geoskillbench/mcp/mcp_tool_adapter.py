"""MCP 工具适配器（迭代 3：真 MCP 客户端）。

把「假 MCP + 硬编码工具实现」改造为「真 MCP 客户端」：
- 工具实现（geopandas 几何运算）已搬入 `mock_gis_server.py`，作为本地 stdio MCP server 进程。
- adapter 只负责连 server（当前仅 stdio/mock）、`tools/list` 自动发现工具、`invoke` 走 `tools/call` 协议。
- mock 与未来云端 server 走同一条 code path（transport 区分 stdio/sse，云端后续接入）。

同步/异步桥接：MCP SDK 的 ClientSession 是 async，而 runner/executor 是同步调用链。
adapter 内部持有一个后台 event loop 线程，同步方法用 ``run_coroutine_threadsafe`` 桥接，
对外接口（connect_servers / register_datasets / list_tools / get_agent_tools /
validate_required_tools / invoke / get_dataset_store / set_output_dir）保持不变。
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from geoskillbench.models.result import ToolCallRecord
from geoskillbench.models.scenario import MCPServerConfig, ToolRef
from geoskillbench.models.test_context import DatasetContext


@dataclass
class ToolDefinition:
    server: str
    name: str
    optional: bool = False
    input_schema: dict[str, Any] | None = None


def schema_to_pydantic_model(
    model_name: str, schema: dict[str, Any]
) -> "type":
    """把 MCP 工具 inputSchema（JSON Schema）转成 pydantic 模型，供 StructuredTool 生成参数。

    - 顶层必须有 properties（对象）。缺省模型（空参数）返回 None，调用方用无参函数。
    - 只映射 string/number/integer/boolean/array/object；未知类型按 string 兜底（宽松，避免崩）。
    - required 里缺默认值 → 必填；有默认值或不在 required → 选填。
    """
    props = schema.get("properties") or {}
    if not props:
        return None
    from pydantic import Field, create_model

    _TYPE_MAP = {
        "string": str,
        "number": float,
        "integer": int,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    required = set(schema.get("required") or [])
    fields: dict[str, Any] = {}
    for name, prop in props.items():
        py_type = _TYPE_MAP.get((prop or {}).get("type", "string"), str)
        has_default = "default" in (prop or {})
        if name in required and not has_default:
            fields[name] = (py_type, Field(description=(prop or {}).get("description", "")))
        else:
            default = (prop or {}).get("default", None)
            fields[name] = (
                py_type,
                Field(default=default, description=(prop or {}).get("description", "")),
            )
    return create_model(model_name, **fields)


class _MCPConnection:
    """一条到 MCP server 的连接（async 客户端，由 adapter 的后台 loop 线程驱动）。"""

    def __init__(self, server_id: str, server: MCPServerConfig) -> None:
        self.server_id = server_id
        self.server = server
        self._stack: AsyncExitStack | None = None
        self.session: Any | None = None
        self.tools: list[Any] = []

    async def connect(self) -> list[Any]:
        transport = self.server.transport.lower()
        self._stack = AsyncExitStack()
        headers: dict[str, str] = {}
        if self.server.credential_env:
            token = os.environ.get(self.server.credential_env, "")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        if transport == "sse":
            from mcp import ClientSession
            from mcp.client.sse import sse_client

            read, write = await self._stack.enter_async_context(
                sse_client(self.server.url, headers=headers or None)
            )
        elif transport == "http":
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            read, write, _ = await self._stack.enter_async_context(
                streamable_http_client(
                    self.server.url,
                    http_client=httpx.AsyncClient(headers=headers or None),
                )
            )
        else:  # model validation normally catches this; keep a defensive boundary for direct callers.
            raise ValueError(f"Unsupported MCP transport: {transport}")
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        self.tools = (await self.session.list_tools()).tools
        return self.tools

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self.session.call_tool(tool_name, arguments or {})
        text = ""
        for block in result.content or []:
            text += getattr(block, "text", "") or ""
        if result.isError:
            raise RuntimeError(text.strip() or f"Tool {tool_name} failed on server.")
        try:
            return json.loads(text) if text.strip() else {}
        except json.JSONDecodeError:
            return {"result": text}

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None


class MCPToolAdapter:
    def __init__(self) -> None:
        self._servers: dict[str, MCPServerConfig] = {}
        self._catalog: dict[str, ToolDefinition] = {}
        self._dataset_store: dict[str, DatasetContext] = {}
        self._result_locations: dict[str, dict[str, str]] = {}
        self._run_id: str | None = None
        self._conns: dict[str, _MCPConnection] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        atexit.register(self.close)

    # ---------- 同步/异步桥接 ----------

    def _ensure_loop(self) -> None:
        if self._loop is None:
            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=loop.run_forever, name="mcp-adapter-loop", daemon=True)
            thread.start()
            self._loop = loop
            self._loop_thread = thread

    def _run_async(self, coro: Any, timeout: float = 120.0) -> Any:
        self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # ---------- 配置与连接 ----------

    def set_output_dir(self, path: str | Path | None) -> None:
        """保留旧 API 以兼容调用方；服务化结果不在 client 端落盘。"""
        return None

    def connect_servers(self, servers: list[MCPServerConfig]) -> None:
        """连接所有网络 server，tools/list 自动发现工具。"""
        self._servers = {server.id: server for server in servers}
        self._catalog.clear()
        for server in servers:
            conn = _MCPConnection(server.id, server)
            tools = self._run_async(conn.connect())
            with self._lock:
                for tool in tools:
                    self._catalog.setdefault(
                        tool.name,
                        ToolDefinition(
                            server=server.id,
                            name=tool.name,
                            input_schema=dict(tool.inputSchema or {}),
                        ),
                    )
            self._conns[server.id] = conn

    def register_datasets(self, datasets: dict[str, DatasetContext], run_id: str | None = None) -> None:
        self._dataset_store = dict(datasets)
        self._run_id = run_id or next((item.run_id for item in datasets.values() if item.run_id), None)

    # ---------- 工具清单与校验 ----------

    def list_tools(self) -> list[ToolDefinition]:
        with self._lock:
            return list(self._catalog.values())

    def validate_required_tools(self, required_tools: list[ToolRef]) -> None:
        with self._lock:
            # mock/stdio 下工具身份以 name 为准（server 只是归属标签）；缺失按 name 判定。
            # 云端跨 server 同名工具后续接入时再收紧为 (server, name) 精确匹配。
            missing = [tool.name for tool in required_tools if tool.name not in self._catalog]
        if missing:
            missing_text = ", ".join(missing)
            raise ValueError(f"Required MCP tools are missing: {missing_text}")

    def missing_tools(self, tool_names: list[str]) -> list[str]:
        """返回已发现工具中不存在的工具名（供 skill 所需工具 fail-fast 校验用）。"""
        with self._lock:
            return [name for name in tool_names if name not in self._catalog]

    def get_agent_tools(self, required_tools: list[ToolRef], optional_tools: list[ToolRef]) -> dict[str, Callable]:
        """返回已发现工具名 → 可调用对象（arg dict → 结果 dict）。工具来自 server 自动发现。"""
        with self._lock:
            names = list(self._catalog.keys())
        tools: dict[str, Callable] = {}
        for name in names:
            def _make(tool_name: str) -> Callable:
                def callable(arguments: dict[str, Any]) -> dict[str, Any]:
                    record = self.invoke(tool_name, arguments)
                    if record.status != "success":
                        raise RuntimeError(record.error_message or f"Tool {tool_name} failed.")
                    return record.result or {}
                return callable
            tools[name] = _make(name)
        return tools

    def get_dataset_store(self) -> dict[str, DatasetContext]:
        return self._dataset_store

    def get_result_location(self, alias: str) -> dict[str, str] | None:
        """评测引擎读取真实结果用；不进入 Agent 可见结果或报告。"""
        return self._result_locations.get(alias)

    def register_result_payload(self, result: dict[str, Any]) -> dict[str, Any]:
        """登记工具产出（MCP 或外部 agent tool_event）。非 GIS 结果原样返回。"""
        if not isinstance(result, dict):
            return result
        try:
            return self._register_generated_dataset(result)
        except ValueError:
            return result

    # ---------- 工具调用 ----------

    def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolCallRecord:
        with self._lock:
            defn = self._catalog.get(tool_name)
        if defn is None:
            return ToolCallRecord(
                tool_name=tool_name,
                arguments=arguments,
                result=None,
                status="failed",
                error_message=f"No implementation for tool {tool_name}",
            )
        conn = self._conns.get(defn.server)
        if conn is None:
            return ToolCallRecord(
                tool_name=tool_name,
                arguments=arguments,
                result=None,
                status="failed",
                error_message=f"Server '{defn.server}' not connected",
            )
        try:
            result = self._run_async(conn.call(tool_name, arguments or {}))
            public_result = self._register_generated_dataset(result)
            return ToolCallRecord(tool_name=tool_name, arguments=arguments, result=public_result, status="success")
        except Exception as exc:
            return ToolCallRecord(
                tool_name=tool_name,
                arguments=arguments,
                result=None,
                status="failed",
                error_message=str(exc),
            )

    def close(self) -> None:
        conns = list(self._conns.values())
        self._conns.clear()
        self._catalog.clear()
        self._dataset_store.clear()
        self._result_locations.clear()
        self._servers.clear()
        self._run_id = None
        if not conns or self._loop is None:
            return
        try:
            async def _close_all() -> None:
                for conn in conns:
                    try:
                        await conn.close()
                    except Exception:
                        pass
            asyncio.run_coroutine_threadsafe(_close_all(), self._loop).result(timeout=10)
        except Exception:
            pass

    # ---------- 生成数据集登记 ----------

    def _register_generated_dataset(self, result: dict[str, Any]) -> dict[str, Any]:
        """登记服务端结果。对外只返回不透明 handle，物理表名/URL 仅内部保留。"""
        if not isinstance(result, dict):
            return result

        if result.get("success") is True and not (result.get("bufferResult") or result.get("tableName") or result.get("handle")):
            raise ValueError(result.get("note") or "MCP reported success without a result dataset")
        is_supermap = result.get("success") is True and ("bufferResult" in result or "tableName" in result)
        if is_supermap:
            raw_handle = str(result.get("bufferResult") or result.get("tableName") or "")
            alias = "buffer_result"
            run_id = self._run_id or "run"
            safe_handle = f"dh_{run_id}_{alias}"
            self._result_locations[alias] = {
                "tableName": str(result.get("tableName") or ""),
                "svc_url": str(result.get("bufferResultSvcURL") or ""),
                "bufferResult": raw_handle,
            }
            self._dataset_store[alias] = DatasetContext(
                handle=safe_handle,
                name=alias,
                role="result",
                run_id=run_id,
                source_alias=alias,
            )
            return {
                "success": True,
                "handle": safe_handle,
                "dataset": alias,
                "alias": alias,
                "role": "result",
                "run_id": run_id,
            }

        descriptor = result.get("dataset") if isinstance(result.get("dataset"), dict) else result
        alias = descriptor.get("alias") or result.get("dataset")
        handle = descriptor.get("handle")
        if not (alias and handle):
            return result
        leaked = ("path", "table", "tableName", "schema", "server_side_path", "bufferResultSvcURL")
        if any(key in result or key in descriptor for key in leaked):
            raise ValueError("MCP dataset result contains a prohibited physical location field")
        run_id = descriptor.get("run_id")
        expected_run_id = self._run_id
        if expected_run_id and run_id != expected_run_id:
            raise ValueError("MCP dataset result belongs to another run")
        role = descriptor.get("role", "result")
        if role != "result":
            return result
        self._dataset_store[str(alias)] = DatasetContext(
            handle=str(handle),
            name=str(alias),
            role="result",
            run_id=run_id,
            geometry_type=descriptor.get("geometry_type"),
            crs=descriptor.get("crs") or (f"EPSG:{descriptor['srid']}" if descriptor.get("srid") else None),
            feature_count=descriptor.get("feature_count"),
            fields=descriptor.get("fields") or [],
            source_alias=str(alias),
            expires_at=descriptor.get("expires_at"),
            metadata=descriptor.get("metadata") or {},
        )
        return {
            "success": True,
            "handle": str(handle),
            "dataset": str(alias),
            "alias": str(alias),
            "role": "result",
            "run_id": run_id,
            "geometry_type": descriptor.get("geometry_type"),
            "crs": descriptor.get("crs"),
            "feature_count": descriptor.get("feature_count"),
        }
