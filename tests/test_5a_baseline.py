from __future__ import annotations

from pathlib import Path

from geoskillbench.assertions.assertion_engine import AssertionEngine
from geoskillbench.loader.scenario_loader import ScenarioLoader
from geoskillbench.models.result import AssertionResult, FailureRecord, JudgeResult
from geoskillbench.models.test_context import TestContext as RunTestContext
from geoskillbench.recorder.execution_recorder import ExecutionRecorder
from geoskillbench.runner import _evaluation_verdict, _top_level_status
from geoskillbench.runtime.judge_runtime import JudgeEngine
from geoskillbench.security.paths import resolve_inside
from geoskillbench.security.redaction import redact

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_SCENARIO = REPO_ROOT / "scenarios" / "buffer_school_500m_5b_001.yml"


def test_empty_assertions_are_skipped_not_vacuous_pass() -> None:
    result = AssertionEngine().run(
        [],
        ExecutionRecorder(scenario_id="empty"),
        RunTestContext(scenario_id="empty", scenario_name="empty"),
    )
    assert result.status == "skipped"
    assert result.passed is False
    assert result.score == 0.0


def test_redact_nested_secrets_and_bearer() -> None:
    value = redact(
        {
            "headers": {"Authorization": "Bearer abc123"},
            "db_url": "postgresql://user:password@example.test:5432/geoskillbench",
            "message": "Authorization: Bearer xyz789",
        }
    )
    assert value["headers"]["Authorization"] == "[REDACTED]"
    assert "password" not in value["db_url"]
    assert "abc123" not in str(value)
    assert "xyz789" not in str(value)


def test_resolve_inside_rejects_sibling_prefix(tmp_path) -> None:
    root = tmp_path / "skills" / "package"
    root.mkdir(parents=True)
    safe = root / "references" / "guide.md"
    safe.parent.mkdir()
    safe.write_text("guide", encoding="utf-8")
    assert resolve_inside(root, "references/guide.md") == safe.resolve()
    try:
        resolve_inside(root, "../package_evil/secret.txt")
    except ValueError:
        pass
    else:
        raise AssertionError("sibling path must be rejected")


def test_assertion_result_legacy_default_remains_passed() -> None:
    result = AssertionResult(passed=True, score=1.0)
    assert result.status == "passed"


def _assertion(passed: bool, status: str = "passed") -> AssertionResult:
    return AssertionResult(passed=passed, score=1.0 if passed else 0.0, status=status)  # type: ignore[arg-type]


def _judge(passed: bool, *, mode: str = "llm", status: str = "passed") -> JudgeResult:
    return JudgeResult(score=1.0 if passed else 0.0, passed=passed, judge_mode=mode, status=status)  # type: ignore[arg-type]


class TestEvaluationVerdict:
    def test_execution_failure_never_passes(self) -> None:
        verdict = _evaluation_verdict(
            execution_ok=False,
            assertion_result=_assertion(True),
            judge_result=_judge(True),
        )
        assert verdict == "failed"

    def test_missing_results_are_not_evaluable(self) -> None:
        assert _evaluation_verdict(execution_ok=True, assertion_result=None, judge_result=_judge(True)) == "not_evaluable"
        assert _evaluation_verdict(execution_ok=True, assertion_result=_assertion(True), judge_result=None) == "not_evaluable"

    def test_skipped_assertions_with_disabled_judge_not_evaluable(self) -> None:
        verdict = _evaluation_verdict(
            execution_ok=True,
            assertion_result=_assertion(False, status="skipped"),
            judge_result=_judge(True, mode="disabled", status="skipped"),
        )
        assert verdict == "not_evaluable"

    def test_skipped_assertions_rely_on_judge(self) -> None:
        verdict = _evaluation_verdict(
            execution_ok=True,
            assertion_result=_assertion(False, status="skipped"),
            judge_result=_judge(True, mode="rule-skill"),
        )
        assert verdict == "passed"

    def test_judge_unavailable_is_not_evaluable_not_pass(self) -> None:
        verdict = _evaluation_verdict(
            execution_ok=True,
            assertion_result=_assertion(True),
            judge_result=_judge(False, mode="error", status="unavailable"),
        )
        assert verdict == "not_evaluable"

    def test_assertion_failure_fails_verdict(self) -> None:
        verdict = _evaluation_verdict(
            execution_ok=True,
            assertion_result=_assertion(False, status="failed"),
            judge_result=_judge(True),
        )
        assert verdict == "failed"

    def test_happy_path_passes(self) -> None:
        verdict = _evaluation_verdict(
            execution_ok=True,
            assertion_result=_assertion(True),
            judge_result=_judge(True),
        )
        assert verdict == "passed"


class TestTopLevelStatus:
    def test_passed_requires_clean_operational_state(self) -> None:
        assert _top_level_status("passed", "succeeded", "succeeded", "succeeded", []) == "passed"

    def test_fatal_failure_forces_failed(self) -> None:
        failures = [FailureRecord(phase="RUN_AGENT", code="agent_error", message="boom", source="sut")]
        assert _top_level_status("passed", "succeeded", "succeeded", "succeeded", failures) == "failed"

    def test_archive_failure_forces_failed(self) -> None:
        assert _top_level_status("passed", "succeeded", "failed", "succeeded", []) == "failed"

    def test_not_evaluable_is_failed_status(self) -> None:
        assert _top_level_status("not_evaluable", "succeeded", "succeeded", "succeeded", []) == "failed"


class TestJudgeSemantics:
    def _scenario(self):
        return ScenarioLoader().load(str(SAMPLE_SCENARIO))

    def _context(self) -> RunTestContext:
        return RunTestContext(scenario_id="t", scenario_name="t")

    def test_disabled_judge_is_skipped_not_formal_pass(self) -> None:
        scenario = self._scenario()
        scenario.judge.enabled = False
        result = JudgeEngine().evaluate(
            scenario, self._context(), ExecutionRecorder(scenario_id="t"), _assertion(True)
        )
        assert result.judge_mode == "disabled"
        assert result.status == "skipped"

    def test_explicit_rule_judge_produces_formal_verdict(self) -> None:
        scenario = self._scenario()
        scenario.runtime.judge_model = "rule-based-test"
        result = JudgeEngine().evaluate(
            scenario, self._context(), ExecutionRecorder(scenario_id="t"), _assertion(True)
        )
        assert result.judge_mode in {"rule-skill", "rule-agent"}
        assert result.status in {"passed", "failed"}

    def test_unavailable_llm_judge_does_not_fall_back_to_rule_pass(self) -> None:
        scenario = self._scenario()
        scenario.runtime.judge_model = "no-such-model-alias"
        result = JudgeEngine().evaluate(
            scenario, self._context(), ExecutionRecorder(scenario_id="t"), _assertion(True)
        )
        assert result.judge_mode == "error"
        assert result.status == "unavailable"
        assert result.passed is False


def test_report_generator_writes_run_directory(tmp_path: Path) -> None:
    from geoskillbench.models.result import TestResult
    from geoskillbench.reports.report_generator import ReportGenerator

    report_gen = ReportGenerator()
    test_result = TestResult(
        run_id="run_test_123",
        scenario_id="scenario_test",
        scenario_name="Test Scenario",
        status="passed",
        duration_ms=100,
        stage_results={},
        tool_calls=[],
        assertions=[],
        judge={},
        conversation=[],
        final_output={"final_response": "done"},
        loaded_skill_references=[],
        errors=[],
        operational_status="succeeded",
        evaluation_verdict="passed",
        termination_reason="completed",
        archive_status="succeeded",
        cleanup_status="succeeded",
        failures=[],
    )
    json_path, md_path = report_gen.write_reports(str(tmp_path), test_result)
    assert json_path == tmp_path / "runs" / "run_test_123" / "result.json"
    assert md_path == tmp_path / "runs" / "run_test_123" / "report.md"
    assert json_path.exists()
    assert md_path.exists()
    # 验证兼容旧路径产物
    assert (tmp_path / "json" / "scenario_test.json").exists()
    assert (tmp_path / "markdown" / "scenario_test.md").exists()

