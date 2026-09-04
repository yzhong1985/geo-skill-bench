from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from geoskillbench.batch_runner import BatchRunner
from geoskillbench.models.batch import BatchRequest
from geoskillbench.runner import STAGES, TestRunner
from geoskillbench.security.redaction import redact


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class TaskState:
    task_id: str
    scenario_path: str
    output_dir: str
    run_config: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    current_stage: str | None = None
    stage_results: dict[str, str] = field(default_factory=lambda: {stage: "PENDING" for stage in STAGES})
    result: dict[str, Any] | None = None
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)

    def snapshot(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "run_id": self.task_id,
            "scenario_path": self.scenario_path,
            "output_dir": self.output_dir,
            "run_config": dict(self.run_config),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "current_stage": self.current_stage,
            "stage_results": dict(self.stage_results),
            "result": self.result,
            "error": self.error,
        }


@dataclass
class BatchTaskState:
    batch_id: str
    request: dict[str, Any]
    status: str = "pending"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    total_runs: int = 0
    completed_runs: int = 0
    current_scenario: str | None = None
    current_iteration: int | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)

    def snapshot(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "request": dict(self.request),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "total_runs": self.total_runs,
            "completed_runs": self.completed_runs,
            "current_scenario": self.current_scenario,
            "current_iteration": self.current_iteration,
            "result": self.result,
            "error": self.error,
        }


class TaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskState] = {}
        self._batch_tasks: dict[str, BatchTaskState] = {}

    def list_tasks(self) -> list[dict[str, Any]]:
        return [task.snapshot() for task in sorted(self._tasks.values(), key=lambda item: item.created_at, reverse=True)]

    def get_task(self, task_id: str) -> TaskState | None:
        return self._tasks.get(task_id)

    async def create_task(self, scenario_path: str, output_dir: str, run_config: dict[str, Any] | None = None) -> TaskState:
        task = TaskState(task_id=uuid4().hex, scenario_path=scenario_path, output_dir=output_dir, run_config=run_config or {})
        self._tasks[task.task_id] = task
        await self._push_event(task, {"type": "task_created", "task": task.snapshot()})
        asyncio.create_task(self._run_task(task))
        return task

    async def event_stream(self, task_id: str):
        task = self._tasks[task_id]
        next_index = 0
        while True:
            async with task.condition:
                if next_index >= len(task.events) and task.status not in {"completed", "failed"}:
                    try:
                        await asyncio.wait_for(task.condition.wait(), timeout=15)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                while next_index < len(task.events):
                    event = task.events[next_index]
                    next_index += 1
                    yield self._format_sse(event)
                if task.status in {"completed", "failed"} and next_index >= len(task.events):
                    break

    # ---------- Batch Task 生命周期 ----------

    def list_batch_tasks(self) -> list[dict[str, Any]]:
        return [b.snapshot() for b in sorted(self._batch_tasks.values(), key=lambda item: item.created_at, reverse=True)]

    def get_batch_task(self, batch_id: str) -> BatchTaskState | None:
        return self._batch_tasks.get(batch_id)

    async def create_batch_task(self, batch_req: BatchRequest) -> BatchTaskState:
        batch_id = f"batch_{uuid4().hex[:12]}"
        total_runs = len(batch_req.scenarios) * batch_req.repeat_count
        task = BatchTaskState(
            batch_id=batch_id,
            request=batch_req.model_dump(),
            total_runs=total_runs,
        )
        self._batch_tasks[batch_id] = task
        await self._push_batch_event(task, {"type": "batch_created", "batch": task.snapshot()})
        asyncio.create_task(self._run_batch_task(task, batch_req))
        return task

    async def batch_event_stream(self, batch_id: str):
        task = self._batch_tasks[batch_id]
        next_index = 0
        while True:
            async with task.condition:
                if next_index >= len(task.events) and task.status not in {"completed", "failed"}:
                    try:
                        await asyncio.wait_for(task.condition.wait(), timeout=15)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                while next_index < len(task.events):
                    event = task.events[next_index]
                    next_index += 1
                    yield self._format_sse(event)
                if task.status in {"completed", "failed"} and next_index >= len(task.events):
                    break

    async def _run_batch_task(self, task: BatchTaskState, req: BatchRequest) -> None:
        loop = asyncio.get_running_loop()
        task.status = "running"
        task.updated_at = utc_now()
        await self._push_batch_event(task, {"type": "batch_started", "batch": task.snapshot()})

        def emit(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(asyncio.create_task, self._handle_batch_runner_event(task.batch_id, event))

        try:
            batch_runner = BatchRunner()
            batch_result = await asyncio.to_thread(batch_runner.run_batch, req, task.batch_id, emit)
            task.status = "completed"
            task.result = redact(batch_result.model_dump())
            task.updated_at = utc_now()
            await self._push_batch_event(task, {"type": "batch_finished", "batch": task.snapshot()})
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
            task.updated_at = utc_now()
            await self._push_batch_event(task, {"type": "batch_finished", "batch": task.snapshot()})

    async def _handle_batch_runner_event(self, batch_id: str, event: dict[str, Any]) -> None:
        task = self._batch_tasks.get(batch_id)
        if not task:
            return
        task.updated_at = utc_now()
        if event.get("type") == "batch_item_start":
            task.current_scenario = event.get("scenario")
            task.current_iteration = event.get("iteration")
        elif event.get("type") == "batch_item_complete":
            task.completed_runs = event.get("current_index", task.completed_runs + 1)
        await self._push_batch_event(task, redact({"type": event.get("type", "batch_event"), "batch": task.snapshot(), "payload": event}))

    async def _push_batch_event(self, task: BatchTaskState, event: dict[str, Any]) -> None:
        async with task.condition:
            task.events.append(event)
            task.condition.notify_all()

    # ---------- 内部 Runner 执行与转发 ----------

    async def _run_task(self, task: TaskState) -> None:
        loop = asyncio.get_running_loop()
        task.status = "running"
        task.updated_at = utc_now()
        await self._push_event(task, {"type": "task_started", "task": task.snapshot()})

        def emit(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(asyncio.create_task, self._handle_runner_event(task.task_id, event))

        try:
            result = await asyncio.to_thread(TestRunner().run, task.scenario_path, task.output_dir, emit, task.run_config, task.task_id)
            task.status = "completed"
            task.result = redact(result.model_dump())
            task.updated_at = utc_now()
            if result.status != "passed":
                task.error = "; ".join(result.errors) if result.errors else f"Evaluation: {result.evaluation_verdict}"
            await self._push_event(task, {"type": "task_finished", "task": task.snapshot()})
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
            task.updated_at = utc_now()
            await self._push_event(task, {"type": "task_finished", "task": task.snapshot()})

    async def _handle_runner_event(self, task_id: str, event: dict[str, Any]) -> None:
        task = self._tasks[task_id]
        task.updated_at = utc_now()
        if "stage" in event:
            task.current_stage = event["stage"]
        if "stage_results" in event:
            task.stage_results = dict(event["stage_results"])
        if event.get("type") == "error":
            task.error = event.get("message")
        if event.get("type") == "result":
            task.result = event.get("result")
        await self._push_event(task, redact({"type": event.get("type", "event"), "task": task.snapshot(), "payload": event}))

    async def _push_event(self, task: TaskState, event: dict[str, Any]) -> None:
        async with task.condition:
            task.events.append(event)
            task.condition.notify_all()

    def _format_sse(self, event: dict[str, Any]) -> str:
        return f"event: {event.get('type', 'message')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
