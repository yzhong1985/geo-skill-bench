from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RuntimeConfig(BaseModel):
    executor: str = "skill"  # skill=本地技能评测（历史别名 langgraph）；orchestrator=指挥外部 agent；http_agent=透传
    agent_model: str = "rule-based-agent"
    judge_model: str = ""  # 空 = 跟随 agent_model（迭代 2 LLM judge）；配 rule-based-* 开头或别名缺失则显式降级规则判定
    max_turns: int = 6
    timeout_seconds: int = 180
    memory_enabled: bool = False


class FixtureConfig(BaseModel):
    # 5B 正式数据引用使用 catalog_id/evaluation_id；path/table 仅保留用于尚未迁移的历史配置。
    id: str
    name: str = ""
    type: str = "vector"
    format: str | None = None
    path: str | None = None
    catalog_id: str | None = None
    evaluation_id: str | None = None
    crs: str | None = None
    geometry_type: str | None = None
    import_as: str = "dataset"
    register_metadata: bool = True
    cleanup: bool = True
    db_url: str | None = None
    table: str | None = None
    db_schema: str | None = "public"

    @model_validator(mode="after")
    def _check_reference(self) -> "FixtureConfig":
        if not self.path and not self.table and not self.catalog_id and not self.evaluation_id:
            raise ValueError(f"fixture {self.id}: requires catalog_id/evaluation_id or legacy path/table")
        return self


class DataServiceConfig(BaseModel):
    """数据服务控制面配置；认证只允许环境变量引用。"""

    url: str
    credential_env: str | None = None
    timeout_seconds: float = 30.0

    @model_validator(mode="after")
    def _check_url_and_credential(self) -> "DataServiceConfig":
        if not self.url.startswith(("http://", "https://")):
            raise ValueError("data_service.url requires an http(s) URL")
        if self.credential_env and not self.credential_env.replace("_", "").isalnum():
            raise ValueError("data_service.credential_env must be an environment variable name")
        return self


class DataConfig(BaseModel):
    service: DataServiceConfig | None = None
    fixtures: list[FixtureConfig] = Field(default_factory=list)
    # 参考数据集（ground truth）：仅断言引擎比对时读取，不暴露给被测 agent（不进 adapter/提示词）。
    # 独立块是为了避免标准答案泄露——参考数据与输入数据同列表会被拼进 agent 可见数据集。
    reference: list[FixtureConfig] = Field(default_factory=list)


class MCPServerConfig(BaseModel):
    id: str
    name: str
    transport: Literal["sse", "http"]
    # 正式 5B 运行只接受网络 MCP；认证只能通过环境变量引用注入。
    url: str
    credential_env: str | None = None
    required: bool = True

    @model_validator(mode="after")
    def _check_network_endpoint(self) -> "MCPServerConfig":
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(f"MCP server {self.id}: transport={self.transport} requires an http(s) url")
        if self.credential_env and not self.credential_env.replace("_", "").isalnum():
            raise ValueError(f"MCP server {self.id}: credential_env must be an environment variable name")
        return self


class ToolRef(BaseModel):
    server: str
    name: str


class MCPToolsConfig(BaseModel):
    required: list[ToolRef] = Field(default_factory=list)
    optional: list[ToolRef] = Field(default_factory=list)


class MCPConfig(BaseModel):
    servers: list[MCPServerConfig] = Field(default_factory=list)
    tools: MCPToolsConfig = Field(default_factory=MCPToolsConfig)


class SkillConfig(BaseModel):
    load_mode: str = "file"
    path: str
    entry: str | None = None
    lazy_load_references: bool = False
    required: bool = True


class ExpectedBehavior(BaseModel):
    should_load_skills: list[str] = Field(default_factory=list)
    should_call_tools: list[str] = Field(default_factory=list)
    optional_tools: list[str] = Field(default_factory=list)
    should_not: list[str] = Field(default_factory=list)


class AssertionConfig(BaseModel):
    type: str
    tool: str | None = None
    skill_id: str | None = None
    path: str | None = None
    reference: str | None = None
    argument: str | None = None
    value: Any = None
    alias: str | None = None
    target: str | None = None
    values: list[Any] = Field(default_factory=list)
    sequence: list[str] = Field(default_factory=list)
    relation: str | None = None
    source: str | None = None
    field: str | None = None
    rule: str | None = None
    # 结果内容断言（result_*）阈值：min/max 通用；max_ratio=相对误差；max_meters=偏移米数；mode=字段匹配(exact/contains)；count=要素数
    min: float | None = None
    max: float | None = None
    max_ratio: float | None = None
    max_meters: float | None = None
    mode: str | None = None
    count: int | None = None


class JudgeConfig(BaseModel):
    enabled: bool = True
    rubric: list[str] = Field(default_factory=list)
    include_conversation: bool = False  # 默认只喂 最终回答+工具调用+断言结果；true 时追加对话（截断）
    penalize_no_ask_back: bool = False  # external_driven 场景：外部 agent 缺必要信息不反问自行猜测 → 连续扣分（LLM rubric + 规则镜像）；默认关=存量零回归


class PassCriteria(BaseModel):
    required_assertions_passed: bool = True
    judge_score_min: float = 0.8

    @model_validator(mode="after")
    def _check_legacy_assertion_flag(self) -> "PassCriteria":
        if not self.required_assertions_passed:
            raise ValueError("pass_criteria.required_assertions_passed=false 已废弃；断言默认是硬门槛")
        return self


class AgentConfig(BaseModel):
    """agent_test 模式下外部智能体接入配置（见 docs/design/01-Agent接入契约.md）"""

    type: str = "http"
    endpoint: str | None = None
    query_params: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    api_key_env: str | None = None
    body: dict[str, Any] = Field(default_factory=dict)
    stream_response: bool = False
    timeout_seconds: int = 120
    session_id: str | None = None
    description: str = ""  # 外部 agent 能力说明，喂给 orchestrator 系统提示词（决定发什么指令、何时算达成）
    # orchestrator 任务流：react=现有 ReAct 模板（默认）；scripted=内置固定节点流程；
    # 其它值=orchestrator_flows.FLOW_REGISTRY 里注册的自定义 flow 名
    flow: str = "react"
    # orchestrator 本地 agent 是否允许缺信息时向用户追问（默认关，存量场景零回归）
    ask_user: bool = False
    # 模拟用户设定（反问闭环下沉后，各 executor 用 UserSimulator 按此设定回答 agent 追问）：
    # user_enabled 是"反问时模拟用户是否回答"的开关（ask_user 决定 agent 会不会反问）；
    # user_goal 是模拟用户"确定知道"的信息，规则回答用正则从中提取（数据集/距离/格式）。
    # skill 场景（agent_skill_test）没有外部 agent，但可配 agent.user_* 让 skill 反问也能闭环。
    user_enabled: bool = True
    user_profile: str = "normal_user"  # 模拟用户身份人设（persona）
    user_goal: str = ""  # 模拟用户目标/已知信息（反问时从中提取回答）
    user_max_turns: int = 5  # agent↔模拟用户最多往返轮次
    user_model: str = "rule-based-user"  # 模拟用户回答用模型（空/rule-based-* → 规则回答）


class TargetConfig(BaseModel):
    skill_id: str | None = None
    skill_version: str | None = None


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    version: str
    type: Literal["agent_skill_test", "agent_test"] = "agent_skill_test"
    description: str = ""
    target: TargetConfig
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    skill: SkillConfig | None = None
    agent: AgentConfig | None = None
    user_task: str
    expected_behavior: ExpectedBehavior = Field(default_factory=ExpectedBehavior)
    assertions: list[AssertionConfig] = Field(default_factory=list)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    pass_criteria: PassCriteria = Field(default_factory=PassCriteria)

    @model_validator(mode="after")
    def _check_type_fields(self) -> "Scenario":
        if self.type == "agent_skill_test" and self.skill is None:
            raise ValueError("type=agent_skill_test 时 skill 必填")
        if self.type == "agent_test" and self.agent is None:
            raise ValueError("type=agent_test 时 agent 配置必填")
        return self
