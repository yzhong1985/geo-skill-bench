# 迭代 5B 计划：MCP 全面服务化与共享 PostGIS 数据面

>状态：**已立项，协议待冻结**。
>
>前置：迭代 5A 已完成“防止失败被判为通过”的最小可信基线。5B 只解决网络 MCP 与共享 PostGIS 数据闭环；不纳入批量重复运行、统计聚合或 Judge 横向分析。
>
>范围边界：平台侧（`geoskillbench/`）与平台—云端数据服务协议；云上 MCP 服务和 PostGIS 的具体部署由用户负责。

---

## 1. 目标

把当前“本地文件随运行传递、本地 mock/stdio MCP、服务端返回本机文件路径”的同机模式，改成：

```text
网络 MCP
+ 共享 PostGIS
+ 受控数据 handle
+ run 隔离结果
+ 结果断言、归档、清理闭环
```

正式运行不再依赖：

- `transport: mock` / `stdio`；
- `GEO_MCP_DATASETS` 环境变量注入；
- MCP server 返回本机文件路径并由 client 复制；
- client 把 `db_table` 拉成临时文件后再给 MCP 使用；
- 平台、Agent 或 Scenario 直接操作任意物理数据库表名。

## 2. 已确认的关键决策

| 主题 | 决策 |
|---|---|
| MCP transport | **硬切**：正式 Scenario 只支持 `sse` / `http`；`mock` / `stdio` 校验直接报错，不设兼容窗口。 |
| 数据面 | 数据类 Scenario 统一使用共享 PostGIS；本地文件不再是正式运行时数据通道。 |
| 数据标识 | 平台和 Agent 后续操作服务端签发的**不透明 handle**，不接受任意物理表名。 |
| 数据发现 | Skill 可声明通用数据搜索/元数据/GIS 工具；Agent 可通过 common MCP 数据服务自主搜索和选择源数据。 |
| fixture | 不再是每个 Scenario 必须直接交给 Agent 的输入。固定输入评测保留 fixture；自主发现评测可以没有 fixture。 |
| reference | 仍是平台私有的 ground truth，只供断言引擎比对，Agent 和 common 搜索服务不可见。 |
| 选错源数据 | 多数端到端任务不强制“必须选某一物理数据集”；Agent 最终结果与 reference 不符即失败。 |
| Judge | 5B 不做多轮 Judge 横向分析、稳定性评测或自动建议；这些依赖 batch/repeat/aggregation，留给后续迭代。 |

## 3. 目标架构

### 3.1 数据分层

```text
Common MCP Data Server
├── 已发布数据目录
│   └── Agent 可以按关键词、字段、空间条件等自主发现和使用
├── 私有评测数据
│   └── reference / ground truth；Agent 不可搜索、不可读取
└── run 隔离结果
    └── 服务端生成，返回受控 result handle，运行结束后归档并释放
```

“已发布数据目录”是 common 数据服务自身维护的运行配置，不要求每个 Scenario 逐项列出候选数据。它必须排除：reference、其他 run 的临时结果、系统表、未发布数据及无权限数据。

### 3.2 受控 handle

Scenario 使用逻辑业务标识或用户任务描述；真实表名只存在于数据服务内部。

```text
Agent 调 search_datasets("学校")
  → common 数据服务从已发布目录中检索
  → 返回输入数据 handle
  → Agent 用输入 handle 调 GIS MCP 工具
  → 服务端创建 run 隔离结果
  → 返回 result handle
  → 平台用 result handle 做 inspect / compare / export / release
```

示例：

```json
{
  "handle": "ds_7f3a...",
  "run_id": "run_abc",
  "alias": "buffer_result",
  "geometry_type": "Polygon",
  "srid": 3857,
  "feature_count": 42
}
```

handle 至少绑定：`run_id`、数据角色（input/reference/result）、权限（read/compare/export/release）与失效策略。服务端拒绝平台或 Agent 传入任意 `schema.table`。

### 3.3 两类评测数据入口

#### 固定输入型

目标：单独评测 GIS 操作、参数、CRS 和结果生成。

```text
平台直接提供输入 handle
→ Agent 使用该 handle 执行
→ result handle 与私有 reference 比对
```

这类 Scenario 可保留 `data.fixtures`，但它的语义是“直接授权给 Agent 的输入”，不是数据库表名。

#### 自主发现型

目标：评测任务理解、数据发现、数据选择和 GIS 操作的端到端能力。

```text
Agent 调 common MCP 搜索工具
→ 自行检查候选数据元信息
→ 选择输入 handle
→ 执行 GIS 操作
→ 最终结果与私有 reference 比对
```

这类 Scenario 可以没有 fixture。平台不预先告诉 Agent 应选哪张数据；只通过最终业务结果的确定性断言判断成功与否。

## 4. 运行闭环

```text
平台创建 run
  ↓
向数据服务注册 run 身份和权限上下文
  ↓
Agent 使用 common MCP 搜索或接收固定输入 handle
  ↓
Agent 调用网络 GIS MCP 工具
  ↓
服务端在 run 隔离空间写入结果数据
  ↓
返回 result handle
  ↓
断言引擎通过受限 evaluation 权限读取 result/reference 并比较
  ↓
导出允许归档的结果证据
  ↓
release handle / 清理 run 隔离数据
```

## 5. 需先冻结的协议

实现代码前，新建 `docs/design/04-MCP服务化数据协议.md`，并至少冻结以下内容。

1. **认证与身份**：平台如何认证数据服务；Agent 调用是否通过平台代理；run 身份如何携带。
2. **数据发现**：common MCP 搜索工具名、请求字段、搜索范围、排序、返回的安全元数据与 handle 格式。
3. **handle 生命周期**：输入、reference、result handle 的创建方、绑定字段、权限、过期和失效错误。
4. **run 注册**：注册/幂等/冲突/重试/取消的 HTTP 控制面协议；它不属于 MCP 标准 `tools/list` / `tools/call`。
5. **结果协议**：GIS 工具返回数据集 handle 的固定 JSON 结构、错误码和 alias 规则。
6. **隔离**：服务端如何隔离并发 run 的结果（独立 schema 或服务端内部命名均可，物理实现不暴露给平台）。
7. **断言访问**：断言引擎如何获得仅用于 compare 的 result/reference 权限，reference 不得经 Agent 可用工具暴露。
8. **归档与清理**：谁 export、谁 release、失败如何表达、TTL 如何兜底、重复清理是否幂等。
9. **可观测性**：run_id、handle、tool_call 和导出证据如何关联；公共日志不得泄露真实表名或密钥。

## 6. 平台改动面

| 模块 | 5B 改动 |
|---|---|
| `models/scenario.py` | transport 收缩为 `sse/http`；更新 fixture 为 direct input 可选语义；reference 标记 evaluation-only。 |
| `mcp/mcp_tool_adapter.py` | 删除 mock/stdio、env 注入和本机路径复制；支持网络 MCP 的 handle 输入/输出和受控结果登记。 |
| `fixtures/fixture_manager.py` | 不再把本地文件作为正式数据通道；固定输入改为申请/接收数据服务 handle。 |
| `assertions/result_comparator.py` | 通过 evaluation 权限按 handle 读取 result/reference，不能传任意表名。 |
| `runner.py` | 创建/结束 run 时调用数据控制面注册、归档、release；记录 handle 与清理状态。 |
| `scenarios/*.yml` | 迁移正式 Scenario 的 transport 与数据语义；固定输入和自主发现型分开表达。 |
| `docs/design/04-*` | 新增并冻结数据控制协议。 |
| 活文档 | 同步 `docs/design/00-*`、`docs/guide/Scenario配置指南.md`、`README.md`。 |

## 7. 不在 5B 范围内

- repeat、batch、聚合统计；
- 多轮 Judge 稳定性、过程一致性或自动改进建议；
- Agent / Skill 自动修改；
- 完整 Task Center（持久任务、取消、重试、队列）；
- 大规模新增 GIS 断言类型、地图审阅或 Dashboard；
- 对云端 MCP 服务部署方式的改造。

## 8. 验收标准

- [ ] 正式 Scenario 无 `mock` / `stdio`；旧 transport 校验直接失败。
- [ ] 平台通过网络 MCP 完成真实数据发现或固定输入的 GIS 执行。
- [ ] Agent、平台和 Scenario 都不能传任意物理表名进行读写或删除。
- [ ] common 搜索服务不能返回 reference、其他 run 结果或未发布数据。
- [ ] 服务端返回并校验受控 handle；越权、过期或跨 run handle 有明确拒绝错误。
- [ ] result_* 断言可通过受限 evaluation 路径比较 result 和 reference。
- [ ] 并发两个 run 的结果互不覆盖、互不可读写。
- [ ] 正常与异常结束均尽力 export 允许归档的证据并 release 临时结果；重复 release 安全。
- [ ] 协议文档、平台测试和至少一个真机纵向场景闭环完成。

## 9. 执行顺序

1. 先写并确认 `docs/design/04-MCP服务化数据协议.md`。
2. 与数据服务对齐 common 搜索、run 注册、handle、结果和 release 的契约并做最小连通验证。
3. 收紧 Scenario transport 与数据模型。
4. 改造 adapter、fixture、runner 和结果断言的 handle 流程。
5. 先迁移一个固定输入型纵向场景，完成真机闭环。
6. 验证并发隔离、错误、归档和清理。
7. 再迁移剩余正式 Scenario；自主发现型 Scenario 在 common 搜索协议稳定后单独添加。
8. 同步活文档并写复盘。

## 10. 执行前提

- 用户提供已部署的云端 MCP 服务与共享 PostGIS。
- 数据服务存在“已发布可检索数据”与“私有评测数据”的访问边界。
- 数据服务能实现协议冻结后的 run 注册、handle 校验、结果隔离和 release 能力。
- 真实数据、reference 数据的版本和来源可追溯。
