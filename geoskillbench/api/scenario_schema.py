"""前端"新建 Scenario"表单的 schema 定义（常用字段子集，模型驱动）。

前端通过 GET /api/scenarios/schema 拉取本结构并动态渲染表单：
- 每个 group 用 ``modes`` 标注适用哪种 scenario type（agent_skill_test / agent_test），
  前端按用户选择的模式过滤分组 → 实现"选择模式后配置项自动适配"。
- 每个 field 用点路径 key（如 ``runtime.max_turns``）映射到 Scenario 模型字段；
  ``required`` 标必填；``default`` 是预填的默认值（前端灰字展示，用户输入后覆盖）。
- ``data.fixtures`` 是动态行列表（group.list 定义行字段，前端可增删）。
- ``agent.flow`` 的 options 运行时由 FLOW_REGISTRY 动态填充（get_form_schema）。

为什么单独建文件而不是放 app.py：表单定义是稳定的领域数据，独立模块便于维护；
app.py 只暴露两个薄接口（拉 schema / 保存场景）。
"""

from __future__ import annotations

from copy import deepcopy

# 断言类型元数据：前端"新建 Scenario → 断言配置"逐条添加时，先选 type 再填对应参数。
# - modes：该断言类型适用哪些评测模式（skill 专属的不暴露给 agent 模式，避免恒 false 误导）。
# - category：process（过程断言，检查工具调用/结果存在等流程）| result（结果内容断言，对比参考数据集的真实几何/字段）。
# - fields：选中该 type 后要填的参数槽位（list_text = 逗号分隔转数组；default = 前端预填的默认值，灰字"未自定义"）。
ASSERTION_TYPES: list[dict] = [
    {
        "value": "final_response_contains",
        "label": "最终回答包含关键词",
        "modes": ["agent_skill_test", "agent_test"],
        "category": "process",
        "fields": [{"key": "values", "label": "关键词（逗号分隔）", "type": "list_text"}],
    },
    {
        "value": "skill_loaded",
        "label": "技能已加载",
        "modes": ["agent_skill_test"],
        "category": "process",
        "fields": [{"key": "skill_id", "label": "技能 ID", "type": "text"}],
    },
    {
        "value": "tool_available",
        "label": "工具可用",
        "modes": ["agent_skill_test"],
        "category": "process",
        "fields": [{"key": "tool", "label": "工具名", "type": "text"}],
    },
    {
        "value": "tool_called",
        "label": "工具已调用",
        "modes": ["agent_skill_test", "agent_test"],
        "category": "process",
        "fields": [{"key": "tool", "label": "工具名", "type": "text"}],
    },
    {
        "value": "tool_sequence",
        "label": "工具调用顺序",
        "modes": ["agent_skill_test", "agent_test"],
        "category": "process",
        "fields": [{"key": "sequence", "label": "顺序（逗号分隔）", "type": "list_text"}],
    },
    {
        "value": "tool_argument_equals",
        "label": "工具参数匹配",
        "modes": ["agent_skill_test"],
        "category": "process",
        "fields": [
            {"key": "tool", "label": "工具名", "type": "text"},
            {"key": "argument", "label": "参数名", "type": "text"},
            {"key": "value", "label": "参数值", "type": "text"},
        ],
    },
    {
        "value": "result_dataset_exists",
        "label": "结果数据集存在",
        "modes": ["agent_skill_test", "agent_test"],
        "category": "process",
        "fields": [{"key": "alias", "label": "数据集别名", "type": "text"}],
    },
    {
        "value": "result_geometry_type_in",
        "label": "结果几何类型",
        "modes": ["agent_skill_test", "agent_test"],
        "category": "process",
        "fields": [
            {"key": "target", "label": "数据集别名", "type": "text"},
            {"key": "values", "label": "几何类型（逗号分隔）", "type": "list_text"},
        ],
    },
    {
        "value": "skill_reference_loaded",
        "label": "技能文档已读取",
        "modes": ["agent_skill_test"],
        "category": "process",
        "fields": [{"key": "path", "label": "文档路径", "type": "text"}],
    },
    {
        "value": "skill_reference_not_loaded",
        "label": "技能文档未读取",
        "modes": ["agent_skill_test"],
        "category": "process",
        "fields": [{"key": "path", "label": "文档路径", "type": "text"}],
    },
    {
        "value": "skill_reference_loaded_before_tool",
        "label": "先读文档再调工具",
        "modes": ["agent_skill_test"],
        "category": "process",
        "fields": [
            {"key": "reference", "label": "文档路径", "type": "text"},
            {"key": "tool", "label": "工具名", "type": "text"},
        ],
    },
    {
        "value": "skill_reference_load_count_less_than",
        "label": "文档加载数上限",
        "modes": ["agent_skill_test"],
        "category": "process",
        "fields": [{"key": "value", "label": "上限数量", "type": "number"}],
    },
    {
        "value": "result_overlap_ratio",
        "label": "结果空间重合度（对比参考）",
        "modes": ["agent_skill_test", "agent_test"],
        "category": "result",
        "fields": [
            {"key": "reference", "label": "参考数据集名称", "type": "text", "source": "reference", "help": "必须是 data.reference 里注册的参考数据集"},
            {"key": "min", "label": "最小重合度（交集/参考面积）", "type": "number", "default": 0.9, "min": 0, "max": 1, "step": 0.05, "precision": 2, "help": "0~1，默认 0.9"},
        ],
    },
    {
        "value": "result_area_error_max",
        "label": "结果面积误差（对比参考）",
        "modes": ["agent_skill_test", "agent_test"],
        "category": "result",
        "fields": [
            {"key": "reference", "label": "参考数据集名称", "type": "text", "source": "reference", "help": "必须是 data.reference 里注册的参考数据集"},
            {"key": "max_ratio", "label": "最大相对面积误差", "type": "number", "default": 0.05, "min": 0, "max": 1, "step": 0.01, "precision": 2, "help": "默认 0.05 = 5%"},
        ],
    },
    {
        "value": "result_distance_max",
        "label": "结果空间偏移（Hausdorff）",
        "modes": ["agent_skill_test", "agent_test"],
        "category": "result",
        "fields": [
            {"key": "reference", "label": "参考数据集名称", "type": "text", "source": "reference", "help": "必须是 data.reference 里注册的参考数据集"},
            {"key": "max_meters", "label": "最大偏移（米）", "type": "number", "default": 20, "min": 0, "max": 100000, "step": 1, "precision": 0, "help": "Hausdorff 距离，默认 20 米"},
        ],
    },
    {
        "value": "result_fields_match",
        "label": "结果字段匹配（对比参考）",
        "modes": ["agent_skill_test", "agent_test"],
        "category": "result",
        "fields": [
            {"key": "reference", "label": "参考数据集名称", "type": "text", "source": "reference", "help": "必须是 data.reference 里注册的参考数据集"},
            {"key": "mode", "label": "匹配方式", "type": "select", "default": "contains", "options": [{"value": "contains", "label": "包含（参考字段是结果子集）"}, {"value": "exact", "label": "完全一致"}]},
        ],
    },
    {
        "value": "result_feature_count",
        "label": "结果要素数",
        "modes": ["agent_skill_test", "agent_test"],
        "category": "result",
        "fields": [
            {"key": "reference", "label": "参考数据集名称", "type": "text", "source": "reference", "help": "必须是 data.reference 里注册的参考数据集"},
            {"key": "count", "label": "期望要素数", "type": "number", "min": 1, "max": 100000, "step": 1, "precision": 0, "help": "必填，依赖参考数据"},
        ],
    },
]

# 表单定义（静态部分；agent.flow 的 options 在 get_form_schema 里动态注入）
FORM_SCHEMA: list[dict] = [
    {
        "key": "basic",
        "label": "基本信息",
        "modes": ["agent_skill_test", "agent_test"],
        "fields": [
            {
                "key": "type",
                "label": "评测模式",
                "type": "select",
                "required": True,
                "default": "agent_skill_test",
                "options": [
                    {"value": "agent_skill_test", "label": "skill 模式 · 本地技能评测"},
                    {"value": "agent_test", "label": "agent 模式 · 指挥外部智能体"},
                ],
                "help": "决定后续配置项：skill 模式显示技能/数据源，agent 模式显示外部智能体配置",
            },
            {"key": "id", "label": "场景 ID", "type": "text", "required": True, "default": "", "help": "唯一标识，也是保存的文件名（仅字母/数字/下划线/中划线）"},
            {"key": "name", "label": "场景名称", "type": "text", "required": True, "default": ""},
            {"key": "version", "label": "版本", "type": "text", "default": "1.0.0"},
            {"key": "description", "label": "描述", "type": "textarea", "default": ""},
            {"key": "user_task", "label": "用户任务", "type": "textarea", "required": True, "default": "", "help": "发给智能体的任务描述"},
        ],
    },
    {
        "key": "runtime",
        "label": "运行时配置",
        "modes": ["agent_skill_test", "agent_test"],
        "fields": [
            {
                "key": "runtime.executor",
                "label": "执行器",
                "type": "select",
                "required": True,
                "default": "skill",
                "options": [
                    {"value": "skill", "label": "skill · 本地技能评测"},
                    {"value": "orchestrator", "label": "orchestrator · 本地agent指挥外部agent"},
                    {"value": "external_driven", "label": "external_driven · 外部agent主导(角色反转)"},
                    {"value": "http_agent", "label": "http_agent · 直接透传外部agent"},
                    {"value": "nanobot", "label": "nanobot · 兼容模式"},
                ],
                "help": "跟随评测模式自动切换（skill模式→skill，agent模式→orchestrator），可手动改",
            },
            {"key": "runtime.agent_model", "label": "本地 Agent 模型", "type": "select", "default": "rule-based-agent", "options": [], "help": "models.yaml 里配置的模型别名；rule-based-agent 为无真实模型时的启发式兜底"},
            {"key": "runtime.max_turns", "label": "最大轮次", "type": "number", "default": 6, "help": "orchestrator 最多向外部 agent 发送的指令数"},
            {"key": "runtime.timeout_seconds", "label": "超时(秒)", "type": "number", "default": 180},
        ],
    },
    {
        "key": "skill",
        "label": "技能配置 · skill 模式",
        "modes": ["agent_skill_test"],
        "fields": [
            {"key": "skill.path", "label": "技能文件路径", "type": "text", "required": True, "default": "", "help": "相对 scenarios/ 目录，如 ../skills/gis_buffer_analysis.skill.yml"},
            {
                "key": "skill.load_mode",
                "label": "加载模式",
                "type": "select",
                "default": "file",
                "options": [
                    {"value": "file", "label": "file · 单文件技能"},
                    {"value": "package", "label": "package · 技能包目录"},
                    {"value": "package_zip", "label": "package_zip · 技能包压缩包"},
                ],
            },
        ],
    },
    {
        "key": "data",
        "label": "数据源",
        "modes": ["agent_skill_test", "agent_test"],
        "list": {
            "key": "fixtures",
            "label": "输入数据集（仅 skill 模式：操作对象，agent 可见）",
            "row_label": "输入数据集",
            "modes": ["agent_skill_test"],
            "fields": [
                {"key": "id", "label": "ID", "type": "text", "required": True, "default": ""},
                {"key": "name", "label": "名称", "type": "text", "default": ""},
                {"key": "catalog_id", "label": "目录 ID", "type": "text", "default": "", "help": "5B 服务端输入数据逻辑 ID；有此项则不必再填本地 path"},
                {
                    "key": "format",
                    "label": "格式（本地遗留）",
                    "type": "select",
                    "default": "",
                    "options": [
                        {"value": "", "label": "（服务端引用 / 不填）"},
                        {"value": "geojson", "label": "geojson"},
                        {"value": "shapefile", "label": "shapefile"},
                        {"value": "geopackage", "label": "geopackage"},
                        {"value": "csv", "label": "csv"},
                        {"value": "db_table", "label": "db_table · PostGIS 表"},
                    ],
                },
                {"key": "path", "label": "本地路径（遗留）", "type": "text", "default": "", "help": "仅尚未迁移的本地文件；5B 用 catalog_id"},
                {"key": "table", "label": "数据库表名（format=db_table）", "type": "text", "default": ""},
            ],
        },
        "reference_list": {
            "key": "reference",
            "label": "参考数据集（ground truth，仅断言读取，不暴露给 agent）",
            "row_label": "参考数据集",
            "fields": [
                {"key": "id", "label": "ID", "type": "text", "required": True, "default": ""},
                {"key": "name", "label": "名称", "type": "text", "default": ""},
                {"key": "evaluation_id", "label": "评测数据 ID", "type": "text", "default": "", "help": "5B 评测库 ground truth 逻辑 ID；外部 agent 场景优先填这个，不必配输入文件"},
                {"key": "path", "label": "本地路径（遗留）", "type": "text", "default": "", "help": "仅尚未迁移的本地参考文件"},
                {"key": "table", "label": "数据库表名（遗留 db_table）", "type": "text", "default": ""},
            ],
        },
    },
    {
        "key": "mcp",
        "label": "MCP 工具服务 · skill 模式",
        "modes": ["agent_skill_test"],
        "list": {
            "key": "servers",
            "label": "MCP server（连上后自动发现工具，无需手动声明工具列表）",
            "row_label": "MCP server",
            "fields": [
                {"key": "id", "label": "ID", "type": "text", "required": True, "default": "", "help": "唯一标识（如 gpa_vector）"},
                {"key": "name", "label": "名称", "type": "text", "default": ""},
                {
                    "key": "transport",
                    "label": "传输",
                    "type": "select",
                    "default": "mock",
                    "options": [
                        {"value": "mock", "label": "mock · 本地内置工具"},
                        {"value": "stdio", "label": "stdio · 本地进程"},
                        {"value": "sse", "label": "sse · 远程服务"},
                        {"value": "http", "label": "http · 远程服务"},
                    ],
                },
                {"key": "url", "label": "URL", "type": "text", "default": "", "help": "transport 非 mock 时必填，如 http://host:port/sse"},
            ],
        },
    },
    {
        "key": "agent",
        "label": "外部智能体 · agent 模式",
        "modes": ["agent_test"],
        "fields": [
            {"key": "agent.endpoint", "label": "Endpoint", "type": "text", "required": True, "default": "", "help": "外部智能体 HTTP 接口地址"},
            {"key": "agent.description", "label": "能力说明", "type": "textarea", "default": "", "help": "喂给 orchestrator 提示词，决定发什么指令、何时算达成"},
            {"key": "agent.flow", "label": "任务流程", "type": "select", "required": True, "default": "react", "options": [], "help": "orchestrator 本地 agent 的流程；自定义流程经 FLOW_REGISTRY 注册后也会出现在这里"},
            {"key": "agent.ask_user", "label": "允许追问用户", "type": "switch", "default": False, "help": "本地 agent 缺信息时按 [NEED_INTERACTION] 协议向用户追问，反问由模拟用户自动回答；需要精确回答时在场景 YAML 配 agent.user_goal"},
            {"key": "agent.timeout_seconds", "label": "超时(秒)", "type": "number", "default": 120},
            {"key": "agent.api_key_env", "label": "API Key 环境变量", "type": "text", "default": "", "help": "请求头鉴权用的环境变量名（可选）"},
        ],
    },
    {
        "key": "assertions",
        "label": "断言配置",
        "modes": ["agent_skill_test", "agent_test"],
        "default_switch": {
            "key": "use_default_process_assertions",
            "label": "使用默认过程断言",
            "default": True,
            "help": "开启 = 不手写过程断言（沿用场景自带/断言阶段默认）；关闭 = 手动添加过程断言",
        },
        "result_switch": {
            "key": "use_result_assertions",
            "label": "使用结果断言（对比参考数据集）",
            "default": False,
            "help": "开启 = 手动添加结果内容断言（result_*），需场景配有参考数据集",
        },
        "list": {
            "key": "process_assertions",
            "label": "过程断言",
            "row_label": "过程断言",
            "types": [t for t in ASSERTION_TYPES if t.get("category") != "result"],
        },
        "result_list": {
            "key": "result_assertions",
            "label": "结果断言",
            "row_label": "结果断言",
            "types": [t for t in ASSERTION_TYPES if t.get("category") == "result"],
        },
    },
    {
        "key": "judge",
        "label": "评测判定",
        "modes": ["agent_skill_test", "agent_test"],
        "fields": [
            {"key": "judge.enabled", "label": "启用判定", "type": "switch", "default": True},
            {"key": "pass_criteria.judge_score_min", "label": "判定通过分数", "type": "number", "default": 0.8, "help": "judge 得分低于该值判失败"},
        ],
        "default_switch": {
            "key": "use_default_rubric",
            "label": "使用默认评分标准",
            "default": True,
            "help": "开启 = 用框架内置 rubric；关闭 = 自行逐条添加评分维度",
        },
        "list": {
            "key": "judge.rubric",
            "label": "评分标准",
            "row_label": "评分维度",
            "fields": [{"key": "text", "label": "评分维度", "type": "text"}],
        },
    },
]


def _available_flows() -> list[str]:
    # 触发完整注册链（react 在 orchestrator_executor 定义，scripted 在 orchestrator_flows，
    # keyword/pipeline 在 example_flows），再取注册表全量
    import geoskillbench.executors.orchestrator_executor  # noqa: F401

    from geoskillbench.executors.orchestrator_flows import available_flows

    return available_flows()


def _available_agent_models() -> list[str]:
    """models.yaml 里配置的模型别名；rule-based-agent 启发式兜底永远可用。"""
    from geoskillbench.runtime.llm import load_models_config

    aliases = sorted(load_models_config().get("models", {}).keys())
    return ["rule-based-agent", *[name for name in aliases if name != "rule-based-agent"]]


def get_form_schema() -> list[dict]:
    """返回深拷贝的表单定义，并注入 agent.flow / runtime.agent_model 的动态选项。"""
    schema = deepcopy(FORM_SCHEMA)
    flows = _available_flows()
    models = _available_agent_models()
    for group in schema:
        for field in group.get("fields", []):
            if field.get("key") == "agent.flow":
                field["options"] = [{"value": name, "label": name} for name in flows]
            elif field.get("key") == "runtime.agent_model":
                field["options"] = [{"value": name, "label": name} for name in models]
    return schema
