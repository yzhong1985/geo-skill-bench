from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from geoskillbench.api.app import app
from geoskillbench.models.result import TestResult


def _mock_test_result(scenario_id: str, run_id: str, passed: bool = True) -> TestResult:
    return TestResult(
        run_id=run_id,
        scenario_id=scenario_id,
        scenario_name=f"Scenario {scenario_id}",
        status="passed" if passed else "failed",
        duration_ms=400,
        stage_results={},
        tool_calls=[{"tool_name": "createBuffer", "status": "success"}],
        assertions=[],
        judge={"score": 1.0 if passed else 0.0, "passed": passed},
        conversation=[],
        final_output={"final_response": "done"},
        loaded_skill_references=[],
        errors=[],
        operational_status="succeeded",
        evaluation_verdict="passed" if passed else "failed",
        termination_reason="completed",
        archive_status="succeeded",
        cleanup_status="succeeded",
        failures=[],
    )


def test_batch_api_endpoints() -> None:
    client = TestClient(app)

    # 1. 校验非法创建（空场景）
    resp = client.post("/api/batches", json={"scenarios": []})
    assert resp.status_code == 400

    # 2. 校验场景不存在
    resp = client.post("/api/batches", json={"scenarios": ["scenarios/non_existent.yml"]})
    assert resp.status_code == 404

    # 3. 正常创建并 mock 任务执行
    with patch("geoskillbench.api.task_manager.BatchRunner") as mock_runner_cls:
        mock_runner = MagicMock()
        mock_runner.run_batch.return_value = MagicMock(model_dump=lambda: {"batch_id": "test_b", "summary": {}})
        mock_runner_cls.return_value = mock_runner

        resp = client.post(
            "/api/batches",
            json={
                "scenarios": ["scenarios/buffer_school_500m_5b_001.yml"],
                "repeat_count": 2,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        batch_id = data["batch_id"]
        assert batch_id.startswith("batch_")
        assert data["total_runs"] == 2

        # 查询列表
        resp_list = client.get("/api/batches")
        assert resp_list.status_code == 200
        batches = resp_list.json().get("batches", [])
        assert any(b["batch_id"] == batch_id for b in batches)

        # 查询单批次
        resp_get = client.get(f"/api/batches/{batch_id}")
        assert resp_get.status_code == 200
        assert resp_get.json()["batch_id"] == batch_id



def test_batch_db_persistence(tmp_path, monkeypatch) -> None:
    db_file = tmp_path / "test_batches.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")

    from geoskillbench.api import db

    # 重置 DB 单例连接
    db._engine_instance = None
    db._session_factory = None

    # 验证 DB save / list / get
    batch_data = {
        "batch_id": "test_batch_db_01",
        "status": "succeeded",
        "total_runs": 5,
        "passed_runs": 4,
        "failed_runs": 1,
        "pass_rate": 0.8,
        "summary_json": '{"pass_rate": 0.8, "total_runs": 5}',
    }
    db.save_batch(batch_data)
    loaded = db.get_batch("test_batch_db_01")
    assert loaded is not None
    assert loaded["batch_id"] == "test_batch_db_01"
    assert loaded["pass_rate"] == 0.8
    assert loaded["total_runs"] == 5
    assert loaded["summary"]["pass_rate"] == 0.8

    # 清理重置
    db._engine_instance = None
    db._session_factory = None


def test_postgres_connect_args_include_timeout() -> None:
    from geoskillbench.api import db

    args = db._connect_args("postgresql+psycopg://geo:geo@192.0.2.1:5432/geoskillbench")
    assert args["connect_timeout"] == db.DB_CONNECT_TIMEOUT_SECONDS
    sqlite_args = db._connect_args("sqlite:///./reports.db")
    assert sqlite_args == {"check_same_thread": False}
    assert "connect_timeout" not in sqlite_args


def test_unreachable_postgres_fails_within_connect_timeout(monkeypatch) -> None:
    """黑洞地址应在 connect_timeout 内失败，而不是卡到 OS TCP 超时。"""
    import time

    from geoskillbench.api import db

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://geo:geo@192.0.2.1:5432/geoskillbench")
    db._engine_instance = None
    db._session_factory = None
    started = time.perf_counter()
    try:
        db.list_reports()
        raise AssertionError("unreachable postgres must fail")
    except Exception:
        elapsed = time.perf_counter() - started
        assert elapsed < db.DB_CONNECT_TIMEOUT_SECONDS + 5
    finally:
        db._engine_instance = None
        db._session_factory = None


def _write_batch_summary(tmp_path: Path, batch_id: str) -> Path:
    from geoskillbench.models.batch import BatchRequest, BatchResult, BatchSummary

    batch_dir = tmp_path / "batches" / batch_id
    batch_dir.mkdir(parents=True)
    result = BatchResult(
        batch_id=batch_id,
        request=BatchRequest(scenarios=["scenarios/demo_batch_mock_001.yml"], repeat_count=1),
        summary=BatchSummary(
            batch_id=batch_id,
            created_at="2026-09-02T00:00:00+00:00",
            status="failed",
            total_runs=1,
            failed_runs=1,
            pass_rate=0.0,
        ),
        runs=[],
    )
    summary_file = batch_dir / "summary.json"
    summary_file.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return batch_dir


def test_analyze_and_get_diagnostics(tmp_path, monkeypatch) -> None:
    from geoskillbench.api import app as api_app
    from geoskillbench.models.batch import BatchAIDiagnostics

    monkeypatch.setattr(api_app, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(api_app, "ROOT_DIR", tmp_path)
    batch_id = "batch_diag_01"
    _write_batch_summary(tmp_path, batch_id)
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "keep.yml").write_text("untouched\n", encoding="utf-8")

    diagnostics = BatchAIDiagnostics(
        batch_id=batch_id,
        created_at="2026-09-02T00:00:00+00:00",
        source="llm",
        model="m",
        summary_text="多次失败",
        attribution_breakdown={"env_error": 1.0},
        root_cause_analysis="MCP 失败",
    )
    client = TestClient(app)

    missing = client.get("/api/batches/batch_missing/diagnostics")
    assert missing.status_code == 404

    missing_analyze = client.post("/api/batches/batch_missing/analyze")
    assert missing_analyze.status_code == 404

    with patch("geoskillbench.runtime.batch_analyst.run_batch_ai_analyst", return_value=diagnostics):
        resp = client.post(f"/api/batches/{batch_id}/analyze")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "llm"
    assert body["attribution_breakdown"]["env_error"] == 1.0
    saved = tmp_path / "batches" / batch_id / "ai_diagnostics.json"
    assert saved.exists()
    assert json.loads(saved.read_text(encoding="utf-8"))["batch_id"] == batch_id
    assert (skills_dir / "keep.yml").read_text(encoding="utf-8") == "untouched\n"

    got = client.get(f"/api/batches/{batch_id}/diagnostics")
    assert got.status_code == 200
    assert got.json()["summary_text"] == "多次失败"


def test_analyze_unavailable_writes_placeholder_and_returns_502(tmp_path, monkeypatch) -> None:
    from geoskillbench.api import app as api_app
    from geoskillbench.models.batch import BatchAIDiagnostics

    monkeypatch.setattr(api_app, "REPORTS_DIR", tmp_path)
    batch_id = "batch_diag_fail"
    _write_batch_summary(tmp_path, batch_id)
    placeholder = BatchAIDiagnostics(
        batch_id=batch_id,
        created_at="2026-09-02T00:00:00+00:00",
        source="unavailable",
        model="m",
        summary_text="AI 诊断不可用",
        error="down",
    )
    client = TestClient(app)
    with patch("geoskillbench.runtime.batch_analyst.run_batch_ai_analyst", return_value=placeholder):
        resp = client.post(f"/api/batches/{batch_id}/analyze")
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["source"] == "unavailable"
    saved = json.loads((tmp_path / "batches" / batch_id / "ai_diagnostics.json").read_text(encoding="utf-8"))
    assert saved["source"] == "unavailable"
    assert saved["suggested_patch"] is None

