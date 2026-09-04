from __future__ import annotations

from typing import Any

import pytest

from geoskillbench.assertions.assertion_engine import AssertionEngine
from geoskillbench.assertions.result_comparator import CompareResult
from geoskillbench.assertions.sql_result_comparator import PostgisResultComparator, dataset_sql_name
from geoskillbench.fixtures.fixture_manager import FixtureManager
from geoskillbench.models.result import AssertionItemResult, TestResult
from geoskillbench.models.scenario import AssertionConfig, DataConfig, FixtureConfig, Scenario, SkillConfig, TargetConfig
from geoskillbench.models.test_context import DatasetContext, TestContext
from geoskillbench.recorder.execution_recorder import ExecutionRecorder
from geoskillbench.reports.report_generator import ReportGenerator
from geoskillbench.data_service.models import DatasetDescriptor, RunRegistration


class _FakeMappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeMappings:
        return _FakeMappings(self._rows)


class _FakeConn:
    def __init__(self, engine: "_FakeEngine") -> None:
        self._engine = engine

    def execute(self, sql: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        statement = getattr(sql, "text", None) or str(sql)
        self._engine.statements.append(statement)
        return _FakeResult(self._engine.respond(statement, params or {}))

    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _FakeEngine:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.overlap = {"inter_area": 90.0, "ref_area": 100.0}
        self.area_error = {"area_error": 0.02}
        self.hausdorff = {"hausdorff": 4.5}
        self.count = 2
        self.result_fields = ["school_id", "name", "smgeometry"]
        self.reference_fields = ["school_id", "smgeometry"]

    def connect(self) -> _FakeConn:
        return _FakeConn(self)

    def respond(self, statement: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if "geometry_columns" in statement:
            return [{"f_geometry_column": "smgeometry"}]
        if "information_schema.columns" in statement:
            table = params.get("table")
            names = self.result_fields if table == "result_tbl" else self.reference_fields
            return [{"column_name": name} for name in names]
        if "COUNT(*)" in statement:
            return [{"count": self.count}]
        if "inter_area" in statement:
            return [self.overlap]
        if "area_error" in statement:
            return [self.area_error]
        if "hausdorff" in statement:
            return [self.hausdorff]
        raise AssertionError(f"unexpected SQL: {statement}")


def _comparator() -> tuple[PostgisResultComparator, _FakeEngine]:
    engine = _FakeEngine()
    return PostgisResultComparator(engine=engine, schema="public"), engine


def test_dataset_sql_name_prefers_location_then_logical_id() -> None:
    dataset = DatasetContext(handle="dh", name="buffer_result", metadata={"logical_id": "expected_tbl"})
    assert dataset_sql_name(dataset, {"tableName": "tmp_createBuffer_x"}) == "tmp_createBuffer_x"
    assert dataset_sql_name(dataset) == "expected_tbl"
    assert dataset_sql_name(DatasetContext(handle="dh", name="x")) is None


def test_unsafe_identifier_is_rejected() -> None:
    cmp, _ = _comparator()
    with pytest.raises(ValueError, match="unsafe SQL identifier"):
        cmp.compare("result;drop", "expected_tbl", "feature_count", count=1)


def test_overlap_sql_uses_union_and_utm_not_smid_row() -> None:
    cmp, engine = _comparator()
    result = cmp.compare("result_tbl", "expected_tbl", "overlap_ratio", min=0.9)
    assert result.passed is True
    assert result.actual == 0.9
    metric_sql = next(sql for sql in engine.statements if "inter_area" in sql)
    assert "ST_Union" in metric_sql
    assert "ST_Transform" in metric_sql
    assert "32600" in metric_sql
    assert "smid" not in metric_sql
    assert "LIMIT 1" not in metric_sql


def test_area_and_hausdorff_and_fields_and_count() -> None:
    cmp, _ = _comparator()
    area = cmp.compare("result_tbl", "expected_tbl", "area_error", max_ratio=0.05)
    assert area.passed is True
    assert area.actual == 0.02
    distance = cmp.compare("result_tbl", "expected_tbl", "hausdorff_distance", max_meters=20)
    assert distance.passed is True
    assert distance.actual == 4.5
    fields = cmp.compare("result_tbl", "expected_tbl", "fields", mode="contains")
    assert fields.passed is True
    assert fields.actual == ["name", "school_id"]
    count = cmp.compare("result_tbl", "expected_tbl", "feature_count", count=2)
    assert count.passed is True
    assert count.actual == 2


def test_empty_reference_geometry_fails_overlap() -> None:
    cmp, engine = _comparator()
    engine.overlap = {"inter_area": 0.0, "ref_area": 0.0}
    result = cmp.compare("result_tbl", "expected_tbl", "overlap_ratio", min=0.9)
    assert result.passed is False
    assert "no geometry" in result.detail


class _FakeSql:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def compare(self, result_table: str, reference_table: str, metric: str, **_params: Any) -> CompareResult:
        self.calls.append((result_table, reference_table, metric))
        return CompareResult(True, 0.95, ">= 0.9", "重叠率：实际 0.9500 / 预期 >= 0.9")


class _FakeFile:
    def __init__(self) -> None:
        self.called = False

    def compare(self, *_args: Any, **_params: Any) -> CompareResult:
        self.called = True
        return CompareResult(True, 1.0, ">= 0.9", "file")


def _result_datasets(*, path: str | None = None, logical_id: str | None = "expected_tbl") -> tuple[ExecutionRecorder, TestContext]:
    recorder = ExecutionRecorder(scenario_id="s")
    result = DatasetContext(handle="dh_result", name="buffer_result", path=path)
    recorder.final_output = {"datasets": {"buffer_result": result}}
    reference = DatasetContext(
        handle="dh_ref",
        name="expected_buffer",
        path=path,
        metadata={"logical_id": logical_id} if logical_id else {},
    )
    ctx = TestContext(
        scenario_id="s",
        scenario_name="s",
        datasets={"buffer_result": result},
        reference_datasets={"expected_buffer": reference},
    )
    return recorder, ctx


def test_engine_uses_postgis_when_tables_resolve() -> None:
    sql = _FakeSql()
    files = _FakeFile()
    engine = AssertionEngine(comparator=files, sql_comparator=sql, result_locator=lambda _alias: {"tableName": "tmp_result"})
    recorder, ctx = _result_datasets()
    item = engine._run_result_compare(
        "overlap_ratio",
        AssertionConfig(type="result_overlap_ratio", reference="expected_buffer", min=0.9),
        recorder,
        ctx,
    )
    assert item.backend == "postgis"
    assert item.actual == 0.95
    assert item.expected == ">= 0.9"
    assert files.called is False
    assert sql.calls == [("tmp_result", "expected_tbl", "overlap_ratio")]


def test_engine_falls_back_to_file_without_table_names() -> None:
    files = _FakeFile()
    engine = AssertionEngine(comparator=files, sql_comparator=_FakeSql(), result_locator=lambda _alias: None)
    recorder, ctx = _result_datasets(path="/tmp/result.geojson", logical_id=None)
    item = engine._run_result_compare(
        "overlap_ratio",
        AssertionConfig(type="result_overlap_ratio", reference="expected_buffer", min=0.9),
        recorder,
        ctx,
    )
    assert item.backend == "file"
    assert files.called is True


def test_engine_fails_without_table_or_path() -> None:
    engine = AssertionEngine(sql_comparator=_FakeSql())
    recorder, ctx = _result_datasets(logical_id=None)
    item = engine._run_result_compare(
        "overlap_ratio",
        AssertionConfig(type="result_overlap_ratio", reference="expected_buffer"),
        recorder,
        ctx,
    )
    assert item.passed is False
    assert item.backend is None
    assert "table names" in item.message


def test_registration_keeps_fixture_logical_id() -> None:
    scenario = Scenario(
        id="buffer_school_500m_5b_001",
        name="t",
        version="1.0.0",
        user_task="t",
        target=TargetConfig(skill_id="gis_buffer_analysis"),
        skill=SkillConfig(path="../skills/gis_buffer_analysis.skill.yml"),
        data=DataConfig(
            fixtures=[FixtureConfig(id="schools", catalog_id="agentx_gpa_demo_sdx_school")],
            reference=[FixtureConfig(id="expected_buffer", evaluation_id="tmp_createBuffer_ref")],
        ),
    )
    registration = RunRegistration(
        run_id="run_1",
        inputs=[DatasetDescriptor(handle="dh_in", alias="schools", role="input", run_id="run_1")],
        references=[DatasetDescriptor(handle="dh_ref", alias="gt", role="reference", run_id="run_1")],
    )
    _datasets, references = FixtureManager().prepare(scenario, registration)
    assert "expected_buffer" in references
    assert references["expected_buffer"].metadata.get("logical_id") == "tmp_createBuffer_ref"
    assert references["expected_buffer"].handle == "dh_ref"


def test_report_marks_postgis_backend() -> None:
    result = TestResult(
        scenario_id="s",
        scenario_name="s",
        status="passed",
        duration_ms=1,
        stage_results={},
        tool_calls=[],
        assertions=[
            AssertionItemResult(
                type="result_overlap_ratio",
                passed=True,
                message="重叠率：实际 0.9500 / 预期 >= 0.9",
                backend="postgis",
                actual=0.95,
                expected=">= 0.9",
            ).model_dump()
        ],
        judge={},
    )
    markdown = ReportGenerator().generate_markdown(result)
    assert "`result_overlap_ratio` [postgis]" in markdown
