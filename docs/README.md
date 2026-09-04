# GeoSkillBench 文档索引

> 目录约定（2026-08-24 起）：`design/` 与 `guide/` 是**活文档**，随代码演进维护；`plan/` 与 `retrospective/` 是**历史存档**，保留写作时原貌（仅修路径引用与加状态横幅），不随代码更新。新增设计文档放 `design/` 并按序号前缀命名，新迭代完成后在 `plan/` 留计划、`retrospective/` 留复盘。

## 按阅读顺序

### 入门

1. [design/00-系统总体设计.md](design/00-系统总体设计.md) — 核心测试引擎 + 前端控制台完整设计（v0.7，含修订记录）。想理解"Scenario / Skill / Executor / 模拟用户 / Judge / Assertion 是什么、怎么串起来"，从这开始。
2. [guide/Scenario配置指南.md](guide/Scenario配置指南.md) — 写场景 YAML 的操作手册：字段参考、executor 选择、断言类型、完整示例。**动手写场景前必读。**
3. [reference/项目概览.md](reference/项目概览.md) — 自动生成的代码库导览（⚠️ 内容滞后于早期 MVP，作架构讲解材料，现状以代码为准）。

### 设计（活文档，随代码演进）

| 文档 | 内容 |
|---|---|
| [design/00-系统总体设计.md](design/00-系统总体设计.md) | 总体架构、模块设计、断言类型（含 result_* 结果内容断言）、报告结构、前端控制台 |
| [design/01-Agent接入契约.md](design/01-Agent接入契约.md) | 外部智能体 HTTP 接入契约：协议、scenario 字段、executor 行为、orchestrator / external_driven / 模拟用户反问闭环 |
| [design/02-Executor架构与Nanobot接入.md](design/02-Executor架构与Nanobot接入.md) | Executor 定位、会话接口抽象、与 Runner/Judge/Assertion 的边界、nanobot 接入分析 |
| [design/03-SkillPackage按需加载.md](design/03-SkillPackage按需加载.md) | Skill 包目录规范、`load_skill_reference` 工具、相关断言与 API |
| [design/04-MCP服务化数据协议.md](design/04-MCP服务化数据协议.md) | 5B 网络 MCP、opaque handle、run 控制面、evaluation 与清理协议 |
| [design/05-批次AI诊断.md](design/05-批次AI诊断.md) | 迭代 6.1 横向 AI 诊断：归因枚举、Patch 门控、analyze/diagnostics API、前端 Batch 面板 |

### 操作指南

| 文档 | 内容 |
|---|---|
| [guide/Scenario配置指南.md](guide/Scenario配置指南.md) | 场景 YAML 全字段参考 + 写作指南 + 校验运行命令 |
| [../README.md](../README.md) | 启动方式（本地脚本 / Docker）、API 清单、模型配置 |

### 参考

| 文档 | 内容 |
|---|---|
| [reference/openapi-workflow-studio.yaml](reference/openapi-workflow-studio.yaml) | SuperMap Workflow Studio (Agentx Server) API 规范——外部 agent 接入的对接依据 |
| [reference/项目概览.md](reference/项目概览.md) | 代码库分模块导览（滞后声明见文内） |

## 历史存档

演进时间线（旧→新）：

```
MVP 闭环 → 阶段1 外部智能体黑盒接入 → 阶段2 报告 DB 持久化
        → 迭代1 orchestrator 多轮指挥 → 迭代2 LLM Judge
        → 迭代3 模拟用户 actor 自动多轮 → （迭代间）反问闭环下沉重构
        → 迭代4 云端 MCP 工具接入（真 MCP 客户端 + fail-fast）
        → 工程批次：结果内容断言 result_* / Docker 部署 / 前端场景管理 / 历史自动清理
        → 迭代5A 可信评测基线 → 迭代5B MCP 服务化
        → 迭代6 批量重复运行 → 迭代6.1 批次横向 AI 诊断
        → 迭代7 GIS 确定性评测增强（第一刀：result_* 库内比对）
```

### 计划（plan/）

| 迭代 | 计划 | 复盘 |
|---|---|---|
| 路线图 | [Evaluation平台改造路线图.md](plan/Evaluation平台改造路线图.md)（阶段一~三规划，部分完成） | — |
| 阶段 1 | — | [阶段1-外部智能体黑盒接入.md](retrospective/阶段1-外部智能体黑盒接入.md) |
| 迭代 1 | [迭代1-orchestrator多轮指挥外部agent.md](plan/迭代1-orchestrator多轮指挥外部agent.md) | [迭代1-orchestrator多轮指挥外部agent.md](retrospective/迭代1-orchestrator多轮指挥外部agent.md) |
| 迭代 2 | [迭代2-LLMJudge.md](plan/迭代2-LLMJudge.md) | [迭代2-LLMJudge.md](retrospective/迭代2-LLMJudge.md) |
| 迭代 3 | —（基于迭代 1 基座展开，无独立计划） | [迭代3-模拟用户actor自动多轮.md](retrospective/迭代3-模拟用户actor自动多轮.md)（后续被反问闭环下沉重构取代，见文内注） |
| 重构（迭代间） | — | [反问闭环下沉重构.md](retrospective/反问闭环下沉重构.md)（ActorRuntime/AgentRuntime → UserSimulator） |
| 迭代 4 | [迭代4-云端MCP工具接入.md](plan/迭代4-云端MCP工具接入.md) | [迭代4-云端MCP工具接入.md](retrospective/迭代4-云端MCP工具接入.md) |
| 迭代 5A | [迭代5A-可信评测基线.md](plan/迭代5A-可信评测基线.md)（已完成） | [迭代5A复盘.md](retrospective/迭代5A复盘.md) |
| 迭代 5B | [迭代5B-MCP全面服务化与DB数据面.md](plan/迭代5B-MCP全面服务化与DB数据面.md)（平台侧完成，待真机联调） | [迭代5B复盘.md](retrospective/迭代5B复盘.md) |
| 迭代 6 | [迭代6-批量重复运行与评测标定.md](plan/迭代6-批量重复运行与评测标定.md)（已完成） | [迭代6复盘.md](retrospective/迭代6复盘.md) |
| 迭代 6.1 | [迭代6.1-多维度AI诊断与标定引擎.md](plan/迭代6.1-多维度AI诊断与标定引擎.md)（已完成） | 接在 [迭代6复盘.md](retrospective/迭代6复盘.md) 附录 |
| 迭代 7 | [迭代7-GIS确定性评测增强.md](plan/迭代7-GIS确定性评测增强.md)（第一刀进行中：result_* 库内比对） | — |

> 编号说明：云端 MCP 接入立项时曾称"迭代 3"，因与已有的"迭代 3（模拟用户 actor 多轮）"冲突，整理文档时重编号为迭代 4。

### 待定企划池

[plan/企划池.md](plan/企划池.md) — 有价值但近期不排期的特性想法集中管理（批量重复运行聚合、报告地图可视化、spatial_relation 断言、forbidden_behavior、judge 结构化输出、反问协议结构化、nanobot 真实运行时、云端数据面、前端表单完善）。启动某项时从池中提拔为独立迭代计划。

## 待办

- [ ] 真机验证收尾：LLM judge 质量评估（deepseek-v4-flash 真机 rubric 合理性）、LLM persona 模拟用户质量、external_driven 真机联调
  - 其中 LLM judge 质量评估 + harness 方差量化已立项为"真机标定批次"，**现降级待定**：[../plan/待定企划-真机标定批次-方差量化与Judge质量.md](plan/待定企划-真机标定批次-方差量化与Judge质量.md)；模拟用户规则 vs persona 对比在其批次 A 变量矩阵内一并观察
- [x] ~~harness 方差量化~~（并入迭代 5）
- [ ] http transport MCP server 真机验证

已移入[企划池](plan/企划池.md)（有价值但近期不排期）：云端数据面打通（#8）、spatial_relation / forbidden_behavior 断言落地（#3/#4）；`field_exists` 经评估价值极小，建议从规划中剔除（design/00 §10 已标注未实现）。
