from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any, Callable

import geopandas as gpd
from shapely import hausdorff_distance

from geoskillbench.models.test_context import DatasetContext


@dataclass
class CompareResult:
    passed: bool
    actual: Any
    expected: Any
    detail: str


class ResultComparator:
    """结果内容比对：本地 GeoPandas/Shapely 后端（方案 A，默认主路径）。

    面积/距离指标先对齐到"参考数据集中心点选定的 UTM 带"（米制），
    避免 EPSG:4326 下面积是平方度、EPSG:3857 下远离赤道面积失真。

    PostGIS 后端见 `sql_result_comparator.PostgisResultComparator`，接口同样是 compare(..., metric, **params)。
    """

    METRICS = {"overlap_ratio", "area_error", "hausdorff_distance", "fields", "feature_count"}

    def __init__(self, reader: Callable[[DatasetContext], bytes] | None = None) -> None:
        self._reader = reader

    def compare(
        self,
        target: DatasetContext,
        reference: DatasetContext,
        metric: str,
        **params: Any,
    ) -> CompareResult:
        if metric not in self.METRICS:
            return CompareResult(False, None, None, f"Unsupported comparison metric: {metric}")
        result_gdf = _load_dataset(target, f"result dataset '{target.name or target.handle}'", self._reader)
        reference_gdf = _load_dataset(reference, f"reference dataset '{reference.name or reference.handle}'", self._reader)
        handler = getattr(self, f"_{metric}")
        return handler(result_gdf, reference_gdf, **params)

    # ---------- 指标实现 ----------

    # 逐要素比对阈值：要素数超过该值不再整体 union（大数据集 union_all 是 O(n²) 性能陷阱），
    # 改为逐要素最近邻匹配比对。591 个缓冲多边形整体 union 会卡死（>2min），逐要素毫秒级。
    PER_FEATURE_THRESHOLD = 50

    def _overlap_ratio(self, result: gpd.GeoDataFrame, reference: gpd.GeoDataFrame, **params: Any) -> CompareResult:
        min_ratio = float(params.get("min", 1.0))
        r, ref = self._align_metric(result, reference)
        if len(r) > self.PER_FEATURE_THRESHOLD or len(ref) > self.PER_FEATURE_THRESHOLD:
            return self._per_feature_overlap(r, ref, min_ratio)
        ref_union = _union(ref)
        if ref_union is None or ref_union.area == 0:
            return CompareResult(False, None, None, "Reference has no geometry to compare against")
        res_union = _union(r)
        if res_union is None or res_union.area == 0:
            return CompareResult(False, 0.0, f">= {min_ratio}", "Result has no geometry")
        ratio = min(1.0, res_union.intersection(ref_union).area / ref_union.area)
        passed = ratio >= min_ratio
        return CompareResult(passed, round(ratio, 4), f">= {min_ratio}", f"重叠率：实际 {ratio:.4f} / 预期 >= {min_ratio}")

    def _per_feature_overlap(self, result: gpd.GeoDataFrame, reference: gpd.GeoDataFrame, min_ratio: float) -> CompareResult:
        """逐要素匹配比对重叠率：按 centroid 最近邻把 result 要素映射到 reference 要素，
        逐个算 intersection/ref.area，再求整体加权比值。避免大数据集整体 union 卡死。"""
        matched = _match_nearest(result, reference)
        if not matched:
            return CompareResult(False, None, None, "No matched features to compare")
        total_ref_area = sum(ref.area for _, ref in matched)
        if total_ref_area == 0:
            return CompareResult(False, None, None, "Reference has no geometry to compare against")
        inter_area = sum(_safe_intersection_area(res, ref) for res, ref in matched)
        ratio = min(1.0, inter_area / total_ref_area)
        passed = ratio >= min_ratio
        return CompareResult(passed, round(ratio, 4), f">= {min_ratio}",
                             f"重叠率（逐要素匹配，{len(matched)} 对）：实际 {ratio:.4f} / 预期 >= {min_ratio}")

    def _area_error(self, result: gpd.GeoDataFrame, reference: gpd.GeoDataFrame, **params: Any) -> CompareResult:
        max_ratio = float(params.get("max_ratio", 0.05))
        r, ref = self._align_metric(result, reference)
        if len(r) > self.PER_FEATURE_THRESHOLD or len(ref) > self.PER_FEATURE_THRESHOLD:
            return self._per_feature_area_error(r, ref, max_ratio)
        ref_area = _union(ref).area if _union(ref) is not None else 0.0
        res_area = _union(r).area if _union(r) is not None else 0.0
        if ref_area == 0:
            return CompareResult(False, None, None, "Reference has no geometry to compare against")
        error = abs(res_area - ref_area) / ref_area
        passed = error <= max_ratio
        return CompareResult(passed, round(error, 6), f"<= {max_ratio}", f"面积相对误差：实际 {error:.4%} / 预期 <= {max_ratio:.0%}")

    def _per_feature_area_error(self, result: gpd.GeoDataFrame, reference: gpd.GeoDataFrame, max_ratio: float) -> CompareResult:
        """逐要素面积误差：每对匹配要素算相对误差，取中位数。"""
        matched = _match_nearest(result, reference)
        if not matched:
            return CompareResult(False, None, None, "No matched features to compare")
        errors = []
        for res, ref in matched:
            if ref.area == 0:
                continue
            errors.append(abs(res.area - ref.area) / ref.area)
        if not errors:
            return CompareResult(False, None, None, "No features with nonzero reference area")
        median_error = sorted(errors)[len(errors) // 2]
        passed = median_error <= max_ratio
        return CompareResult(passed, round(median_error, 6), f"<= {max_ratio}",
                             f"面积相对误差（逐要素中位，{len(errors)} 对）：实际 {median_error:.4%} / 预期 <= {max_ratio:.0%}")

    def _hausdorff_distance(self, result: gpd.GeoDataFrame, reference: gpd.GeoDataFrame, **params: Any) -> CompareResult:
        max_meters = float(params.get("max_meters", 20))
        r, ref = self._align_metric(result, reference)
        if len(r) > self.PER_FEATURE_THRESHOLD or len(ref) > self.PER_FEATURE_THRESHOLD:
            return self._per_feature_hausdorff(r, ref, max_meters)
        res_union = _union(r)
        ref_union = _union(ref)
        if res_union is None or ref_union is None or ref_union.is_empty:
            return CompareResult(False, None, None, "Result or reference has no geometry")
        distance = max(hausdorff_distance(res_union, ref_union), hausdorff_distance(ref_union, res_union))
        passed = distance <= max_meters
        return CompareResult(passed, round(distance, 2), f"<= {max_meters} m", f"Hausdorff 偏移：实际 {distance:.2f} m / 预期 <= {max_meters} m")

    def _per_feature_hausdorff(self, result: gpd.GeoDataFrame, reference: gpd.GeoDataFrame, max_meters: float) -> CompareResult:
        """逐要素 Hausdorff：每对匹配要素算双向 Hausdorff，取最大值（最差偏移）。"""
        matched = _match_nearest(result, reference)
        if not matched:
            return CompareResult(False, None, None, "No matched features to compare")
        distances = []
        for res, ref in matched:
            if res.is_empty or ref.is_empty:
                continue
            distances.append(max(hausdorff_distance(res, ref), hausdorff_distance(ref, res)))
        if not distances:
            return CompareResult(False, None, None, "No matched features with geometry")
        worst = max(distances)
        passed = worst <= max_meters
        return CompareResult(passed, round(worst, 2), f"<= {max_meters} m",
                             f"Hausdorff 偏移（逐要素最差，{len(distances)} 对）：实际 {worst:.2f} m / 预期 <= {max_meters} m")

    def _fields(self, result: gpd.GeoDataFrame, reference: gpd.GeoDataFrame, **params: Any) -> CompareResult:
        mode = str(params.get("mode", "contains"))
        result_fields = sorted(result.columns.drop("geometry").tolist())
        reference_fields = sorted(reference.columns.drop("geometry").tolist())
        if mode == "exact":
            passed = result_fields == reference_fields
        else:  # contains：结果字段须覆盖参考字段
            passed = set(reference_fields).issubset(set(result_fields))
        return CompareResult(
            passed,
            result_fields,
            f"{mode}: {reference_fields}",
            f"字段：实际 {result_fields} / 预期 {mode}: {reference_fields}",
        )

    def _feature_count(self, result: gpd.GeoDataFrame, reference: gpd.GeoDataFrame, **params: Any) -> CompareResult:
        count = int(params.get("count", -1))
        actual = len(result)
        passed = actual == count
        return CompareResult(passed, actual, count, f"要素数：实际 {actual} / 预期 {count}")

    # ---------- 工具 ----------

    def _align_metric(self, result: gpd.GeoDataFrame, reference: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
        """两者对齐到参考中心点选定的 UTM 带（米制），供面积/距离指标使用。"""
        lon, lat = _centroid_lonlat(reference)
        utm = _utm_epsg(lon, lat)
        return result.to_crs(utm), reference.to_crs(utm)


def _load_dataset(
    dataset: DatasetContext,
    label: str,
    reader: Callable[[DatasetContext], bytes] | None = None,
) -> gpd.GeoDataFrame:
    if reader is not None:
        try:
            raw = reader(dataset)
            gdf = gpd.read_file(BytesIO(raw))
        except Exception as exc:
            raise ValueError(f"{label} could not be read through evaluation data service") from exc
    else:
        if not dataset.path:
            raise ValueError(f"{label} has no local path to compare (结果未落盘？)")
        gdf = gpd.read_file(dataset.path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    return gdf


def _ensure_geometry(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """去掉空几何行，避免 unary_union 报错。"""
    return gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]


def _union(gdf: gpd.GeoDataFrame) -> Any:
    """空几何防御：无有效几何返回 None。用 union_all()（unary_union 在 geopandas 1.1 已弃用）。"""
    valid = _ensure_geometry(gdf)
    if valid.empty:
        return None
    return valid.geometry.union_all()


def _match_nearest(result: gpd.GeoDataFrame, reference: gpd.GeoDataFrame) -> list[tuple[Any, Any]]:
    """逐要素最近邻匹配：把 result 每个要素按 centroid 最近邻映射到 reference 要素。

    返回 [(res_geom, ref_geom), ...]。仅对已对齐到同坐标系（米制 UTM）的输入使用。
    用 numpy 批量算距离矩阵（n×m），避免纯 Python O(n²) 逐对循环（大数据集会卡死）；
    不依赖 sklearn（项目未声明该依赖，镜像也不一定有）。
    """
    res_valid = _ensure_geometry(result)
    ref_valid = _ensure_geometry(reference)
    if res_valid.empty or ref_valid.empty:
        return []
    res_pts = [(g.centroid.x, g.centroid.y) for g in res_valid.geometry]
    ref_pts = [(g.centroid.x, g.centroid.y) for g in ref_valid.geometry]
    res_geoms = list(res_valid.geometry)
    ref_geoms = list(ref_valid.geometry)
    try:
        import numpy as np

        res_arr = np.array(res_pts, dtype=np.float64)
        ref_arr = np.array(ref_pts, dtype=np.float64)
        # 距离矩阵 n×m（欧氏，米制 UTM 下合理）
        diff = res_arr[:, None, :] - ref_arr[None, :, :]
        dist = np.einsum("ijk,ijk->ij", diff, diff)
        idx = dist.argmin(axis=1)
        return [(res_geoms[i], ref_geoms[j]) for i, j in enumerate(idx)]
    except ImportError:
        # 极端回退：纯 Python O(n²)（仅 numpy 也不可用时）
        return [
            (res_geoms[i], min(ref_geoms, key=lambda rg: (rg.centroid.x - res_geoms[i].centroid.x) ** 2
                                + (rg.centroid.y - res_geoms[i].centroid.y) ** 2))
            for i in range(len(res_geoms))
        ]


def _safe_intersection_area(a: Any, b: Any) -> float:
    """空几何防御的 intersection 面积。"""
    if a is None or b is None or a.is_empty or b.is_empty:
        return 0.0
    return a.intersection(b).area


def _centroid_lonlat(gdf: gpd.GeoDataFrame) -> tuple[float, float]:
    geographic = gdf.to_crs("EPSG:4326")
    minx, miny, maxx, maxy = geographic.total_bounds
    return (minx + maxx) / 2, (miny + maxy) / 2


def _utm_epsg(lon: float, lat: float) -> int:
    zone = max(1, min(60, int((lon + 180) // 6) + 1))
    return 32600 + zone if lat >= 0 else 32700 + zone
