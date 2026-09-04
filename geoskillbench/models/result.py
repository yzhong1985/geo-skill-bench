from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from geoskillbench.models.skill import LoadedSkillReference


StageStatus = Literal["PENDING", "RUNNING", "PASSED", "FAILED", "SKIPPED"]
OperationalStatus = Literal["not_started", "running", "succeeded", "failed", "timed_out", "cancelled"]
EvaluationVerdict = Literal["passed", "failed", "not_evaluable", "skipped"]
TerminationReason = Literal[
    "completed",
    "configuration_error",
    "environment_error",
    "agent_error",
    "empty_response",
    "unfinished",
    "turn_limit",
    "runtime_timeout",
    "assertion_error",
    "judge_unavailable",
    "judge_invalid",
    "archive_error",
    "cleanup_error",
    "runner_error",
]
ArchiveStatus = Literal["pending", "succeeded", "failed", "partial", "not_attempted"]
CleanupStatus = Literal["pending", "succeeded", "failed", "partial", "not_attempted"]
FailureSource = Literal["sut", "configuration", "environment", "platform", "judge", "archive", "cleanup"]


class FailureRecord(BaseModel):
    phase: str
    code: str
    message: str
    source: FailureSource = "platform"
    fatal: bool = True
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ToolCallRecord(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    status: Literal["success", "failed"]
    latency_ms: int | None = None
    error_message: str | None = None
    tool_type: Literal["mcp", "skill_internal"] = "mcp"
    recorded_at: str | None = None
    order: int | None = None


class AgentRunResult(BaseModel):
    final_response: str = ""
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    conversation: list[dict[str, Any]] = Field(default_factory=list)
    output_artifacts: dict[str, Any] = Field(default_factory=dict)
    status: Literal["passed", "failed", "timeout"] = "passed"
    error_message: str | None = None


class ExecutorSessionRequest(BaseModel):
    scenario_id: str
    skill_id: str | None = None
    skill_prompt: str | None = None
    test_context: dict[str, Any]
    tools: list[Any]
    agent: dict[str, Any] | None = None
    role_model_config: dict[str, Any] = Field(default_factory=dict)
    max_turns: int = 6
    timeout_seconds: int = 180
    memory_enabled: bool = False


class ExecutorSession(BaseModel):
    session_id: str
    executor_type: str
    scenario_id: str
    skill_id: str | None = None
    created_at: str
    runtime_mode: Literal["real", "compatibility"] = "real"
    runtime_metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutorStepResult(BaseModel):
    response: str = ""
    need_interaction: bool = False
    finished: bool = False
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    conversation: list[dict[str, Any]] = Field(default_factory=list)


class AssertionItemResult(BaseModel):
    type: str
    passed: bool
    message: str
    target: str | None = None
    actual: Any = None
    expected: Any = None
    backend: Literal["file", "postgis"] | None = None


class AssertionResult(BaseModel):
    passed: bool
    score: float
    items: list[AssertionItemResult] = Field(default_factory=list)
    status: Literal["passed", "failed", "skipped"] = "passed"


class JudgeResult(BaseModel):
    score: float = 0.0
    passed: bool = False
    reason: str = ""
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    judge_mode: Literal["llm", "rule-skill", "rule-agent", "disabled", "error"] = "rule-skill"
    model: str = ""
    status: Literal["passed", "failed", "skipped", "unavailable", "invalid"] = "passed"
    fallback_reason: str | None = None


class TestResult(BaseModel):
    run_id: str = ""
    scenario_id: str
    scenario_name: str
    status: Literal["passed", "failed"]
    duration_ms: int
    stage_results: dict[str, StageStatus]
    skill: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]]
    assertions: list[dict[str, Any]]
    judge: dict[str, Any]
    conversation: list[dict[str, Any]] = Field(default_factory=list)
    final_output: dict[str, Any] = Field(default_factory=dict)
    loaded_skill_references: list[LoadedSkillReference] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    operational_status: OperationalStatus = "succeeded"
    evaluation_verdict: EvaluationVerdict = "not_evaluable"
    termination_reason: TerminationReason = "completed"
    archive_status: ArchiveStatus = "not_attempted"
    cleanup_status: CleanupStatus = "not_attempted"
    failures: list[FailureRecord] = Field(default_factory=list)
