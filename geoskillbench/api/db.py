"""评测报告与批次持久化连接层（阶段二与迭代六）。

- DATABASE_URL 为空（本地开发）→ 自动用 SQLite 文件库 reports.db，零配置可跑。
- DATABASE_URL 非空（服务器部署）→ 用 PostGIS / PostgreSQL：
      DATABASE_URL=postgresql+psycopg://user:pass@host:5432/dbname
  同一份代码，通过 .env 切换，结构不变。
- 网络库连接超时 3 秒（connect_timeout）。库不可达时由 /api/runs 等接口按设计降级，
  避免等操作系统 TCP 超时把前端代理拖成 500。

表结构：
1. reports 表：存单次评测报告的 JSON + Markdown 全文及摘要。
2. batches 表：存批次运行（Batch/Repeat）的聚合汇总与 JSON 全文。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import Float, Integer, String, Text, create_engine, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

load_dotenv()

DEFAULT_DATABASE_URL = "sqlite:///./reports.db"  # 本地开发兜底，不依赖服务器
DB_CONNECT_TIMEOUT_SECONDS = 3


class Base(DeclarativeBase):
    pass


class RunReport(Base):
    __tablename__ = "reports"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String, index=True)
    scenario_name: Mapped[str] = mapped_column(String, default="")
    executor: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="")
    batch_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=lambda: datetime.now(UTC).isoformat())
    json_content: Mapped[str] = mapped_column(Text, default="")
    md_content: Mapped[str] = mapped_column(Text, default="")


class BatchReport(Base):
    __tablename__ = "batches"

    batch_id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[str] = mapped_column(String, default=lambda: datetime.now(UTC).isoformat())
    status: Mapped[str] = mapped_column(String, default="succeeded")
    total_runs: Mapped[int] = mapped_column(Integer, default=0)
    passed_runs: Mapped[int] = mapped_column(Integer, default=0)
    failed_runs: Mapped[int] = mapped_column(Integer, default=0)
    pass_rate: Mapped[float] = mapped_column(Float, default=0.0)
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")


def _database_url() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    return url or DEFAULT_DATABASE_URL


def _connect_args(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {"connect_timeout": DB_CONNECT_TIMEOUT_SECONDS}


def _engine():
    url = _database_url()
    return create_engine(url, connect_args=_connect_args(url), pool_pre_ping=True)


_engine_instance = None
_session_factory = None


def _get_engine():
    global _engine_instance, _session_factory
    if _engine_instance is None:
        _engine_instance = _engine()
        _session_factory = sessionmaker(bind=_engine_instance, expire_on_commit=False)
        Base.metadata.create_all(_engine_instance)  # 首次使用自动建表
    return _engine_instance


def _session():
    _get_engine()
    return _session_factory()


# ---------- 单次运行报告持久化 ----------

def save_report(report: dict) -> None:
    """持久化一条报告。report 需含 run_id/scenario_id/scenario_name/executor/status/json/md。"""
    with _session() as session:
        session.merge(
            RunReport(
                run_id=report["run_id"],
                scenario_id=report["scenario_id"],
                scenario_name=report.get("scenario_name", ""),
                executor=report.get("executor", ""),
                status=report.get("status", ""),
                batch_id=report.get("batch_id"),
                json_content=report.get("json", ""),
                md_content=report.get("md", ""),
            )
        )
        session.commit()
    prune_reports()


def list_reports(scenario_id: str | None = None, batch_id: str | None = None) -> list[dict]:
    with _session() as session:
        statement = select(RunReport).order_by(RunReport.created_at.desc())
        if scenario_id:
            statement = statement.where(RunReport.scenario_id == scenario_id)
        if batch_id:
            statement = statement.where(RunReport.batch_id == batch_id)
        return [_report_dict(row) for row in session.scalars(statement)]


def get_report(run_id: str) -> dict | None:
    with _session() as session:
        row = session.get(RunReport, run_id)
        return _report_dict(row) if row else None


def _report_dict(row: RunReport) -> dict:
    return {
        "run_id": row.run_id,
        "scenario_id": row.scenario_id,
        "scenario_name": row.scenario_name,
        "executor": row.executor,
        "status": row.status,
        "batch_id": row.batch_id,
        "created_at": row.created_at,
        "json": row.json_content,
        "md": row.md_content,
    }


# ---------- 批次运行持久化 ----------

def save_batch(batch_data: dict[str, Any]) -> None:
    """持久化一个批次运行结果。"""
    with _session() as session:
        session.merge(
            BatchReport(
                batch_id=batch_data["batch_id"],
                created_at=batch_data.get("created_at") or datetime.now(UTC).isoformat(),
                status=batch_data.get("status", "succeeded"),
                total_runs=int(batch_data.get("total_runs", 0)),
                passed_runs=int(batch_data.get("passed_runs", 0)),
                failed_runs=int(batch_data.get("failed_runs", 0)),
                pass_rate=float(batch_data.get("pass_rate", 0.0)),
                summary_json=batch_data.get("summary_json", "{}"),
                result_json=batch_data.get("result_json", "{}"),
            )
        )
        session.commit()


def list_batches() -> list[dict[str, Any]]:
    with _session() as session:
        statement = select(BatchReport).order_by(BatchReport.created_at.desc())
        return [_batch_dict(row) for row in session.scalars(statement)]


def get_batch(batch_id: str) -> dict[str, Any] | None:
    with _session() as session:
        row = session.get(BatchReport, batch_id)
        return _batch_dict(row) if row else None


def _batch_dict(row: BatchReport) -> dict[str, Any]:
    summary = {}
    try:
        summary = json.loads(row.summary_json)
    except Exception:
        pass
    return {
        "batch_id": row.batch_id,
        "created_at": row.created_at,
        "status": row.status,
        "total_runs": row.total_runs,
        "passed_runs": row.passed_runs,
        "failed_runs": row.failed_runs,
        "pass_rate": row.pass_rate,
        "summary": summary,
        "result_json": row.result_json,
    }


def reports_db_path() -> str:
    url = _database_url()
    if url.startswith("sqlite"):
        return str(Path(url.removeprefix("sqlite:///")).resolve())
    return url


def prune_reports(keep: int | None = None) -> int:
    limit = keep if keep is not None else _history_keep()
    with _session() as session:
        statement = delete(RunReport).where(
            RunReport.run_id.not_in(
                select(RunReport.run_id).order_by(RunReport.created_at.desc()).limit(limit)
            )
        )
        result = session.execute(statement)
        session.commit()
        return result.rowcount or 0


def _history_keep() -> int:
    try:
        return int(os.environ.get("GEO_BENCH_HISTORY_KEEP", "100"))
    except ValueError:
        return 100
