from __future__ import annotations

from typing import Any

from geoskillbench.models.result import AssertionItemResult, AssertionResult
from geoskillbench.models.scenario import AssertionConfig
from geoskillbench.models.test_context import TestContext
from geoskillbench.recorder.execution_recorder import ExecutionRecorder
from geoskillbench.assertions.result_comparator import ResultComparator
from geoskillbench.assertions.sql_result_comparator import PostgisResultComparator, dataset_sql_name


class AssertionEngine:
    def __init__(
        self,
        comparator: ResultComparator | None = None,
        sql_comparator: PostgisResultComparator | None = None,
        result_locator: Any | None = None,
    ) -> None:
        self.comparator = comparator or ResultComparator()
        self.sql_comparator = sql_comparator
        self.result_locator = result_locator

    def run(
        self,
        assertions: list[AssertionConfig],
        recorder: ExecutionRecorder,
        test_context: TestContext,
    ) -> AssertionResult:
        if not assertions:
            return AssertionResult(passed=False, score=0.0, items=[], status="skipped")
        items = [self._run_single(assertion, recorder, test_context) for assertion in assertions]
        passed_count = sum(1 for item in items if item.passed)
        score = round(passed_count / len(items), 2)
        return AssertionResult(passed=passed_count == len(items), score=score, items=items, status="passed" if passed_count == len(items) else "failed")

    def _run_single(
        self,
        assertion: AssertionConfig,
        recorder: ExecutionRecorder,
        test_context: TestContext,
    ) -> AssertionItemResult:
        handlers = {
            "skill_loaded": self._skill_loaded,
            "tool_available": self._tool_available,
            "tool_called": self._tool_called,
            "tool_sequence": self._tool_sequence,
            "tool_argument_equals": self._tool_argument_equals,
            "result_dataset_exists": self._result_dataset_exists,
            "result_geometry_type_in": self._result_geometry_type_in,
            "final_response_contains": self._final_response_contains,
            "skill_reference_loaded": self._skill_reference_loaded,
            "skill_reference_not_loaded": self._skill_reference_not_loaded,
            "skill_reference_loaded_before_tool": self._skill_reference_loaded_before_tool,
            "skill_reference_load_count_less_than": self._skill_reference_load_count_less_than,
            "result_overlap_ratio": self._result_overlap_ratio,
            "result_area_error_max": self._result_area_error_max,
            "result_distance_max": self._result_distance_max,
            "result_fields_match": self._result_fields_match,
            "result_feature_count": self._result_feature_count,
        }
        if assertion.type not in handlers:
            return AssertionItemResult(
                type=assertion.type,
                passed=False,
                message=f"Unsupported assertion type: {assertion.type}",
                target=assertion.tool or assertion.alias or assertion.target,
            )
        return handlers[assertion.type](assertion, recorder, test_context)

    def _skill_loaded(self, assertion: AssertionConfig, recorder: ExecutionRecorder, _: TestContext) -> AssertionItemResult:
        passed = assertion.skill_id in recorder.skill_loaded
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Skill {'was' if passed else 'was not'} loaded: {assertion.skill_id}",
            target=assertion.skill_id,
        )

    def _tool_available(self, assertion: AssertionConfig, _: ExecutionRecorder, test_context: TestContext) -> AssertionItemResult:
        passed = assertion.tool in test_context.mcp_tools and test_context.mcp_tools[assertion.tool].available
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Tool {'is' if passed else 'is not'} available: {assertion.tool}",
            target=assertion.tool,
        )

    def _tool_called(self, assertion: AssertionConfig, recorder: ExecutionRecorder, _: TestContext) -> AssertionItemResult:
        passed = any(call.tool_name == assertion.tool for call in recorder.tool_calls)
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Tool {'was' if passed else 'was not'} called: {assertion.tool}",
            target=assertion.tool,
        )

    def _tool_sequence(self, assertion: AssertionConfig, recorder: ExecutionRecorder, _: TestContext) -> AssertionItemResult:
        called = [call.tool_name for call in recorder.tool_calls]
        sequence = assertion.sequence
        position = 0
        for tool_name in called:
            if position < len(sequence) and tool_name == sequence[position]:
                position += 1
        passed = position == len(sequence)
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Tool sequence {'matched' if passed else 'did not match'}: {sequence}",
            target=" -> ".join(sequence),
        )

    def _tool_argument_equals(self, assertion: AssertionConfig, recorder: ExecutionRecorder, _: TestContext) -> AssertionItemResult:
        relevant_calls = [call for call in recorder.tool_calls if call.tool_name == assertion.tool]
        passed = any(self._normalize(call.arguments.get(assertion.argument)) == self._normalize(assertion.value) for call in relevant_calls)
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Tool argument {'matched' if passed else 'did not match'} for {assertion.tool}.{assertion.argument}",
            target=assertion.tool,
        )

    def _result_dataset_exists(self, assertion: AssertionConfig, recorder: ExecutionRecorder, test_context: TestContext) -> AssertionItemResult:
        dataset_exists = assertion.alias in recorder.final_output.get("datasets", {}) or assertion.alias in test_context.datasets
        return AssertionItemResult(
            type=assertion.type,
            passed=dataset_exists,
            message=f"Result dataset {'exists' if dataset_exists else 'does not exist'}: {assertion.alias}",
            target=assertion.alias,
        )

    def _result_geometry_type_in(self, assertion: AssertionConfig, recorder: ExecutionRecorder, test_context: TestContext) -> AssertionItemResult:
        dataset = recorder.final_output.get("datasets", {}).get(assertion.target) or test_context.datasets.get(assertion.target)
        geometry_type = dataset.geometry_type if dataset else None
        passed = geometry_type in assertion.values
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Geometry type {'matched' if passed else 'did not match'} for {assertion.target}: {geometry_type}",
            target=assertion.target,
        )

    def _final_response_contains(self, assertion: AssertionConfig, recorder: ExecutionRecorder, _: TestContext) -> AssertionItemResult:
        response = recorder.final_output.get("final_response", "")
        # 兼容两种写法：values 列表（规范）或 value 单值（曾长期被契约文档示例误用）。
        expected = assertion.values if assertion.values else ([assertion.value] if assertion.value is not None else [])
        if not expected:
            return AssertionItemResult(
                type=assertion.type,
                passed=False,
                message="final_response_contains requires 'values' or 'value' to be configured.",
            )
        passed = all(str(value) in response for value in expected)
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Final response {'contains' if passed else 'does not contain'} expected values: {expected}",
        )

    def _skill_reference_loaded(self, assertion: AssertionConfig, recorder: ExecutionRecorder, _: TestContext) -> AssertionItemResult:
        passed = any(reference.path == assertion.path for reference in recorder.loaded_skill_references)
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Skill reference {'was' if passed else 'was not'} loaded: {assertion.path}",
            target=assertion.path,
        )

    def _skill_reference_not_loaded(self, assertion: AssertionConfig, recorder: ExecutionRecorder, _: TestContext) -> AssertionItemResult:
        passed = all(reference.path != assertion.path for reference in recorder.loaded_skill_references)
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Skill reference {'was not' if passed else 'was'} loaded: {assertion.path}",
            target=assertion.path,
        )

    def _skill_reference_loaded_before_tool(self, assertion: AssertionConfig, recorder: ExecutionRecorder, _: TestContext) -> AssertionItemResult:
        reference_order = next((reference.order for reference in recorder.loaded_skill_references if reference.path == assertion.reference), None)
        tool_order = next((call.order for call in recorder.tool_calls if call.tool_name == assertion.tool), None)
        passed = reference_order is not None and tool_order is not None and reference_order < tool_order
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Skill reference {'was' if passed else 'was not'} loaded before tool: {assertion.reference} -> {assertion.tool}",
            target=assertion.reference,
        )

    def _skill_reference_load_count_less_than(self, assertion: AssertionConfig, recorder: ExecutionRecorder, _: TestContext) -> AssertionItemResult:
        limit = int(assertion.value)
        load_count = len(recorder.loaded_skill_references)
        passed = load_count < limit
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Skill reference load count {load_count} {'is' if passed else 'is not'} less than {limit}",
        )

    # ---------- 结果内容断言（result_*） ----------

    def _result_overlap_ratio(self, assertion: AssertionConfig, recorder: ExecutionRecorder, test_context: TestContext) -> AssertionItemResult:
        return self._run_result_compare("overlap_ratio", assertion, recorder, test_context)

    def _result_area_error_max(self, assertion: AssertionConfig, recorder: ExecutionRecorder, test_context: TestContext) -> AssertionItemResult:
        return self._run_result_compare("area_error", assertion, recorder, test_context)

    def _result_distance_max(self, assertion: AssertionConfig, recorder: ExecutionRecorder, test_context: TestContext) -> AssertionItemResult:
        return self._run_result_compare("hausdorff_distance", assertion, recorder, test_context)

    def _result_fields_match(self, assertion: AssertionConfig, recorder: ExecutionRecorder, test_context: TestContext) -> AssertionItemResult:
        return self._run_result_compare("fields", assertion, recorder, test_context)

    def _result_feature_count(self, assertion: AssertionConfig, recorder: ExecutionRecorder, test_context: TestContext) -> AssertionItemResult:
        return self._run_result_compare("feature_count", assertion, recorder, test_context)

    def _run_result_compare(
        self,
        metric: str,
        assertion: AssertionConfig,
        recorder: ExecutionRecorder,
        test_context: TestContext,
    ) -> AssertionItemResult:
        # 结果数据集别名缺省回落 buffer_result（skill 流里 create_buffer 的默认产出别名），
        # 手写 yml / 前端表单不带 target 也能定位结果数据集；显式写了则用显式值。
        target = assertion.target or "buffer_result"
        result_dataset = recorder.final_output.get("datasets", {}).get(target) or test_context.datasets.get(target)
        if result_dataset is None:
            return AssertionItemResult(
                type=assertion.type,
                passed=False,
                message=f"Result dataset not found: {target}",
                target=target,
            )
        reference_dataset = test_context.reference_datasets.get(assertion.reference)
        if reference_dataset is None:
            return AssertionItemResult(
                type=assertion.type,
                passed=False,
                message=f"Reference dataset not found: {assertion.reference}",
                target=target,
            )
        params = {k: v for k, v in {
            "min": assertion.min,
            "max_ratio": assertion.max_ratio,
            "max_meters": assertion.max_meters,
            "mode": assertion.mode,
            "count": assertion.count,
        }.items() if v is not None}
        try:
            location = self.result_locator(target) if callable(self.result_locator) else None
            result_table = dataset_sql_name(result_dataset, location)
            reference_table = dataset_sql_name(reference_dataset)
            use_postgis = (
                self.sql_comparator is not None
                and bool(result_table)
                and bool(reference_table)
            )
            if use_postgis:
                cmp = self.sql_comparator.compare(result_table, reference_table, metric, **params)
                backend = "postgis"
            elif getattr(result_dataset, "path", None) or getattr(reference_dataset, "path", None):
                cmp = self.comparator.compare(result_dataset, reference_dataset, metric, **params)
                backend = "file"
            elif self.sql_comparator is None:
                raise ValueError("result_* 库内比对需要 GEO_EVAL_DATABASE_URL（结果库，不是 DATABASE_URL 报告库）")
            else:
                raise ValueError("in-db comparison requires result and reference table names")
        except Exception as exc:
            return AssertionItemResult(
                type=assertion.type,
                passed=False,
                message=f"Result comparison failed: {exc}",
                target=target,
            )
        return AssertionItemResult(
            type=assertion.type,
            passed=cmp.passed,
            message=cmp.detail,
            target=target,
            actual=cmp.actual,
            expected=cmp.expected,
            backend=backend,
        )

    def _normalize(self, value: Any) -> Any:
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value
