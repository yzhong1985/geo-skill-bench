from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from geoskillbench.models.batch import (
    BatchRequest,
    BatchResult,
    BatchRunRecord,
    BatchSummary,
    VarianceMetrics,
)
from geoskillbench.runtime.batch_analyst import (
    apply_patch_gate,
    assemble_analyst_input,
    compress_run_record,
    generate_unified_diff,
    run_batch_ai_analyst,
    unavailable_diagnostics,
    validate_attribution,
)


SKILL_ORIGINAL = """id: demo_buffer_analysis
name: demo
instructions: |
  调用工具完成缓冲区。
"""


def _batch_result(
    *,
    batch_id: str = "batch_test",
    scenarios: list[str] | None = None,
    pass_rate: float = 0.0,
    failed: int = 3,
    passed: int = 0,
    runs: list[BatchRunRecord] | None = None,
) -> BatchResult:
    total = passed + failed
    summary = BatchSummary(
        batch_id=batch_id,
        created_at="2026-09-02T00:00:00+00:00",
        status="failed" if passed == 0 else ("succeeded" if failed == 0 else "partial"),
        total_runs=total,
        passed_runs=passed,
        failed_runs=failed,
        pass_rate=pass_rate,
        overall_variance=VarianceMetrics(trajectory_entropy=1.2 if failed else 0.0),
    )
    return BatchResult(
        batch_id=batch_id,
        request=BatchRequest(scenarios=scenarios or ["scenarios/demo.yml"], repeat_count=total or 1),
        summary=summary,
        runs=runs or [],
    )


def test_validate_attribution_accepts_known_keys() -> None:
    parsed = validate_attribution(
        {
            "skill_prompt_issue": 0.4,
            "agent_drift": 0.3,
            "env_error": 0.1,
            "assertion_or_scenario": 0.1,
            "harness_variance": 0.05,
            "unknown": 0.05,
        }
    )
    assert parsed is not None
    assert abs(sum(parsed.values()) - 1.0) < 1e-6


def test_validate_attribution_rejects_unknown_key_and_bad_sum() -> None:
    assert validate_attribution({"perfect_execution": 1.0}) is None
    assert validate_attribution({"skill_prompt_issue": 0.2, "agent_drift": 0.2}) is None
    assert validate_attribution({}) is None


def test_compress_run_record_keeps_failures_and_tool_sequence() -> None:
    compressed = compress_run_record(
        {
            "run_id": "r1",
            "scenario_id": "demo",
            "evaluation_verdict": "failed",
            "operational_status": "failed",
            "termination_reason": "environment_error",
            "duration_ms": 100,
            "tool_calls": [
                {"tool_name": "create_buffer", "status": "failed", "error_message": "timeout"},
                {"tool_name": "publish_map", "status": "success"},
            ],
            "assertions": [
                {"type": "tool_called", "passed": True, "message": "ok"},
                {"type": "final_response_contains", "passed": False, "message": "missing 500"},
            ],
            "errors": ["boom"],
            "judge": {"score": 0.2, "issues": ["no handle"]},
            "final_output": {"final_response": "失败"},
        }
    )
    assert compressed["tool_sequence"] == ["create_buffer", "publish_map"]
    assert compressed["failed_assertions"][0]["type"] == "final_response_contains"
    assert "timeout" in compressed["tool_errors"][0]


def test_assemble_input_reads_run_and_skill(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    skill_file = skill_dir / "demo.skill.yml"
    skill_file.write_text(SKILL_ORIGINAL, encoding="utf-8")
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    (scenario_dir / "demo.yml").write_text(
        "\n".join(
            [
                "id: demo",
                "name: demo",
                "version: 1.0.0",
                "type: agent_skill_test",
                "target:",
                "  skill_id: demo_buffer_analysis",
                "user_task: buffer",
                "skill:",
                "  load_mode: file",
                "  path: ../skills/demo.skill.yml",
            ]
        ),
        encoding="utf-8",
    )
    run_id = "batch_test_demo_1"
    run_dir = tmp_path / "reports" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "scenario_id": "demo",
                "evaluation_verdict": "failed",
                "operational_status": "failed",
                "tool_calls": [{"tool_name": "create_buffer", "status": "failed", "error_message": "x"}],
                "assertions": [{"type": "tool_called", "passed": False, "message": "missing tool"}],
                "judge": {"score": 0.1, "issues": ["no tool"]},
                "final_output": {"final_response": "failed"},
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    batch = _batch_result(
        scenarios=["scenarios/demo.yml"],
        runs=[
            BatchRunRecord(
                run_id=run_id,
                scenario_id="demo",
                scenario_name="demo",
                iteration=1,
                status="failed",
                evaluation_verdict="failed",
                duration_ms=10,
                tool_call_count=1,
            )
        ],
    )
    payload = assemble_analyst_input(batch, reports_dir=tmp_path / "reports", root_dir=tmp_path)
    assert payload["patch_eligible"] is True
    assert payload["unique_skill"]["skill_id"] == "demo_buffer_analysis"
    assert "调用工具完成缓冲区" in payload["unique_skill"]["original_content"]
    assert payload["runs"][0]["tool_sequence"] == ["create_buffer"]
    assert payload["runs"][0]["failed_assertions"][0]["type"] == "tool_called"


def test_patch_gate_requires_skill_share_and_real_diff() -> None:
    skill = {
        "skill_id": "demo_buffer_analysis",
        "target_file": "skills/demo.skill.yml",
        "original_content": SKILL_ORIGINAL,
    }
    updated = SKILL_ORIGINAL + "  必须调用 create_buffer。\n"
    assert apply_patch_gate({"agent_drift": 1.0}, skill, updated, "x") is None
    assert apply_patch_gate({"skill_prompt_issue": 0.5}, None, updated, "x") is None
    assert apply_patch_gate({"skill_prompt_issue": 0.5}, skill, SKILL_ORIGINAL, "x") is None
    patch = apply_patch_gate({"skill_prompt_issue": 0.5}, skill, updated, "补强约束")
    assert patch is not None
    assert patch.diff_content.startswith("--- a/skills/demo.skill.yml")
    assert "必须调用 create_buffer" in patch.diff_content


def test_generate_unified_diff_uses_real_file_content() -> None:
    diff = generate_unified_diff("skills/a.yml", "a\n", "a\nb\n")
    assert "+b" in diff
    assert diff.startswith("--- a/skills/a.yml")


def test_unavailable_has_no_patch() -> None:
    diag = unavailable_diagnostics(_batch_result(), model="m", error="no llm")
    assert diag.source == "unavailable"
    assert diag.suggested_patch is None
    assert diag.attribution_breakdown == {}
    assert "no llm" in (diag.error or "")


def test_run_analyst_parses_llm_json_and_builds_real_diff(tmp_path: Path) -> None:
    skill_file = tmp_path / "skills" / "demo.skill.yml"
    skill_file.parent.mkdir()
    skill_file.write_text(SKILL_ORIGINAL, encoding="utf-8")
    scenario = tmp_path / "scenarios" / "demo.yml"
    scenario.parent.mkdir()
    scenario.write_text(
        "\n".join(
            [
                "id: demo",
                "name: demo",
                "version: 1.0.0",
                "type: agent_skill_test",
                "target:",
                "  skill_id: demo_buffer_analysis",
                "user_task: buffer",
                "skill:",
                "  load_mode: file",
                "  path: ../skills/demo.skill.yml",
            ]
        ),
        encoding="utf-8",
    )
    batch = _batch_result(scenarios=["scenarios/demo.yml"])
    updated = SKILL_ORIGINAL + "  【强制】必须调用 create_buffer。\n"
    llm_payload = {
        "summary_text": "多次未调用关键工具",
        "attribution_breakdown": {"skill_prompt_issue": 0.6, "agent_drift": 0.4},
        "root_cause_analysis": "Skill 未强制工具调用",
        "suggested_patch": {
            "skill_id": "demo_buffer_analysis",
            "target_file": "skills/demo.skill.yml",
            "new_file_content": updated,
            "explanation": "补强制规则，改完需重跑 batch",
        },
    }

    class _Resp:
        content = "```json\n" + json.dumps(llm_payload, ensure_ascii=False) + "\n```"

    with patch("geoskillbench.runtime.batch_analyst.build_llm") as mock_build:
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _Resp()
        mock_build.return_value = mock_llm
        with patch("geoskillbench.runtime.batch_analyst.load_models_config", return_value={"models": {"m": {}}}):
            diag = run_batch_ai_analyst(batch, model="m", reports_dir=tmp_path / "reports", root_dir=tmp_path)

    assert diag.source == "llm"
    assert diag.attribution_breakdown["skill_prompt_issue"] == 0.6
    assert diag.suggested_patch is not None
    assert "create_buffer" in diag.suggested_patch.diff_content
    assert skill_file.read_text(encoding="utf-8") == SKILL_ORIGINAL


def test_run_analyst_drops_patch_when_skill_not_primary(tmp_path: Path) -> None:
    skill_file = tmp_path / "skills" / "demo.skill.yml"
    skill_file.parent.mkdir()
    skill_file.write_text(SKILL_ORIGINAL, encoding="utf-8")
    scenario = tmp_path / "scenarios" / "demo.yml"
    scenario.parent.mkdir()
    scenario.write_text(
        "\n".join(
            [
                "id: demo",
                "name: demo",
                "version: 1.0.0",
                "type: agent_skill_test",
                "target:",
                "  skill_id: demo_buffer_analysis",
                "user_task: buffer",
                "skill:",
                "  load_mode: file",
                "  path: ../skills/demo.skill.yml",
            ]
        ),
        encoding="utf-8",
    )
    batch = _batch_result(scenarios=["scenarios/demo.yml"])
    llm_payload = {
        "summary_text": "环境抖动",
        "attribution_breakdown": {"env_error": 0.7, "skill_prompt_issue": 0.3},
        "root_cause_analysis": "MCP 超时",
        "suggested_patch": {
            "new_file_content": SKILL_ORIGINAL + "x",
            "explanation": "should be dropped",
        },
    }

    class _Resp:
        content = json.dumps(llm_payload)

    with patch("geoskillbench.runtime.batch_analyst.build_llm") as mock_build:
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _Resp()
        mock_build.return_value = mock_llm
        with patch("geoskillbench.runtime.batch_analyst.load_models_config", return_value={"models": {"m": {}}}):
            diag = run_batch_ai_analyst(batch, model="m", reports_dir=tmp_path / "reports", root_dir=tmp_path)
    assert diag.suggested_patch is None
    assert diag.attribution_breakdown["env_error"] == 0.7


def test_run_analyst_llm_failure_is_unavailable() -> None:
    with patch("geoskillbench.runtime.batch_analyst.build_llm", side_effect=RuntimeError("down")):
        with patch("geoskillbench.runtime.batch_analyst.load_models_config", return_value={"models": {"m": {}}}):
            diag = run_batch_ai_analyst(_batch_result(), model="m")
    assert diag.source == "unavailable"
    assert diag.suggested_patch is None
    assert "down" in (diag.error or "")


def test_invalid_attribution_falls_back_to_unknown(tmp_path: Path) -> None:
    class _Resp:
        content = json.dumps(
            {
                "summary_text": "x",
                "attribution_breakdown": {"perfect_execution": 1.0},
                "root_cause_analysis": "y",
            }
        )

    with patch("geoskillbench.runtime.batch_analyst.build_llm") as mock_build:
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _Resp()
        mock_build.return_value = mock_llm
        with patch("geoskillbench.runtime.batch_analyst.load_models_config", return_value={"models": {"m": {}}}):
            diag = run_batch_ai_analyst(_batch_result(), model="m", reports_dir=tmp_path, root_dir=tmp_path)
    assert diag.attribution_breakdown == {"unknown": 1.0}
    assert diag.suggested_patch is None
