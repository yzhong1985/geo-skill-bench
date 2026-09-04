from __future__ import annotations

import os
import re
from typing import Any

from sqlalchemy import create_engine, text

from geoskillbench.assertions.result_comparator import CompareResult
from geoskillbench.models.test_context import DatasetContext

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PostgisResultComparator:
    """在 PostGIS 内比较 result/reference，断言只保留指标，不拉几何。

    几何指标对整表 ST_Union 后投到参考中心点 UTM 带（米制），与文件后端并集语义对齐。
    不按 smid 取单行，也不复制文件后端的 centroid 最近邻切分。
    """

    def __init__(self, db_url: str = "", schema: str = "public", *, engine: Any | None = None) -> None:
        if engine is not None:
            self._engine = engine
        else:
            if not db_url.strip():
                raise ValueError("evaluation database URL is empty")
            self._engine = create_engine(db_url.strip(), pool_pre_ping=True)
        self._schema = _quote_ident(schema)

    @classmethod
    def from_env(cls) -> "PostgisResultComparator | None":
        from pathlib import Path

        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        url = (os.environ.get("GEO_EVAL_DATABASE_URL") or "").strip()
        if not url.startswith("postgresql"):
            return None
        schema = (os.environ.get("GEO_EVAL_DB_SCHEMA") or "public").strip() or "public"
        return cls(url, schema=schema)

    def compare(
        self,
        result_table: str,
        reference_table: str,
        metric: str,
        **params: Any,
    ) -> CompareResult:
        result_table = _quote_ident(result_table)
        reference_table = _quote_ident(reference_table)
        try:
            if metric == "feature_count":
                return self._feature_count(result_table, params)
            if metric == "overlap_ratio":
                return self._overlap_ratio(result_table, reference_table, params)
            if metric == "area_error":
                return self._area_error(result_table, reference_table, params)
            if metric == "hausdorff_distance":
                return self._hausdorff(result_table, reference_table, params)
            if metric == "fields":
                return self._fields(result_table, reference_table, params)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"in-db {metric} comparison failed") from exc
        return CompareResult(False, None, None, f"Unsupported in-db comparison metric: {metric}")

    def _feature_count(self, result_table: str, params: dict[str, Any]) -> CompareResult:
        expected = int(params.get("count", -1))
        actual = int(self._scalar(f"SELECT COUNT(*) FROM {self._schema}.{result_table}") or 0)
        passed = actual == expected
        return CompareResult(passed, actual, expected, f"要素数：实际 {actual} / 预期 {expected}")

    def _overlap_ratio(self, result_table: str, reference_table: str, params: dict[str, Any]) -> CompareResult:
        min_ratio = float(params.get("min", 1.0))
        row = self._first(self._utm_metric_sql(
            result_table,
            reference_table,
            """
              ST_Area(ST_Intersection(res_m.g, ref_m.g)) AS inter_area,
              ST_Area(ref_m.g) AS ref_area
            """,
        ))
        inter_area = float(row["inter_area"] or 0) if row else 0.0
        ref_area = float(row["ref_area"] or 0) if row else 0.0
        if ref_area == 0:
            return CompareResult(False, None, f">= {min_ratio}", "Reference has no geometry to compare against")
        ratio = min(1.0, inter_area / ref_area)
        passed = ratio >= min_ratio
        return CompareResult(passed, round(ratio, 4), f">= {min_ratio}", f"重叠率：实际 {ratio:.4f} / 预期 >= {min_ratio}")

    def _area_error(self, result_table: str, reference_table: str, params: dict[str, Any]) -> CompareResult:
        max_ratio = float(params.get("max_ratio", 0.05))
        row = self._first(self._utm_metric_sql(
            result_table,
            reference_table,
            """
              ABS(ST_Area(res_m.g) - ST_Area(ref_m.g))
              / NULLIF(ST_Area(ref_m.g), 0) AS area_error
            """,
        ))
        if not row or row.get("area_error") is None:
            return CompareResult(False, None, f"<= {max_ratio}", "Reference has no geometry to compare against")
        error = float(row["area_error"] or 0)
        passed = error <= max_ratio
        return CompareResult(
            passed,
            round(error, 6),
            f"<= {max_ratio}",
            f"面积相对误差：实际 {error:.4%} / 预期 <= {max_ratio:.0%}",
        )

    def _hausdorff(self, result_table: str, reference_table: str, params: dict[str, Any]) -> CompareResult:
        max_meters = float(params.get("max_meters", 20))
        row = self._first(self._utm_metric_sql(
            result_table,
            reference_table,
            """
              GREATEST(
                ST_HausdorffDistance(res_m.g, ref_m.g),
                ST_HausdorffDistance(ref_m.g, res_m.g)
              ) AS hausdorff
            """,
        ))
        if not row or row.get("hausdorff") is None:
            return CompareResult(False, None, f"<= {max_meters} m", "Result or reference has no geometry")
        distance = float(row["hausdorff"] or 0)
        passed = distance <= max_meters
        return CompareResult(
            passed,
            round(distance, 2),
            f"<= {max_meters} m",
            f"Hausdorff 偏移：实际 {distance:.2f} m / 预期 <= {max_meters} m",
        )

    def _fields(self, result_table: str, reference_table: str, params: dict[str, Any]) -> CompareResult:
        mode = str(params.get("mode", "contains"))
        result_geom = self._geometry_column(result_table).strip('"')
        reference_geom = self._geometry_column(reference_table).strip('"')
        result_fields = self._column_names(result_table, exclude={result_geom})
        reference_fields = self._column_names(reference_table, exclude={reference_geom})
        if mode == "exact":
            passed = result_fields == reference_fields
        else:
            passed = set(reference_fields).issubset(set(result_fields))
        return CompareResult(
            passed,
            result_fields,
            f"{mode}: {reference_fields}",
            f"字段：实际 {result_fields} / 预期 {mode}: {reference_fields}",
        )

    def _utm_metric_sql(self, result_table: str, reference_table: str, select_list: str) -> str:
        result_geom = self._geometry_column(result_table)
        reference_geom = self._geometry_column(reference_table)
        return f"""
            WITH
            res_raw AS (
              SELECT ST_Union(r.{result_geom}) AS g
              FROM {self._schema}.{result_table} r
              WHERE r.{result_geom} IS NOT NULL AND NOT ST_IsEmpty(r.{result_geom})
            ),
            ref_raw AS (
              SELECT ST_Union(e.{reference_geom}) AS g
              FROM {self._schema}.{reference_table} e
              WHERE e.{reference_geom} IS NOT NULL AND NOT ST_IsEmpty(e.{reference_geom})
            ),
            centroid AS (
              SELECT ST_Centroid(ST_Transform(g, 4326)) AS c FROM ref_raw WHERE g IS NOT NULL
            ),
            utm AS (
              SELECT COALESCE((
                SELECT
                  (CASE WHEN ST_Y(c) >= 0 THEN 32600 ELSE 32700 END)
                  + GREATEST(1, LEAST(60, FLOOR((ST_X(c) + 180) / 6)::int + 1))
                FROM centroid
              ), 32650) AS epsg
            ),
            res_m AS (
              SELECT ST_Transform(res_raw.g, utm.epsg) AS g FROM res_raw, utm
            ),
            ref_m AS (
              SELECT ST_Transform(ref_raw.g, utm.epsg) AS g FROM ref_raw, utm
            )
            SELECT {select_list}
            FROM res_m, ref_m
            """

    def _column_names(self, table: str, exclude: set[str] | None = None) -> list[str]:
        rows = self._all(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table
            ORDER BY ordinal_position
            """,
            {"schema": self._schema.strip('"'), "table": table.strip('"')},
        )
        skip = {name.lower() for name in (exclude or set())}
        return sorted(
            str(row["column_name"])
            for row in rows
            if str(row["column_name"]).lower() not in skip
        )

    def _geometry_column(self, table: str) -> str:
        row = self._first(
            """
            SELECT f_geometry_column
            FROM geometry_columns
            WHERE f_table_schema = :schema AND f_table_name = :table
            LIMIT 1
            """,
            {"schema": self._schema.strip('"'), "table": table.strip('"')},
        )
        if row and row.get("f_geometry_column"):
            return _quote_ident(str(row["f_geometry_column"]))
        return _quote_ident("smgeometry")

    def _scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        row = self._first(sql, params)
        if not row:
            return None
        return next(iter(row.values()))

    def _first(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = conn.execute(text(sql), params or {}).mappings().first()
            return dict(row) if row is not None else None

    def _all(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql), params or {}).mappings().all()
            return [dict(row) for row in rows]


def dataset_sql_name(dataset: DatasetContext, location: dict[str, str] | None = None) -> str | None:
    if location:
        table = location.get("tableName") or location.get("bufferResult")
        if table:
            return table
    logical_id = (dataset.metadata or {}).get("logical_id")
    return str(logical_id) if logical_id else None


def _quote_ident(value: str) -> str:
    if not _IDENT.match(value):
        raise ValueError(f"unsafe SQL identifier: {value}")
    return f'"{value}"'
