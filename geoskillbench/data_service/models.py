from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DatasetRole = Literal["input", "reference", "result"]


class DatasetDescriptor(BaseModel):
    """数据服务签发的安全数据集描述符。

    handle 是平台和 MCP 工具之间唯一可操作的数据标识；物理表名、文件路径和连接串
    均不得出现在该对象中。
    """

    model_config = ConfigDict(extra="forbid")

    protocol_version: str = "geoskillbench-data/v1"
    handle: str
    alias: str
    role: DatasetRole
    run_id: str
    geometry_type: str | None = None
    crs: str | None = None
    feature_count: int | None = None
    fields: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    content_hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_safe_handle(self) -> "DatasetDescriptor":
        prohibited = ("path", "table", "schema", "db_url", "database_url", "server_side_path")
        leaked = [key for key in prohibited if key in self.metadata]
        if leaked:
            raise ValueError(f"dataset descriptor metadata contains prohibited fields: {', '.join(leaked)}")
        if not self.handle.strip():
            raise ValueError("dataset descriptor handle must not be empty")
        return self


class RunRegistration(BaseModel):
    """register_run 成功后的受控运行上下文。"""

    model_config = ConfigDict(extra="forbid")

    protocol_version: str = "geoskillbench-data/v1"
    run_id: str
    status: Literal["registered", "already_registered"] = "registered"
    inputs: list[DatasetDescriptor] = Field(default_factory=list)
    references: list[DatasetDescriptor] = Field(default_factory=list)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _check_roles_and_run(self) -> "RunRegistration":
        for dataset in self.inputs:
            if dataset.role != "input":
                raise ValueError(f"registered input {dataset.alias} must have role=input")
            if dataset.run_id != self.run_id:
                raise ValueError(f"registered input {dataset.alias} belongs to another run")
        for dataset in self.references:
            if dataset.role != "reference":
                raise ValueError(f"registered reference {dataset.alias} must have role=reference")
            if dataset.run_id != self.run_id:
                raise ValueError(f"registered reference {dataset.alias} belongs to another run")
        return self


class ArchiveResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_id: str
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class ReleaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: Literal["released", "already_released", "expired"]
