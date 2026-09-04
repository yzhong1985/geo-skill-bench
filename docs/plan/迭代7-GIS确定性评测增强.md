# 迭代 7 计划：GIS 确定性评测增强

> 状态：**进行中（2026-09-02）**。第一刀：现有 `result_*` 的 PostGIS 库内比对与结构化输出。
>
> 来源：[未来迭代路线图-可信评测与GIS差异化.md](未来迭代路线图-可信评测与GIS差异化.md) §7。活设计同步 [../design/00-系统总体设计.md](../design/00-系统总体设计.md) §10.8。
>
> 定位：把结果内容断言从“文件后端能算、库内半成品”补成两条等价路径。正式 verdict 仍只看硬断言；本迭代不加 LLM。

---

## 1. 第一刀目标（本轮）

现有 `PostgisResultComparator` 不能当评测依据：

- `result_fields_match` 库内直接 `Unsupported`；
- overlap / area / Hausdorff 按 `smid` JOIN 后 `LIMIT 1`，只看一行，和文件后端的并集语义不一致；
- 面积/距离未投到米制 UTM，4326 下会用平方度；
- 断言项只有 `message`，报告无法区分走了文件还是 PostGIS。

本轮做完：

1. 库内覆盖与文件后端相同的五个指标：`overlap_ratio` / `area_error` / `hausdorff_distance` / `fields` / `feature_count`。
2. 几何指标用 **ST_Union 全集**，不再 `smid LIMIT 1`。
3. 面积/距离先按参考中心点选 UTM 带再算，单位是米。
4. `AssertionItemResult` 增加 `actual` / `expected` / `backend`（`file` | `postgis`）；报告 Markdown 标出后端。
5. 仅当结果表和参考表都能解析到合法标识符、且 `GEO_EVAL_DATABASE_URL` 可用时走库内；否则有本地 path 走文件，否则失败。
6. 测试 mock 引擎，不连真实 PostGIS、不跑 LLM。

## 2. 明确不做（本刀）

- 新断言：`spatial_relation` / `geometry_valid` / `crs_matches` / `no_empty_geometry` / `result_feature_match`
- 一对一要素匹配、ground truth provenance
- 地图证据（迭代 8）、DB schema 变更、真机联调
- 自动写 Skill / 改正式 verdict 语义

## 3. 库内语义

| 指标 | SQL 要点 |
|---|---|
| overlap | `ST_Area(ST_Intersection(res_union, ref_union)) / ST_Area(ref_union)`，UTM |
| area_error | `abs(ST_Area(res_union) - ST_Area(ref_union)) / ST_Area(ref_union)`，UTM |
| Hausdorff | 双向 `ST_HausdorffDistance` 取 max，UTM |
| fields | 列名去掉几何列；`contains` / `exact` |
| feature_count | `COUNT(*)` |

与文件后端的差异：库内**始终**做数据集并集，不按要素数 50 切到 centroid 最近邻。PostGIS `ST_Union` 能扛大表；centroid 一对多匹配本身是文件路径的性能妥协，不复制到库内。

结果表名来自 adapter 内部 `result_locator`（不进报告）；参考表名来自 fixture `evaluation_id` / `catalog_id` 写入的 `metadata.logical_id`。data service 注册覆盖 descriptor 时保留该 logical_id，否则 5B 场景会丢表名。物理表名不得写入 Agent 可见结果或报告正文。

## 4. 验收

- [x] 五个 `result_*` 库内均有实现，`fields` 不再 Unsupported
- [x] 几何 SQL 不含 `smid` / `LIMIT 1`
- [x] 库内断言项带 `backend=postgis` 与 actual/expected
- [x] 有 path、无表名时仍走文件后端
- [x] mock 测试覆盖 overlap/area/hausdorff/fields/count 与路由；不连真实库

第一刀代码已落地（2026-09-02）。

第二刀（同日）：外部 agent 模式可配 `result_*`。HTTP `tool_event` 的 GIS 产出登记进 adapter locator；`data.reference` 用 `evaluation_id`，不必配输入文件。未做：真机联调、`spatial_relation` 等新断言。
