# MCP 服务化数据协议 v1

> 状态：5B 实施基线（2026-08-28）。本文冻结平台与数据服务之间的边界；具体云端部署、PostGIS 物理 schema 和迁移由数据服务负责。

## 1. 目标与边界

5B 把平台从“客户端传本地路径、MCP 返回服务端路径”切换为网络协议：平台只处理服务端签发的 opaque handle，不能传入或解析物理表名、文件路径、数据库 URL。

协议分为两层：

- **MCP 层**：标准 `tools/list`、`tools/call`，用于发现和调用 GIS/data 工具。
- **数据控制层**：HTTPS 管理 run、授权数据、evaluation、归档和释放；控制层不是 MCP tool 的替代实现。

## 2. 身份与请求上下文

平台控制面、Agent MCP 调用和 Evaluation 比对使用独立的服务身份。凭证只能由环境变量、工作负载身份或外部密钥系统注入，禁止写入 Scenario、工具参数、报告和普通日志。

每个请求必须携带协议版本，并关联平台生成的 `run_id`。MCP 服务应从认证上下文或受控 header 获取 run，而不是相信工具参数中的任意 run ID。公共日志可以记录 run、server、tool 和 handle 的摘要，但不得记录 token、连接串、物理表/路径。

## 3. Run 控制面

以下路径是建议的 v1 控制面，实际部署可使用等价路径，但字段和语义必须保持一致：

### `POST /admin/runs`

请求：

```json
{
  "protocol_version": "geoskillbench-data/v1",
  "run_id": "run_abc",
  "scenario_id": "buffer_school_500m_001",
  "inputs": ["schools-v1"],
  "references": ["schools-buffer-500m-reference-v1"],
  "idempotency_key": "run_abc"
}
```

响应包含 `run_id`、有效期、Agent 可见 `inputs` 和 Evaluation-only `references`。重复请求必须幂等返回同一 run；同一幂等键对应不同参数时返回 `409 conflict`。

### `GET /datasets/{handle}`

只接受 opaque handle，返回不含物理位置的安全元数据。禁止通过 URL 或 JSON 传入 `schema.table`、文件路径或连接串。

### `GET /datasets/{handle}/evaluation`

只对 Evaluation 身份开放，返回受控 GeoJSON/GeoParquet 数据流或等价的服务端比较结果。Agent 身份、common 搜索和 MCP 工具不得调用 reference 的 evaluation 接口。

### `POST /admin/runs/{run_id}/archive`

在 release 前执行，归档允许保存的结果证据和 manifest，返回 `manifest_id`，不返回裸文件系统路径。归档失败必须返回结构化错误，平台记录 `archive_status=failed`。

### `DELETE /admin/runs/{run_id}`

释放 run 的临时结果和授权 handle。重复释放、已释放或已过期请求必须幂等返回 `already_released` 或 `expired`。客户端崩溃时由服务端 TTL 清理 orphan run。

建议错误码：`unauthorized`、`forbidden`、`not_found`、`conflict`、`handle_expired`、`handle_cross_run`、`invalid_handle`、`archive_failed`、`release_failed`。

## 4. Handle

服务端签发的 descriptor 至少包含：

```json
{
  "protocol_version": "geoskillbench-data/v1",
  "handle": "dh_opaque_value",
  "alias": "schools",
  "role": "input",
  "run_id": "run_abc",
  "geometry_type": "Point",
  "crs": "EPSG:4326",
  "feature_count": 42,
  "fields": ["school_id", "geometry"],
  "expires_at": "2026-08-28T12:00:00Z"
}
```

`role` 取 `input`、`result`、`reference`：

| 角色 | Agent 可见 | 平台可用 | Evaluation 可用 |
|---|---:|---:|---:|
| input | read/调用工具 | inspect/release | compare |
| result | 当前 run 内由工具返回 | inspect/archive/release | compare |
| reference | 否 | 不向 Agent 转发 | compare/export（按权限） |

handle 必须绑定 run、角色、权限和过期时间。Agent 不得读取 reference、其他 run 或未发布数据；平台也不得凭空构造 handle。descriptor 中禁止 `path`、`table`、`schema`、`db_url`、`server_side_path` 等字段。

## 5. MCP 工具

正式 Scenario 仅允许：

- `sse`：MCP SSE transport；
- `http`：MCP Streamable HTTP transport。

`http` 不得复用 SSE client。工具发现使用 `tools/list`，工具调用使用 `tools/call`。数据型工具的结果必须返回 `role=result` 的 descriptor；非数据型工具可以返回普通 JSON/text。

数据搜索工具（如 `search_datasets`、`get_dataset_metadata`）由 common server 自己维护已发布目录。搜索结果只能包含 Agent 有权使用的 input descriptor；reference、其他 run 结果、系统表和未发布数据必须在服务端过滤。搜索排序应稳定，并返回数据版本/内容 hash 以便复现。

## 6. 结果与比较

GIS 工具结果使用：

```json
{
  "dataset": {
    "handle": "dh_result",
    "alias": "buffer_result",
    "role": "result",
    "run_id": "run_abc",
    "geometry_type": "Polygon",
    "crs": "EPSG:4326",
    "feature_count": 42,
    "fields": ["school_id", "geometry"]
  }
}
```

平台按 alias 建立当前 run 的 result registry，但后续 inspect/compare/export/release 只能使用 handle。结果缺 handle、角色错误、跨 run、已过期或含物理位置字段时视为协议错误，不能当作可通过结果。

断言引擎使用 Evaluation 身份读取 result 和 reference。reference 永远只来自 `data.reference` 的受控授权，不得从 Agent input registry 回退获取。

## 7. 隔离、归档与清理

服务端负责决定独立 schema、run 前缀、RLS 或其他物理实现；这些细节不进入平台契约。并发 run 即使使用相同 alias，也必须互不可读写、互不覆盖。平台顺序为：

```text
register → grant input/reference → MCP execution → evaluation compare → archive → release
```

MCP 连接和 Executor session 关闭后执行 release；连接、Agent、断言、Judge 或报告异常都不能跳过 release。客户端有限重试，服务端 TTL 是最终兜底。平台分别记录 execution/assertion/judge/archive/cleanup 状态。

## 8. 可追溯性与安全

报告可保存 run ID、alias、角色、版本、内容 hash、feature count、工具调用和 manifest ID，但不得保存 token、密码、DB URL、物理表名、服务端临时路径或 reference 到 Agent 的泄露内容。每次比较应能追溯输入/结果/reference 的 handle 和版本；服务端应审计授权、读取、导出、释放及拒绝原因。
