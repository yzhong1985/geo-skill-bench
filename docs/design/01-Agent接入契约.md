# Agent 接入契约

> 定义"独立智能体"（`type: agent_test` 的 scenario）如何接入 GeoSkillBench 评测平台做黑盒评测。
> 首个接入对象：**SuperMap Workflow Studio API**（Agentx Server），接口文档见 `../reference/openapi-workflow-studio.yaml`。
> 本文为活文档：阶段一建立基础契约（一问一答），后续 orchestrator / external_driven / 反问闭环等扩展见第 7 节，历史过程见 `../retrospective/`。

## 1. 接入对象与协议

- 系统：SuperMap Workflow Studio（Agentx Server）
- 交互模式：**一问一答**——每次请求返回一个完整响应，无平台内部 `[NEED_INTERACTION]` 语义
- 响应方式：SSE 流式（`stream=true`）或 JSON（`stream=false`），由调用方选择

### 1.1 运行工作流接口

```
POST /agentx/workflowstudio/api/v1/run/{flow_id}
```

| 参数 | 位置 | 类型 | 说明 |
|---|---|---|---|
| `flow_id` | path | uuid | 工作流智能体 ID |
| `stream` | query | bool | `true`=SSE 流式，`false`=JSON |

请求体 `SimplifiedAPIRequest`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `input_value` | string | 输入给 ChatInput 的文本（必填，由 executor 动态注入） |
| `input_type` | chat/text/any | 默认 chat |
| `output_type` | chat/text/any/debug | 默认 chat |
| `output_component` | string \| null | 可选，指定输出组件 ID |
| `tweaks` | object | 可选，覆盖组件参数 |
| `session_id` | string \| null | 可选，回传以保持多轮上下文 |

响应 `RunResponse`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `outputs` | array | 运行结果；实测为 `[{ "outputs": [{ "results": { "message": { "data": { "text": ... }}}]}]` |
| `session_id` | string | 可回传以保持上下文 |

## 2. agent 配置字段（scenario YAML）

```yaml
type: agent_test
agent:
  type: http
  endpoint: http://<host>:8490/agentx/workflowstudio/api/v1/run/<flow_id>
  query_params:
    stream: "true"
  headers: {}
  api_key_env: AGENTX_API_KEY     # 密钥从环境变量读取，绝不写死在 YAML
  body:
    input_type: chat
    output_type: chat
  stream_response: true
  timeout_seconds: 120
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `type` | str | 接入类型，当前仅 `http`（预留 `sdk`） |
| `endpoint` | str | POST 完整 URL（含 flow_id 等路径参数） |
| `query_params` | dict[str,str] | 追加到 query string（如 `stream`） |
| `headers` | dict[str,str] | 固定请求头 |
| `api_key_env` | str \| null | 环境变量名；存在则注入 `Authorization: Bearer <value>` |
| `body` | dict | 固定请求体字段（`input_type`/`output_type`/`tweaks` 等） |
| `stream_response` | bool | `true` 时按 SSE 流式解析响应 |
| `timeout_seconds` | int | 单次请求超时 |
| `session_id` | str \| null | 可选，固定会话 ID。**不配时每次 run 自动生成随机 uuid**，保证外部 agent 每次评测都是全新会话（隔离记忆/缓存，避免复读上次回答污染结果）。想测持久会话（多轮上下文）才显式配置 |

`input_value` 与 `session_id` 由 executor 动态注入 body，不写死在 YAML。**注意：未显式配 `session_id` 时，`session_id` 是每次 run 随机生成的，不是写死的——目的是评测隔离。**

## 3. Executor 行为（HttpAgentExecutor）

三段式：

1. **create_session**：读取 `agent` 配置，校验 `endpoint` 非空，初始化会话状态（headers 注入 api_key、初始 session_id），返回 `ExecutorSession`。
2. **send_message(message)**：
   - body = `agent.body` 副本，注入 `input_value=message`；已有 session_id 则注入。
   - `POST endpoint`（params=`query_params`，headers，json=body，timeout）。
   - 非流式：解析 JSON → 从 `outputs` 提取文本 → 保存响应中的新 `session_id`（不解析工具调用）。
   - 流式：按 SSE 聚合文本增量，同时把 `tool_event` 解析为 `tool_calls`；GIS 工具产出（`tableName` / `bufferResult`）经 adapter 登记为不透明 handle，物理表名只留内部 locator 供 `result_*` 库内比对；非 GIS 产出原样保留。**流关闭 = 完成**。
   - 返回 `ExecutorStepResult(response=..., finished=True, need_interaction=False, tool_calls=<解析出的工具调用，无上报则为空>)`。
3. **close_session**：清理会话状态。

### finished / need_interaction 语义

- **finished**：每次 send_message 拿到完整响应即 `True`（一问一答没有中途完成态）。
- **need_interaction**：恒为 `False`。外部 agent 不遵守平台内部 `[NEED_INTERACTION]`/`[FINAL]` 协议。
- 多轮上下文通过 `session_id` 维持：每次响应更新，下次请求回传。

## 4. 文本提取规则（2026-08 真实联调后实测）

**非流式 JSON（stream=false）：**

真实结构为三层嵌套，最终回答在 `results.message.data.text`：

```
outputs[0].outputs[0].results.message.data.text
```

提取顺序：`results` → `text`/`message`/`content` → `data.text` → `outputs` → 受限遍历（跳过 `inputs`/`session_id` 等元数据，避免误提取用户输入回显）。

**SSE 流（stream=true）：**

真实格式**不是**标准 SSE，而是**每行一个完整 JSON**：

```json
{"event": "token", "data": {"chunk": "增量文本", "id": "..."}}
{"event": "add_message", "data": {"sender": "AI", "text": "...", ...}}
{"event": "tool_event", "data": {"event_type": "tool_start", "name": "...", "input": {...}}}
```

- 最终回答 = 所有 `event: token` 的 `data.chunk` 按序拼接。
- `add_message` 仅作兜底，且只取 AI 消息（避免拼入 User 输入回显）。
- 同时兼容标准 SSE 的 `data: {...}` 帧 + `[DONE]` 结束（mock 用此格式验证）。
- 流结束（连接关闭）即视为完成，无 `[DONE]` 标记。

提取逻辑集中在 `HttpAgentExecutor._extract_text` / `_parse_sse` 两处。

## 5. 断言可用性矩阵（agent_test 模式）

| 断言类型 | 可用性 | 原因 |
|---|---|---|
| `skill_loaded` / `skill_reference_loaded*` | ❌ 不可用 | 外部黑盒不上报技能加载 |
| `result_dataset_exists` / `result_geometry_type_in` | ❌ 不可用 | 依赖平台侧 mock `_dataset_store`，外部 agent 不会填充 |
| `tool_called` / `tool_sequence` / `tool_argument_equals` | ✅ 可用 | executor 把真实接口 SSE 的 `tool_event` 解析为 `tool_calls`；接口不上报时列表为空、断言判失败而非报错 |
| `final_response_contains` | ✅ 可用 | 基于最终文本响应 |

**黑盒信任边界**：所有"外部 agent 行为"断言都依赖对方自报，平台无法验证真实性。当前接入对象不上报工具调用，因此 agent_test 场景实际只能用 `final_response_contains` 这类文本断言。

> `final_response_contains` 期望值可写 `values`（列表，规范写法）或 `value`（单值，兼容）。两者都不配时断言直接判失败，避免空检查恒过。

## 6. scenario 示例

```yaml
# scenarios/agent_buffer_001.yml
id: agent_buffer_001
name: 外部智能体-缓冲区分析
version: "1.0.0"
type: agent_test
description: 对 SuperMap Workflow Studio 智能体做黑盒评测
target: {}
runtime:
  executor: http_agent
  agent_model: external-agent
  max_turns: 1
agent:
  type: http
  endpoint: http://<host>:8490/agentx/workflowstudio/api/v1/run/<flow_id>
  query_params:
    stream: "true"
  stream_response: true
  body:
    input_type: chat
    output_type: chat
data: {}
mcp:
  servers: []
  tools:
    required: []
    optional: []
user_task: 对道路要素做 500 米缓冲区分析
assertions:
  - type: final_response_contains
    values:
      - "缓冲区"
judge:
  enabled: false
pass_criteria:
  required_assertions_passed: true
  judge_score_min: 0.8
```

## 7. orchestrator 模式：本地 agent 多轮指挥外部 agent

> 对应迭代 1（见 `docs/plan/迭代1-orchestrator多轮指挥外部agent.md`）。与第 2~6 节的**直接一问一答**模式不同，本模式引入本地 agent（LangGraph ReAct）作为"操作者"。

### 7.1 行为差异

| 项 | 直接模式（`http_agent`） | orchestrator 模式（`orchestrator`） |
|---|---|---|
| user_task 语义 | 直接透传给外部 agent | 作为**目标**，由本地 agent 拆解 |
| 多轮 | 依赖外部 `session_id` 上下文，一问一答 | 本地 agent 自主发多条指令，读响应决定下一步 |
| 终止 | 每次请求返回即结束 | 本地 agent 发 `[FINAL]` 结束；`max_turns` 硬兜底 |
| 外部协议 | 无需遵守 `[NEED_INTERACTION]/[FINAL]` | 本地 agent 单方面发出 `[FINAL]`，外部 agent 仍无需遵守 |
| 配置 | `runtime.executor: http_agent` | `runtime.executor: orchestrator`，且需配真实 `agent_model` |

### 7.2 额外配置

- `runtime.executor: orchestrator`
- `runtime.agent_model`：**必填真实模型别名**（models.yaml），无启发式兜底；缺模型/缺 endpoint/缺 LangGraph 依赖 → 直接报错。
- `runtime.max_turns`：最多向外部 agent 发送的指令数；超限本地 agent 被强制以 `[FINAL]` 收尾。
- `agent.description`：外部 agent 能力说明，喂给本地 agent 决定发什么指令、何时算达成。

### 7.3 记录与断言

- 每轮 `指令/回答` 记入 `external_interactions`（报告 markdown 与前端展示）。
- 外部 agent 上报的 `tool_event` 仍解析为 `tool_calls`，`tool_called` / `tool_sequence` / `tool_argument_equals` 断言可用性不变（取决于外部是否上报）。
- `final_response` 为本地 agent 的 `[FINAL]` 总结，`final_response_contains` 对它判定。

示例：

```yaml
runtime:
  executor: orchestrator
  agent_model: deepseek-v4-flash
  max_turns: 5
agent:
  type: http
  endpoint: http://<host>:8490/agentx/workflowstudio/api/v1/run/<flow_id>
  query_params: { stream: "true" }
  stream_response: true
  body: { input_type: chat, output_type: chat }
  description: 能对 GIS 数据集执行查询、叠加求交等空间分析的智能体
user_task: 对北京市与中心城区求交
```

### 7.4 任务流配置（`agent.flow`）

orchestrator 的任务流（本地 agent 怎么指挥外部 agent）可通过 `agent.flow` 选择，默认 `react`，不写即用默认，**零回归**。

| 值 | 说明 |
|---|---|
| `react`（默认） | langgraph prebuilt ReAct：自由式循环，LLM 自主决定发指令/读响应/何时 `[FINAL]` |
| `scripted` | 内置固定节点 StateGraph：每轮 = 生成一条指令 → 发外部 → 判定完成；终止判定 = `max_turns`/超限规则硬判 + LLM 判完成，结构固定可复现 |
| 其它 | `orchestrator_flows.FLOW_REGISTRY` 里注册的自定义流程名 |

```yaml
agent:
  type: http
  endpoint: http://<host>:8490/agentx/workflowstudio/api/v1/run/<flow_id>
  stream_response: true
  flow: scripted          # 切到内置固定节点流程；省略/写 react 走默认
  description: 能对 GIS 数据集执行查询、叠加求交等空间分析的智能体
```

**自定义流程（注册表扩展口）**：在任意模块里手写一个 `builder(state) -> 可 invoke 的图` 并用 `register_flow` 注册，然后在 orchestrator_executor 侧 import 触发注册，scenario 按名引用：

```python
# my_custom_flow.py
from geoskillbench.executors.orchestrator_flows import register_flow, send_external_instruction

@register_flow("my_flow")
def build_my_flow(state):
    # 用 langgraph StateGraph 手写固定节点，可复用 send_external_instruction 发指令/记录
    # state: OrchestratorSessionState（含 request/http_session/max_turns/models_config）
    ...
```

```python
# orchestrator_executor.py 或你的入口处
import my_custom_flow  # 触发注册，使 "my_flow" 进入 FLOW_REGISTRY
```

```yaml
# scenario
agent:
  flow: my_flow
```

前置条件与 `react` 相同：`runtime.agent_model` 必须是 models.yaml 里的真实别名、`agent.endpoint` 必填、LangGraph 依赖就位。

### 7.5 与模拟用户自动多轮（react 流程）

orchestrator 场景默认是本地 agent 单方面指挥外部 agent。若希望本地 agent 缺信息时（外部 agent 反问、缺少数据集/参数）**向用户追问**，并由模拟用户自动回答，在 `agent` 块配置即可（模拟用户设定已并入 `agent`，无独立 `actor:` 块）：

```yaml
agent:
  flow: react        # 默认；v1 追问能力仅内置在 react 流程
  ask_user: true     # 开关：本地 agent 无法推断所需信息时输出 [NEED_INTERACTION] 问题（agent 会不会反问）
  user_enabled: true # 反问时模拟用户是否自动回答
  user_max_turns: 3  # agent↔模拟用户最多往返轮次
  user_goal: 使用 schools 数据对学校周边 500 米做缓冲区分析   # 模拟用户"确定知道"的信息，回答追问时从中提取
  user_model: rule-based-user   # 空/rule-based-* → 规则回答；真实模型别名 → LLM persona（失败降级规则）
```

> 历史注：迭代 3 时模拟用户配置曾是独立的 `runtime.actor_model` + `actor:` 块，反问闭环下沉重构后并入 `agent.user_*` 扁平字段（pydantic 会静默忽略残留的旧 `actor:` 块，等于没配）。迁移说明见 `../retrospective/反问闭环下沉重构.md`。

**协议**：`ask_user: true` 时，本地 agent 输出 `[NEED_INTERACTION] <问题>` → orchestrator executor 内部调用共享的 `UserSimulator`（规则 or LLM 双实现）按关键词（哪个数据/缓冲距离/输出格式/候选列表）从 `user_goal` 提取回答，作为下一轮消息回传本地 agent → agent 拿答案继续指挥 → 最终 `[FINAL]`。多轮对话在 session 内累积，agent 不丢上下文。（反问闭环已下沉到 executor 内部，runner 不再参与循环。）

**覆盖范围**：`[NEED_INTERACTION]` 识别仅对 **react 流程且 `ask_user: true`** 生效；非 react 流程（scripted / pipeline / keyword / 自定义 flow）v1 未内置追问节点，需要时由 flow 作者按同一前缀协议自行实现（final_response 以 `[NEED_INTERACTION]` 开头即可触发模拟用户回答）。

**注意**：`ask_user` 默认 `false`，存量场景行为不变。开启后本地 agent 在信息不足时可能多一轮追问往返，消耗 `user_max_turns` 的预算（不消耗 `runtime.max_turns` 的外部指令预算）。

**动态候选选择**（2026-08 扩展）：除反问具体问题外，agent 也可能**列出候选让用户选**——如"可用数据集有 schools_a、schools_b、rivers，用哪个？"。`UserSimulator` 识别"可选 / 候选 / 可用 / 可以选择 / 以下"引导的选项列表（支持顿号、中英文逗号、分号、竖线、'或'分隔，自动过滤问号尾巴），优先于关键词匹配处理：用 `user_goal` 的目标词对候选做匹配（精确 > 子串包含 > 取第一个），匹配不上取第一个候选兜底，保证对话不断。配合 `react + ask_user` 可覆盖"agent 搜索数据集/功能后让用户选择确认"的场景。

```yaml
agent:
  user_goal: 使用 schools 数据对学校周边 500 米做缓冲区分析
  # 目标词 schools 会在候选 [schools_a, schools_b, rivers] 中通过子串匹配命中 schools_a
```

### 7.6 external_driven：外部 agent 主导式评测（角色反转）

与 7.1~7.5 的"本地 agent 指挥外部 agent"相反，`external_driven` 让**外部 HTTP agent 成为被测对象与主导者**：它拿到 `user_task` 自主执行，缺必要信息（数据集名/缓冲距离/输出格式）时**主动反问**；平台侧共享的 `UserSimulator` 扮演**模拟用户+引导**，从 `agent.user_goal` 派生 persona 回答反问。

```yaml
runtime:
  executor: external_driven      # 新 executor
  agent_model: external-agent     # 占位：外部 agent 是黑盒 HTTP，不需要本地 agent 模型
  judge_model: deepseek-v4-flash  # 必须显式配真实 judge 模型，否则 JudgeEngine 跟随 agent_model 而误降级
  max_turns: 6                    # 硬兜底：平台→外部 agent 的消息总数上限（含 persona 回答/引导）
agent:
  type: http
  endpoint: http://<host>:8490/agentx/workflowstudio/api/v1/run/<flow_id>
  stream_response: true
  body: { input_type: chat, output_type: chat }
  # 模拟用户设定（与 orchestrator 同一套字段，由共享 UserSimulator 消费）
  user_enabled: true              # 默认 true
  user_profile: normal_user       # persona 人设
  user_goal: 使用 schools 数据对学校周边 500 米做缓冲区分析，输出 GeoJSON   # LLM persona 来源
  user_model: deepseek-v4-flash   # 真实模型别名 → LLM persona；空/rule-based-* → 规则降级回答
user_task: 对学校周边做缓冲区分析，把结果给我   # 刻意含糊，考验外部 agent 是否反问
judge:
  enabled: true
  penalize_no_ask_back: true      # 外部 agent 缺必要信息不反问、自行猜测执行 → 连续扣分（即使结果对）
```

**行为**：
- 会话内多轮在 executor 内部闭环：外部回复命中 `complete`（askback 规则）或 `runtime.max_turns` 耗尽 → 终止；命中 `ask`（反问）→ UserSimulator 回答；`continue`（进展）→ UserSimulator 给一句引导。
- `need_interaction` 恒 `False`（runner 无 actor 循环）；每轮交互写入 `external_interactions`（随 `TestResult.final_output` 输出，报告/前端可见）。
- **`max_turns` 语义**：对 `external_driven`，它是平台→外部 agent 的**消息总数**（含 persona 回答/引导），不是 runner 循环轮数（runner 只走 1 轮）。

**评测维度（`judge.penalize_no_ask_back`，默认 false 零回归）**：外部 agent 缺必要信息不反问自行猜测执行，即使最终结果对也扣分。LLM judge 主判（喂 `external_interactions` + rubric 追加反问维度），LLM 不可用时规则镜像兜底（`judge_runtime` 检查所有外部回复均非反问 → `-0.2`）。判定与 executor 分类共用 `geoskillbench/runtime/askback.py`，单一事实源不漂移。

**前置条件**：外部 agent 需支持 `session_id` 回传维持多轮上下文（HttpAgentExecutor 已处理），否则一轮即回、靠 `max_turns` 收尾；mock 反问见 `examples/agent_external/agent_server.py` 的 `build_askback_reply`。