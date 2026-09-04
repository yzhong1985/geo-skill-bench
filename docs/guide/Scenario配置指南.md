# GeoSkillBench Scenario 配置指南

> 一份 yml 文件 = 一次评测场景。本文说明 scenario 文件的字段、两种模式、executor/flow 选择、模拟用户配置与断言写法。
> 配套：模型定义见 `geoskillbench/models/scenario.py`；前端可用"新建 Scenario"表单（覆盖常用字段），复杂配置直接改 yml。

## 1. 概述

| 维度 | 说明 |
|---|---|
| 文件位置 | `scenarios/*.yml`（相对路径引用其他资源：`../skills/...`、`../fixtures/...`） |
| 两种模式 | `type: agent_skill_test`（**skill 模式**，测本地技能）/ `type: agent_test`（**agent 模式**，测外部智能体） |
| 两种编辑方式 | 前端表单（常用字段，保存为 `scenarios/<id>.yml`）/ 手写 yml（全量字段） |
| 校验与运行 | `python -m geoskillbench.cli validate scenarios/xx.yml` / 前端 Validate+Create Task |

**模式决定配置块**：skill 模式需要 `skill`，输入用 `data.fixtures`（`catalog_id` 或遗留 path）；两种模式都可配 `data.reference`（ground truth，仅断言读取）。agent 模式需要 `agent`（含 `user_*`），**不必配输入文件**——外部 agent 自己选数据，平台只备 `evaluation_id` 参考表做 `result_*` 库内比对。

## 2. 文件结构总览

```yaml
id: 场景唯一标识（也是文件名）
name: 场景名
version: 版本          # 必填
type: agent_skill_test | agent_test   # 默认 agent_skill_test
description: 描述
target: { skill_id, skill_version }   # 可选，agent 模式通常 target: {}
runtime:              # 公共
  executor: skill     # skill | orchestrator | external_driven | http_agent | nanobot
  agent_model: rule-based-agent   # 本地 agent 模型（models.yaml 别名）
  max_turns: 6
  timeout_seconds: 180
data:
  fixtures: [ ... ]   # 输入数据集（skill 模式；agent 模式通常省略）
  reference: [ ... ]  # 参考数据集（ground truth，仅断言读取；agent 模式用 evaluation_id）
skill:                # 仅 skill 模式
  path, load_mode, ...
agent:                # agent 模式必填；skill 模式可配 user_*（模拟用户）
  endpoint, flow, ask_user, ...
  user_enabled, user_profile, user_goal, user_max_turns, user_model   # 模拟用户（反问回答）
mcp:                  # 公共（agent_test 可空）
  servers: [ ... ]
  tools: { required, optional }
judge:                # 公共
  enabled, rubric, include_conversation
expected_behavior:    # 公共（skill 模式常用）
  should_load_skills, should_call_tools, optional_tools, should_not
assertions: [ ... ]   # 公共
pass_criteria:        # 公共
  required_assertions_passed, judge_score_min
user_task: 用户任务   # 必填
```

## 3. 公共字段

### 3.1 顶层

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `id` | ✅ | — | 唯一标识，同时是文件名（字母/数字/下划线/中划线） |
| `name` | ✅ | — | 场景名 |
| `version` | ✅ | — | 版本号 |
| `type` | — | `agent_skill_test` | 评测模式，决定后面配置块 |
| `description` | — | `""` | 描述 |
| `target` | ✅（块） | — | `{skill_id, skill_version}`；agent 模式写 `target: {}` |
| `user_task` | ✅ | — | 发给智能体的任务描述（agent 模式会被 orchestrator 本地 agent 当第一轮输入） |

### 3.2 `runtime`（执行与模型）

| 字段 | 默认 | 说明 |
|---|---|---|
| `executor` | `skill` | 见 §4 executor 选择 |
| `agent_model` | `rule-based-agent` | 本地 agent 模型（models.yaml 别名）；`rule-based-*` 走启发式兜底 |
| `judge_model` | `""` | 空 = 跟随 agent_model；配 `rule-based-*` 显式降级规则判定 |
| `max_turns` | `6` | runner 总轮次上限；orchestrator 场景 = 最多发外部指令数；external_driven 场景 = 平台→外部 agent 消息总数上限 |
| `timeout_seconds` | `180` | 单步超时 |
| `memory_enabled` | `false` | 是否启用会话记忆 |

> 历史注：`runtime.actor_model` 已随反问闭环下沉重构删除，模拟用户模型由 `agent.user_model` 配置。

### 3.3 `judge` / `pass_criteria`（评测判定）

| 字段 | 默认 | 说明 |
|---|---|---|
| `judge.enabled` | `true` | 是否跑 judge（LLM 优先，缺模型自动降级规则判定） |
| `judge.rubric` | `[]` | LLM judge 的评分细则（逐条 rubric） |
| `judge.include_conversation` | `false` | true 时 LLM judge 额外喂完整对话（截断） |
| `judge.penalize_no_ask_back` | `false` | external_driven：外部 agent 缺必要信息不反问 → 连续扣分 |
| `pass_criteria.required_assertions_passed` | `true` | 断言全过才判通过 |
| `pass_criteria.judge_score_min` | `0.8` | judge 得分下限 |

### 3.4 `expected_behavior`（预期行为，skill 模式常用）

```yaml
expected_behavior:
  should_load_skills: [gis_buffer_analysis]   # 期望加载的技能
  should_call_tools: [query_dataset_metadata, create_buffer]
  optional_tools: [reproject_dataset]          # 可选调用
  should_not: ["在缺少输入数据时直接执行"]       # 禁止行为（供 LLM judge 参考）
```

### 3.5 `mcp`（MCP 工具服务器）

```yaml
mcp:
  servers:
    - id: gpa_vector
      name: GPA 矢量分析服务
      transport: mock          # mock（等价 stdio，本地 mock server 进程）| stdio | sse | http
      url: mock://vector       # mock/stdio 不需要远程地址；sse/http 必填
      required: true
```

**工具自动发现（v0.5 现状）**：工具由 server `tools/list` 自动发现，`mcp.tools.required/optional` **不再作为授权来源**（字段保留兼容存量）。真正的可见性过滤 = 场景声明的工具 ∩ skill `recommended_mcp_tools`——skill 未推荐的工具不暴露给 agent。skill 声明的必需工具在所有 server 上都缺失时，LOAD_SKILL 阶段 fail-fast 直接失败（agent 不启动，报告列缺失清单）。

```yaml
# 存量写法仍兼容（tools 块会被读取但不做授权过滤）
mcp:
  servers: [...]
  tools:
    required: [{ server: metadata, name: query_dataset_metadata }]
    optional: [{ server: gpa_vector, name: reproject_dataset }]
```

云端 MCP server 示例（sse transport）见 `scenarios/cloud_remote_smoke_001.yml`；本地 mock 全流程见 `scenarios/mock_buffer_full_flow_001.yml`。

agent 模式通常 `mcp: { servers: [], tools: { required: [], optional: [] } }`（或省略）。

## 4. `runtime.executor` 选择

| executor | 适用 | 角色 |
|---|---|---|
| `skill` | skill 模式（默认） | 本地 agent 用技能评测，可走 MCP 工具；历史别名 `langgraph` |
| `orchestrator` | agent 模式 | 本地 LLM agent **多轮指挥**外部 agent（拆解→发指令→读回答→决定下一步） |
| `external_driven` | agent 模式 | 角色反转：外部 agent 主导自主执行，缺信息反问；平台 LLM 扮演模拟用户回答 |
| `http_agent` | agent 模式 | user_task **直接透传**外部 agent，一问一答（外部 agent 足够聪明时用） |
| `nanobot` | 兼容 | nanobot 运行时契约（当前兼容模式） |

> 判断：外部 agent 全自动执行 → `http_agent`；需要本地拆解/指挥/追问 → `orchestrator` 或 `external_driven`。

## 5. skill 模式专属字段（`type: agent_skill_test`）

### 5.1 `skill`

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `path` | ✅ | — | 相对 `scenarios/`，如 `../skills/gis_buffer_analysis.skill.yml` |
| `load_mode` | — | `file` | `file`（单文件）/ `package`（技能包目录）/ `package_zip` |
| `entry` | — | — | 入口文件（package 模式） |
| `lazy_load_references` | — | `false` | 引用按需加载（配合 `skill_reference_*` 断言） |
| `required` | — | `true` | 加载失败是否判失败 |

### 5.2 `data.fixtures` / `data.reference`（数据集注册）

**输入数据（`data.fixtures`）**——skill 的操作对象，agent 可见（提示词列出、工具可解析）。**最小配置只需 `id` + `path`**（其余自动识别）：

```yaml
data:
  fixtures:
    - id: schools              # 数据集标识（工具/断言里用这个名字），唯一必填
      path: ../fixtures/schools.geojson
```

**参考数据（`data.reference`）**——结果断言的 ground truth，**独立块、与输入数据分离**。参考数据**不暴露给被测 agent**（不进提示词、工具解析不到），只在断言引擎比对时读取，避免标准答案泄露：

```yaml
data:
  reference:
    - id: expected_buffer      # 结果断言 reference 用这个名字
      path: ../fixtures/expected_buffer_school_500m.geojson
```

自动识别项（可省略，显式写了会覆盖）：`name`（缺省=id）、`type`（vector）、`format`（按扩展名）、`crs`、`geometry_type`、`feature_count`、`fields`（均从文件读取）。支持 geojson / shapefile / gpkg。两个列表结构一致，都用下方 db_table 语法（参考数据也支持从 PostGIS 表拉取）。

**数据库数据源（`format: db_table`）**：输入或参考数据集在 PostGIS 表里时，`table` 必填，`db_url` 缺省回落 `DATABASE_URL` 环境变量（即 docker 部署的 PostGIS 库）。放在 `data.fixtures`（输入）或 `data.reference`（参考）皆可：

```yaml
data:
  fixtures:
    - id: schools
      path: ../fixtures/schools.geojson
  reference:
    - id: expected_buffer
      type: vector
      format: db_table
      db_url: postgresql+psycopg://geo:geo@host:5432/dbname   # 可省略，缺省用 DATABASE_URL
      table: expected_buffer
      db_schema: public       # 缺省 public
```

拉取时按 `geometry_columns` 取几何列与 SRID，落到本地临时文件供比对，**run 结束时由 cleanup 删除**（不进报告产物）。可参考 `scenarios/buffer_school_500m_reference_db_001.yml`。正式 5B / 外部 agent 评测优先用 `evaluation_id`，不要再配本地 path。

### 5.3 外部 agent 场景的参考数据

`type: agent_test` **不需要** `data.fixtures`（输入由外部 agent 自己选）。要做 `result_*` 时只配 `data.reference`：

```yaml
type: agent_test
data:
  reference:
    - id: expected_buffer
      evaluation_id: tmp_createBuffer_260828150924737   # 评测库 ground truth 逻辑表
assertions:
  - type: result_overlap_ratio
    target: buffer_result          # HTTP tool_event 登记的默认别名
    reference: expected_buffer
    min: 0.9
```

前提：外部 agent SSE `tool_event` 带 SuperMap 结果字段（`tableName` / `bufferResult`），平台登记后走 PostGIS 库内比对（`GEO_EVAL_DATABASE_URL`）。没有 GIS 产出则 `result_*` 失败，不影响纯文字场景。

## 6. agent 模式专属字段（`type: agent_test`）

### 6.1 `agent`（外部智能体接入）

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `endpoint` | ✅ | — | 外部 agent HTTP 接口 |
| `description` | — | `""` | 能力说明，喂给 orchestrator 提示词（决定发什么指令、何时算达成） |
| `flow` | — | `react` | orchestrator 本地 agent 的流程，见 §7 |
| `ask_user` | — | `false` | 缺信息时是否允许向用户追问（react 流程内置）；反问由模拟用户自动回答 |
| `timeout_seconds` | — | `120` | 请求超时 |
| `type` | — | `http` | 接入类型 |
| `api_key_env` | — | — | 请求头鉴权环境变量名 |
| `stream_response` | — | `false` | 是否 SSE 流式 |
| `query_params` / `headers` / `body` / `session_id` | — | — | 透传参数 |

### 6.2 模拟用户 `agent.user_*`（反问回答，两种模式都可配）

反问闭环已下沉到 executor 内部：被测 agent 输出 `[NEED_INTERACTION] <问题>` → 共享 `UserSimulator`
按 `user_goal` 回答（规则正则或 LLM persona）→ 回答回填继续。模拟用户设定并入 `agent` 块：

```yaml
agent:
  # ... agent 模式的外部接入字段（endpoint/flow 等）...
  ask_user: true                # 是否允许被测 agent 反问（开关）
  user_enabled: true            # 反问时模拟用户是否自动回答（默认 true）
  user_profile: normal_user     # 模拟用户人设 persona（LLM 模式用）
  user_max_turns: 5             # agent↔模拟用户最多往返轮次
  user_goal: 使用 schools 数据对学校周边 500 米做缓冲区分析，输出格式 GeoJSON
  user_model: rule-based-user   # 模拟用户模型；空/rule-based-* → 规则回答
```

`ask_user` 决定"被测 agent 会不会反问"，`user_*` 决定"反问时模拟用户怎么回答"，两者配合。skill 模式没有外部 agent，但同样可配 `agent.user_*` 让 skill 反问也能闭环。

> **规则回答的提取顺序**（`user_model` 为空或 `rule-based-*`）：候选选择 > 输出格式 > 数据集 > 距离 > 兜底。详见 §9 user_goal 写作指南。

## 7. `agent.flow`（orchestrator 本地 agent 流程）

| flow | 定义位置 | 行为 |
|---|---|---|
| `react` | orchestrator_executor（默认） | 自由式 ReAct：LLM 自主决定发指令/追问，`ask_user=true` 时支持 [NEED_INTERACTION] 追问 |
| `scripted` | orchestrator_flows | 内置固定节点：生成指令→发外部→LLM 判完成→路由，结构固定可审计 |
| `keyword` / `pipeline` | example_flows（示例） | keyword：终止用规则关键词；pipeline：带首轮计划节点 |
| 自定义 | `@register_flow("名字")` | 写新模块注册，scenario 按名引用（见 `example_flows.py` 注释） |

> 追问（模拟用户回答）**仅 react 流程内置**；scripted/pipeline/keyword 需 flow 作者按 `[NEED_INTERACTION]` 协议自行实现。

## 8. 协议前缀（[NEED_INTERACTION] / [FINAL]）

| 前缀 | 含义 | 触发 |
|---|---|---|
| `[NEED_INTERACTION]` | 本地 agent 要追问用户 | `ask_user=true` 且 react 流程；executor 内部闭环 → 模拟用户按 `user_goal` 回答 → 继续 |
| `[FINAL]` | 任务完成/收尾 | 最终回答必须以它开头，否则 `final_response_contains` 断言、judge 失效 |

## 9. user_goal 写作指南

`user_goal` = **模拟用户"确定知道"的信息**。`UserSimulator` 用正则从 goal 提取答案（候选选择 > 格式 > 数据集 > 距离），规则回答由此生成：

| goal 片段（句式） | 服务的问题 | 提取结果 |
|---|---|---|
| `使用 schools 数据` | "用哪个数据集？" + **候选选择** | 目标词 `schools` |
| `500 米` | "缓冲距离？" | `500 米` |
| `输出格式 GeoJSON` | "输出格式？" | `GeoJSON` |

**最佳实践**：
1. 固定句式 `使用 {英文id} 数据`——候选选择的正则只认 `[A-Za-z0-9_]+`，中文/无句式提取不到目标词，会落"取第一个候选"
2. 数据集名写**前缀/系列名**（如 `schools`）而非全名——候选往往是 `schools_a`、`schools_b` 这种动态 id，前缀能子串命中
3. 只写用户"确定知道"的信息；用户对候选无所谓就别写（取第一个候选正好符合"随便哪个"）
4. 不知道的**别编**——写进 goal 等于替用户编造，会误导本地 agent

```yaml
agent:
  user_goal: 使用 schools 数据对学校周边 500 米做缓冲区分析，输出格式 GeoJSON
  # 候选 [schools_a, schools_b, rivers] → 子串命中 schools_a
  # "缓冲距离是多少？" → 500 米。  "输出格式？" → GeoJSON。
```

## 10. 断言参考（`assertions`）

`type` + 关键参数（全部断言通过才判 pass）：

| type | 参数 | 说明 |
|---|---|---|
| `skill_loaded` | `skill_id` | 技能是否加载 |
| `tool_available` | `tool` | 工具是否可用 |
| `tool_called` | `tool` | 工具是否被调用 |
| `tool_sequence` | `sequence: [a, b]` | 工具调用顺序（按序出现即可，不要求连续） |
| `tool_argument_equals` | `tool, argument, value` | 某工具某参数等于期望值 |
| `result_dataset_exists` | `alias` | 结果数据集是否存在 |
| `result_geometry_type_in` | `target, values: [Polygon]` | 结果几何类型 |
| `result_overlap_ratio` | `reference, min` | **结果内容**：空间重合度（交集面积/参考面积），默认 `min: 0.9` |
| `result_area_error_max` | `reference, max_ratio` | **结果内容**：总面积相对误差上限，默认 `max_ratio: 0.05` |
| `result_distance_max` | `reference, max_meters` | **结果内容**：Hausdorff 空间偏移上限（米），默认 `max_meters: 20` |
| `result_fields_match` | `reference, mode` | **结果内容**：结果字段与参考字段匹配（`exact` 完全一致 / `contains` 参考是子集，默认 contains） |
| `result_feature_count` | `reference, count` | **结果内容**：结果要素数等于 `count` |
| `final_response_contains` | `values: [a, b]`（或 `value`） | 最终回答包含所有关键字 |
| `skill_reference_loaded` | `path` | 技能引用是否被按需加载 |
| `skill_reference_not_loaded` | `path` | 引用未被加载 |
| `skill_reference_loaded_before_tool` | `reference, tool` | 引用加载先于某工具调用 |
| `skill_reference_load_count_less_than` | `value` | 引用加载次数上限 |

> **结果内容断言（`result_*`）说明**：把评测从"过程对不对"升级到"结果对不对"。比对的是**真实几何内容**，报告断言行会带 **`实际 X / 预期 Y`** 和后端标记（`[file]` / `[postgis]`）。需要：
> 1. 结果数据集可定位：本地 path，或 adapter 内部表名 + 参考 `evaluation_id`/`catalog_id`（`GEO_EVAL_DATABASE_URL` 指向评测库，不是报告库 `DATABASE_URL`）；
> 2. `reference` 参考数据集在 `data.reference` 里注册一个 fixture（如 `expected_buffer`），作为 ground truth。参考数据是独立块，**不暴露给被测 agent**（仅断言引擎比对时读取）。
> 结果数据集别名（`target`）**可省略**，缺省为 `buffer_result`（skill 流里 `create_buffer` 的默认产出别名）；若 skill 产出的数据集别名不同（如工具自定义了 `output_alias`），用 `target: <别名>` 显式指定。
> 面积/偏移先自动对齐到参考中心点选定的 UTM 带（米制），避免 4326 平方度、3857 纬度变形；结果与参考 CRS 不同也能比。库内路径始终对整表 `ST_Union`，不按 `smid` 取单行。参考建议用独立工具（真实 GIS/PostGIS）生成，避免与 mock 同源算法造成假阳性。可参考示例场景 `scenarios/buffer_school_500m_reference_001.yml`、`scenarios/buffer_school_500m_5b_001.yml` 与生成脚本 `scripts/generate_reference_buffer.py`。

## 11. 完整示例

### 11.1 skill 模式（本地技能评测）

```yaml
id: buffer_school_500m_001
name: 学校周边 500 米缓冲区分析
version: 1.0.0
type: agent_skill_test
description: 测试 gis_buffer_analysis 技能是否引导 agent 正确调用 MCP 工具完成缓冲分析。
target:
  skill_id: gis_buffer_analysis
  skill_version: 1.0.0
runtime:
  executor: skill
  agent_model: rule-based-agent
  max_turns: 6
data:
  fixtures:
    - id: schools
      name: 学校点数据
      type: vector
      format: geojson
      path: ../fixtures/schools.geojson
      crs: EPSG:4326
      geometry_type: Point
mcp:
  servers:
    - id: gpa_vector
      name: GPA 矢量分析服务
      transport: mock
      url: mock://vector
      required: true
  tools:
    required:
      - { server: metadata, name: query_dataset_metadata }
      - { server: gpa_vector, name: create_buffer }
skill:
  load_mode: file
  path: ../skills/gis_buffer_analysis.skill.yml
user_task: 请帮我生成 schools 数据周边 500 米的服务范围。
expected_behavior:
  should_load_skills: [gis_buffer_analysis]
  should_call_tools: [query_dataset_metadata, create_buffer]
  should_not: ["在缺少输入数据时直接执行"]
assertions:
  - { type: skill_loaded, skill_id: gis_buffer_analysis }
  - { type: tool_called, tool: create_buffer }
  - { type: tool_argument_equals, tool: create_buffer, argument: distance, value: 500 }
  - { type: result_geometry_type_in, target: buffer_result, values: [Polygon, MultiPolygon] }
  - { type: final_response_contains, values: ["500", "缓冲区"] }
judge:
  enabled: true
pass_criteria:
  required_assertions_passed: true
  judge_score_min: 0.8
```

### 11.2 agent 模式（orchestrator 指挥 + 模拟用户回答反问）

```yaml
id: agent_orchestrated_actor_multi_turn
name: 外部智能体-多轮指挥-与模拟用户自动多轮
version: "1.0.0"
type: agent_test
target: {}
runtime:
  executor: orchestrator
  agent_model: deepseek-v4-flash   # orchestrator 需要真实本地模型（无启发式兜底）
  max_turns: 5                     # 最多向外部 agent 发的指令数
agent:
  type: http
  endpoint: http://<host>:8490/agentx/workflowstudio/api/v1/run/<flow_id>
  description: 能对 GIS 数据集执行查询、叠加求交、缓冲区等空间分析的智能体
  ask_user: true                   # 缺信息时允许向用户追问（反问由模拟用户回答）
  user_enabled: true
  user_max_turns: 3
  user_goal: 使用 schools 数据对学校周边 500 米做缓冲区分析
  flow: react
data: {}
mcp: { servers: [], tools: { required: [], optional: [] } }
user_task: 对学校做缓冲区分析
assertions:
  - { type: final_response_contains, value: "完成" }
judge:
  enabled: true
pass_criteria:
  required_assertions_passed: true
  judge_score_min: 0.8
```

## 12. 校验与运行

```bash
# 校验场景（schema + 依赖检查）
python -m geoskillbench.cli validate scenarios/buffer_school_500m_001.yml

# 列出场景可用工具
python -m geoskillbench.cli list-tools scenarios/buffer_school_500m_001.yml

# 运行并生成报告
python -m geoskillbench.cli run scenarios/buffer_school_500m_001.yml --output reports
```

前端：选中场景 → Validate → Create Task，SSE 实时看阶段进度，结果含工具调用/对话/断言/LLM judge。

### 12.1 场景管理（前端"管理"入口 / REST API）

| 操作 | API | 说明 |
|---|---|---|
| 查看原文 | `GET /api/scenarios/{id}` | 返回 yml 原文（前端编辑回显，保留注释） |
| 新建 | `POST /api/scenarios` | 表单组装或整段 yml；后端 `Scenario.model_validate` 校验 |
| 编辑 | `PUT /api/scenarios/{id}` | 校验后**原样写回**（保留注释与格式）；`id` 不可修改（文件名即 id） |
| 删除 | `DELETE /api/scenarios/{id}` | 只删配置文件；历史报告（reports/ 与 DB run_history）按 scenario_id 独立留存 |

`id` 仅允许字母/数字/下划线/中划线（防路径穿越）。校验失败返回可读 400 与 pydantic 错误明细。

### 12.2 历史评测自动清理

DB run_history 只保留最近 N 条：保存报告时自动清理 + 后端启动时清积压。阈值 `GEO_BENCH_HISTORY_KEEP` 环境变量（默认 100）；报告文件（reports/json、markdown）不受影响，只清理数据库行。

## 13. 边界与常见问题

- **orchestrator 无启发式兜底**：`agent_model` 必须是 models.yaml 真实别名，缺 langgraph 依赖 / 缺 endpoint 直接报错（不是失败场景而是配置错误）。
- **`[FINAL]` 必须打头**：最终回答不以 `[FINAL]` 开头，judge 和 `final_response_contains` 可能误判。
- **反问闭环预算在 executor 内部**：每次追问消耗 `agent.user_max_turns` 的往返预算（不消耗 `runtime.max_turns` 的外部指令预算）；`user_goal` 缺失时规则回答走兜底（数据集→"使用默认数据"、距离→"500 米"、格式→"GeoJSON"）。
- **残留旧 `actor:` 块会静默失效**：pydantic 默认忽略未知字段，旧版独立 `actor:` 块不会报错但模拟用户设定全部丢失（回落默认值）。迁移对照见 `../retrospective/反问闭环下沉重构.md` §3。
- **fail-fast：skill 所需工具缺失时 agent 不启动**：skill 声明的 `recommended_mcp_tools` 未被任何 server 提供 → LOAD_SKILL 阶段直接失败，报告列出缺失清单；这是"环境配错"与"agent 能力不行"的干净切分，不会产生部分评分。
- **Docker 部署下的数据源**：`db_table` fixture 的 `db_url` 缺省回落 `DATABASE_URL`——compose 里指向 postgis 服务（`postgresql+psycopg://geo:geo@postgis:5432/geoskillbench`）；本地跑则回落 `.env` 的同名变量。
- **前端表单覆盖常用字段**：断言（已支持逐条添加 result_*）、expected_behavior、自定义 flow 等复杂块表单不完整暴露，需要时直接编辑生成的 yml（管理入口见 §12.1）。
- **`executor: langgraph` 是历史别名**，等价 `skill`，存量场景兼容可不动。
