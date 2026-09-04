from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SkillContext(BaseModel):
    id: str
    name: str
    type: str = "prompt_skill"
    version: str
    loaded: bool = False
    description: str = ""
    category: str | None = None
    entry_file: str | None = None
    base_dir: str | None = None
    base_prompt: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    references: list[dict[str, Any]] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    lazy_load_references: bool = False
    recommended_mcp_tools: list[str] = Field(default_factory=list)  # skill 推荐工具（executor 用它过滤暴露给 agent 的工具）


class DatasetContext(BaseModel):
    handle: str
    name: str
    role: str = "input"
    run_id: str | None = None
    geometry_type: str | None = None
    crs: str | None = None
    feature_count: int | None = None
    fields: list[str] = Field(default_factory=list)
    # 仅兼容历史本地 fixture；服务化 5B descriptor 不应设置 path。
    path: str | None = None
    semantic_desc: str | None = None
    source_alias: str | None = None
    expires_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MCPToolContext(BaseModel):
    server: str
    available: bool = True
    optional: bool = False


class TestContext(BaseModel):
    scenario_id: str
    scenario_name: str
    skill: SkillContext | None = None
    datasets: dict[str, DatasetContext] = Field(default_factory=dict)
    # 参考数据集（data.reference）：仅断言引擎比对时读取，不暴露给被测 agent（不进提示词/工具解析）
    reference_datasets: dict[str, DatasetContext] = Field(default_factory=dict)
    mcp_tools: dict[str, MCPToolContext] = Field(default_factory=dict)
