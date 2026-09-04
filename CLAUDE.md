# GeoSkillBench 项目协作规则

## 1. 开始工作前先理解文档

处理本项目任何需求前，先阅读与任务相关的 `docs/` 文档；不要只看代码或文件名猜测设计。

推荐阅读顺序：

1. `docs/README.md`：文档索引、活文档/历史文档边界和当前状态。
2. `docs/design/00-系统总体设计.md`：系统架构、核心概念和模块边界。
3. `docs/guide/Scenario配置指南.md`：Scenario 字段、Executor、MCP、模拟用户和断言写法。
4. 与当前任务直接相关的 `docs/design/` 活文档。
5. 与当前迭代相关的 `docs/plan/` 和 `docs/retrospective/`。

阅读文档时遵守：

- `docs/design/`、`docs/guide/` 是随代码演进维护的活文档，应优先参考。
- `docs/plan/` 是计划，不能当作已经实现的功能。
- `docs/retrospective/` 是历史复盘，不能直接当作当前代码行为。
- 文档与代码冲突时，先指出冲突；描述当前行为以代码为准，并判断是否需要同步活文档。
- `docs/plan/未来迭代路线图-可信评测与GIS差异化.md` 是路线脑暴，不等于已排期或已实现。

## 2. 项目定位

GeoSkillBench 是 GIS Agent Skill / 外部 GIS Agent 的自动化评测平台，不是普通聊天应用。

核心链路：

```text
Scenario
→ Fixture / Ground Truth
→ MCP Tool Adapter
→ Skill / Executor
→ Execution Recorder
→ Assertion Engine
→ 可选 Judge / Evaluation Analyst
→ Report
```

核心概念边界：

- **Scenario**：定义测试任务、数据、工具、被测对象、预期行为和断言。
- **Skill**：指导 Agent 如何完成 GIS 任务，不直接执行 GIS 操作。
- **MCP Tool**：执行真实 GIS 操作。
- **Executor**：适配不同被测对象和执行模式。
- **UserSimulator**：模拟用户回答 Agent 的追问；当前反问闭环下沉到 Executor 内部。
- **Assertion**：确定性、可复现的事实校验，优先于主观判断。
- **Judge**：不能未经明确设计就替代硬断言或决定事实标准。单次主观 Judge 应视为辅助能力；未来重点是多轮运行后的稳定性、过程一致性、失败归因和改进建议。

## 3. 当前迭代边界

- 迭代 5A 已收尾，核心目标是防止“失败被判为通过”，并建立最小测试基线。
- 迭代 5B（MCP 全面服务化与 DB 数据面）平台侧已落地，真机联调仍待完成。
- 迭代 6（批量重复运行与评测标定）已完成；6.1 批次横向 AI 诊断已完成。诊断是辅助分析，不得改写正式 verdict / Skill 文件。活设计见 `docs/design/05-批次AI诊断.md`，复盘接在 `docs/retrospective/迭代6复盘.md` 附录。
- 迭代 7 第一刀进行中：现有 `result_*` 的 PostGIS 库内比对与结构化输出。不加 `spatial_relation` 等新断言、不改正式 verdict 语义。计划见 `docs/plan/迭代7-GIS确定性评测增强.md`。路线图中迭代 7 其余项与 8 及以后不要提前实现。

## 4. 评测设计原则

1. 能用确定性代码判断的内容，不交给 LLM Judge。
2. 正式通过/失败必须有明确、可解释、可复现的依据。
3. Agent 执行状态、硬断言结果和辅助分析结果分层保存。
4. 评测结果优先检查真实 GIS 产物、工具调用轨迹和参数，不要只相信最终文字。
5. 空 assertions 不得被当作无条件满分。
6. Judge 的建议不能自动修改断言、Skill、Agent 或容差；必须保留证据并经过人工确认、修改后重新验证。
7. 任何新增指标都要说明：数据来源、计算方式、容差、是否可复现，以及它是否影响正式 verdict。
8. 不要为了提高通过率放宽标准；先判断是 Agent、Skill、工具、断言还是平台的问题。

## 5. 修改代码前的要求

- 先定位实际调用链和现有测试，再决定修改范围。
- 优先做最小改动，避免把多个迭代主题混在一次修改中。
- 保持现有 Scenario、Executor、MCP、Assertion、API 和报告兼容性，除非用户明确批准破坏性变更。
- 新增字段优先向后兼容；修改状态语义时必须检查 Runner、Task/SSE、报告和前端读取方。
- 涉及 GIS 结果时，优先使用真实几何、CRS、属性和工具记录验证，不编造标准答案。
- 代码注释和用户说明默认使用中文；代码、变量名、命令使用英文。
- 修改后至少运行与改动相关的测试、Python 编译检查和 Scenario 配置校验；不要声称未实际执行的验证已经通过。

## 6. 文档维护

- 新设计放 `docs/design/`，并保持编号和索引同步。
- 迭代计划放 `docs/plan/`，完成后在 `docs/retrospective/` 写复盘。
- 不要把“计划中的功能”写成“当前已支持”。
- 若代码行为发生改变，优先同步对应活文档，并在必要时补充变更记录。
- 脑暴结论、正式立项、实现完成和复盘结论要明确区分。

## 7. 运行与安全约束

- 默认使用项目 `.venv` 的 Python。
- 安装 Python 依赖优先使用清华 PyPI 镜像：
  `-i https://pypi.tuna.tsinghua.edu.cn/simple`
- 不执行真实 LLM、云端 MCP、共享 PostGIS 或会覆盖既有报告的运行，除非用户明确要求。
- 不在报告、数据库、SSE 或普通日志中写入 API key、Bearer token、密码、完整 DB URL 等明文 secret。
- 任何删除文件、修改环境变量/密钥、数据库迁移、CI/CD 修改、git push 或重写历史，先向用户确认。

## 8. 沟通方式

- 默认中文，结论先行，少绕弯子。
- 需求有多种合理解释时，先给出推荐方案和影响，再询问是否调整。
- 发现范围膨胀、文档过时、设计与代码不一致或验证不足时，直接说明，不用“功能已完成”掩盖未完成部分。
