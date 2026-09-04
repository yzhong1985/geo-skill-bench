from __future__ import annotations

import datetime
import difflib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from geoskillbench.loader.scenario_loader import ScenarioLoader
from geoskillbench.models.batch import (
    ATTRIBUTION_CATEGORIES,
    SKILL_PATCH_THRESHOLD,
    BatchAIDiagnostics,
    BatchResult,
    SkillPatchSuggestion,
)
from geoskillbench.runtime.llm import build_llm, load_models_config
from geoskillbench.runtime.llm_judge import extract_json

DEFAULT_ANALYST_MODEL = "deepseek-v4-flash"
MAX_RUNS_IN_PROMPT = 20
MAX_TOOL_SEQUENCE = 20
ROOT_DIR = Path(__file__).resolve().parents[2]


def resolve_analyst_model(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    aliases = (load_models_config().get("models") or {})
    if aliases:
        return next(iter(aliases))
    return DEFAULT_ANALYST_MODEL


def compress_run_record(run: dict[str, Any]) -> dict[str, Any]:
    """把单次 run 的 result.json 压成诊断输入，控制 token。"""
    tool_calls = run.get("tool_calls") or []
    sequence: list[str] = []
    tool_errors: list[str] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        name = str(call.get("tool_name") or "")
        if name:
            sequence.append(name)
        if call.get("status") != "success":
            err = call.get("error_message") or call.get("status") or "failed"
            tool_errors.append(f"{name}: {str(err)[:160]}")

    failed_assertions = [
        {"type": item.get("type"), "message": str(item.get("message") or "")[:200]}
        for item in (run.get("assertions") or [])
        if isinstance(item, dict) and not item.get("passed")
    ]
    judge = run.get("judge") if isinstance(run.get("judge"), dict) else {}
    final_output = run.get("final_output") if isinstance(run.get("final_output"), dict) else {}
    return {
        "run_id": run.get("run_id"),
        "scenario_id": run.get("scenario_id"),
        "verdict": run.get("evaluation_verdict"),
        "operational_status": run.get("operational_status"),
        "termination_reason": run.get("termination_reason"),
        "duration_ms": run.get("duration_ms"),
        "tool_sequence": sequence[:MAX_TOOL_SEQUENCE],
        "tool_errors": tool_errors[:5],
        "failed_assertions": failed_assertions[:8],
        "errors": [str(err)[:200] for err in (run.get("errors") or [])][:5],
        "judge_score": judge.get("score"),
        "judge_issues": [str(item)[:160] for item in (judge.get("issues") or [])][:5],
        "final_response_excerpt": str(final_output.get("final_response") or "")[:240],
    }


def _load_run_json(reports_dir: Path, run_id: str) -> dict[str, Any] | None:
    path = reports_dir / "runs" / run_id / "result.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _select_runs_for_prompt(batch_result: BatchResult) -> list[str]:
    records = list(batch_result.runs)
    if len(records) <= MAX_RUNS_IN_PROMPT:
        return [item.run_id for item in records]
    failed = [item.run_id for item in records if item.evaluation_verdict != "passed"]
    passed = [item.run_id for item in records if item.evaluation_verdict == "passed"]
    remaining = MAX_RUNS_IN_PROMPT - min(len(failed), MAX_RUNS_IN_PROMPT)
    selected = failed[:MAX_RUNS_IN_PROMPT]
    if remaining > 0:
        selected.extend(passed[:remaining])
    return selected[:MAX_RUNS_IN_PROMPT]


def _relative_skill_path(skill_path: Path, root_dir: Path) -> str:
    try:
        return skill_path.resolve().relative_to(root_dir.resolve()).as_posix()
    except ValueError:
        return skill_path.name


def _read_skill_file(scenario_path: Path, root_dir: Path) -> dict[str, str] | None:
    try:
        scenario = ScenarioLoader().load(str(scenario_path))
    except Exception:
        return None
    if scenario.skill is None:
        return None
    base = Path(scenario.__dict__.get("_base_path") or scenario_path.parent)
    skill_path = (base / scenario.skill.path).resolve()
    if skill_path.is_dir():
        entry = scenario.skill.entry or "SKILL.md"
        skill_path = (skill_path / entry).resolve()
    if not skill_path.is_file():
        return None
    try:
        content = skill_path.read_text(encoding="utf-8")
    except OSError:
        return None
    return {
        "skill_id": scenario.target.skill_id or skill_path.stem,
        "target_file": _relative_skill_path(skill_path, root_dir),
        "original_content": content,
    }


def collect_skills(batch_result: BatchResult, root_dir: Path) -> list[dict[str, str]]:
    seen: dict[str, dict[str, str]] = {}
    for raw_path in batch_result.request.scenarios:
        path = Path(raw_path)
        if not path.is_absolute():
            path = root_dir / path
        skill = _read_skill_file(path, root_dir)
        if skill is None:
            continue
        seen.setdefault(skill["target_file"], skill)
    return list(seen.values())


def build_evidence_hints(batch_result: BatchResult, compressed_runs: list[dict[str, Any]]) -> dict[str, Any]:
    summary = batch_result.summary
    assertion_types = Counter(
        str(item.get("type") or "unknown")
        for run in compressed_runs
        for item in (run.get("failed_assertions") or [])
    )
    trajectories = Counter(tuple(run.get("tool_sequence") or []) for run in compressed_runs)
    tool_error_runs = sum(1 for run in compressed_runs if run.get("tool_errors"))
    env_like = sum(
        1
        for run in compressed_runs
        if run.get("operational_status") in {"failed", "timed_out"}
        or run.get("termination_reason") in {"environment_error", "runtime_timeout", "configuration_error"}
    )
    return {
        "pass_rate": summary.pass_rate,
        "trajectory_entropy": summary.overall_variance.trajectory_entropy,
        "unique_trajectory_count": len(trajectories),
        "failed_assertion_types": dict(assertion_types),
        "tool_error_run_count": tool_error_runs,
        "env_like_run_count": env_like,
        "operational_distribution": summary.operational_distribution,
        "verdict_distribution": summary.verdict_distribution,
    }


def assemble_analyst_input(
    batch_result: BatchResult,
    *,
    reports_dir: Path | None = None,
    root_dir: Path | None = None,
) -> dict[str, Any]:
    reports_dir = Path(reports_dir or ROOT_DIR / "reports")
    root_dir = Path(root_dir or ROOT_DIR)
    summary = batch_result.summary
    selected_ids = _select_runs_for_prompt(batch_result)
    compressed: list[dict[str, Any]] = []
    for run_id in selected_ids:
        raw = _load_run_json(reports_dir, run_id)
        if raw is None:
            record = next((item for item in batch_result.runs if item.run_id == run_id), None)
            compressed.append(
                {
                    "run_id": run_id,
                    "scenario_id": record.scenario_id if record else "",
                    "verdict": record.evaluation_verdict if record else "",
                    "duration_ms": record.duration_ms if record else 0,
                    "tool_sequence": [],
                    "failed_assertions": [],
                    "missing_run_artifact": True,
                }
            )
            continue
        compressed.append(compress_run_record(raw))

    skills = collect_skills(batch_result, root_dir)
    unique_skill = skills[0] if len(skills) == 1 else None
    return {
        "batch_id": summary.batch_id,
        "created_at": summary.created_at,
        "status": summary.status,
        "total_runs": summary.total_runs,
        "passed_runs": summary.passed_runs,
        "failed_runs": summary.failed_runs,
        "not_evaluable_runs": summary.not_evaluable_runs,
        "pass_rate": summary.pass_rate,
        "verdict_distribution": summary.verdict_distribution,
        "operational_distribution": summary.operational_distribution,
        "overall_variance": summary.overall_variance.model_dump(),
        "by_scenario": {
            key: {
                "scenario_id": value.scenario_id,
                "total_runs": value.total_runs,
                "passed_runs": value.passed_runs,
                "failed_runs": value.failed_runs,
                "pass_rate": value.pass_rate,
                "trajectory_entropy": value.variance.trajectory_entropy,
            }
            for key, value in summary.by_scenario.items()
        },
        "evidence_hints": build_evidence_hints(batch_result, compressed),
        "runs": compressed,
        "skills": [
            {
                "skill_id": item["skill_id"],
                "target_file": item["target_file"],
                "original_content": item["original_content"],
            }
            for item in skills
        ],
        "patch_eligible": unique_skill is not None,
        "unique_skill": unique_skill,
    }


def validate_attribution(raw: Any) -> dict[str, float] | None:
    if not isinstance(raw, dict) or not raw:
        return None
    cleaned: dict[str, float] = {}
    for key, value in raw.items():
        if key not in ATTRIBUTION_CATEGORIES:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number < 0:
            return None
        cleaned[key] = round(number, 4)
    total = sum(cleaned.values())
    if total < 0.99 or total > 1.01:
        return None
    if abs(total - 1.0) > 1e-9 and total > 0:
        cleaned = {key: round(value / total, 4) for key, value in cleaned.items()}
        drift = round(1.0 - sum(cleaned.values()), 4)
        last_key = next(reversed(cleaned))
        cleaned[last_key] = round(cleaned[last_key] + drift, 4)
    return cleaned


def generate_unified_diff(target_file: str, original: str, updated: str) -> str:
    original_lines = original.splitlines(keepends=True)
    updated_lines = updated.splitlines(keepends=True)
    if original_lines and not original_lines[-1].endswith("\n"):
        original_lines[-1] += "\n"
    if updated_lines and not updated_lines[-1].endswith("\n"):
        updated_lines[-1] += "\n"
    diff = difflib.unified_diff(
        original_lines,
        updated_lines,
        fromfile=f"a/{target_file}",
        tofile=f"b/{target_file}",
        lineterm="\n",
    )
    return "".join(diff).rstrip() + ("\n" if original != updated else "")


def apply_patch_gate(
    attribution: dict[str, float],
    unique_skill: dict[str, str] | None,
    new_file_content: str | None,
    explanation: str,
) -> SkillPatchSuggestion | None:
    if unique_skill is None:
        return None
    if attribution.get("skill_prompt_issue", 0.0) < SKILL_PATCH_THRESHOLD:
        return None
    if not new_file_content or new_file_content.strip() == unique_skill["original_content"].strip():
        return None
    diff = generate_unified_diff(unique_skill["target_file"], unique_skill["original_content"], new_file_content)
    if not diff.strip():
        return None
    return SkillPatchSuggestion(
        skill_id=unique_skill["skill_id"],
        target_file=unique_skill["target_file"],
        diff_content=diff,
        explanation=explanation or "建议人工审阅后复制到 Skill 文件，并重新跑 batch 验证。",
    )


def unavailable_diagnostics(
    batch_result: BatchResult,
    *,
    model: str = "",
    error: str,
) -> BatchAIDiagnostics:
    summary = batch_result.summary
    return BatchAIDiagnostics(
        batch_id=summary.batch_id,
        created_at=summary.created_at or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        source="unavailable",
        model=model,
        summary_text=(
            f"批次共 {summary.total_runs} 次运行，通过率 {summary.pass_rate * 100:.1f}%。"
            "AI 诊断不可用，以下仅为统计摘要。"
        ),
        attribution_breakdown={},
        root_cause_analysis="",
        suggested_patch=None,
        error=error,
    )


def _system_prompt() -> str:
    categories = ", ".join(ATTRIBUTION_CATEGORIES)
    return (
        "你是 GeoSkillBench 的批次横向诊断专家。根据重复运行的聚合统计、压缩轨迹和 Skill 原文，"
        "解释失败与漂移，不要改写正式评测结论。\n"
        "只输出一个 JSON 对象，不要输出其他文字。格式：\n"
        "{\n"
        '  "summary_text": "宏观现象，2-4 句",\n'
        f'  "attribution_breakdown": {{类别: 占比}},\n'
        '  "root_cause_analysis": "根因分析，引用失败断言/工具序列/Skill 原文中的证据",\n'
        '  "suggested_patch": null 或 {\n'
        '    "skill_id": "技能ID",\n'
        '    "target_file": "skills/xxx.skill.yml",\n'
        '    "new_file_content": "基于原文修改后的完整 Skill 文件",\n'
        '    "explanation": "为什么改、改完必须重新跑 batch"\n'
        "  }\n"
        "}\n"
        f"attribution_breakdown 的 key 只能是：{categories}。"
        "数值为 0~1，总和必须等于 1。\n"
        "规则：\n"
        "1. 这是辅助分析，不能宣称改变 pass_rate 或 verdict。\n"
        "2. 仅当主因是 skill_prompt_issue（占比>=0.4）且输入中 patch_eligible=true 时才给 suggested_patch；"
        "否则 suggested_patch 必须为 null。\n"
        "3. new_file_content 必须从输入的 Skill 原文修改，禁止编造不存在的文件。\n"
        "4. 全通过且轨迹稳定时，可用 {\"unknown\": 1.0}，suggested_patch 为 null。\n"
        "5. evidence_hints 只是线索，不要当成已证实的归因百分比。"
    )


def _invoke_llm(model: str, payload: dict[str, Any]) -> dict[str, Any]:
    llm = build_llm(model, temperature=0.0, config=load_models_config())
    prompt = (
        f"{_system_prompt()}\n\n批次诊断输入 JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    response = llm.invoke(prompt)
    raw_text = getattr(response, "content", str(response))
    parsed = extract_json(raw_text)
    if not parsed:
        raise ValueError("LLM 未返回可解析的 JSON 对象")
    return parsed


def run_batch_ai_analyst(
    batch_result: BatchResult,
    model: str | None = None,
    *,
    reports_dir: Path | None = None,
    root_dir: Path | None = None,
) -> BatchAIDiagnostics:
    """对批次做横向 AI 诊断。LLM 不可用时返回 source=unavailable，不出假 patch。"""
    resolved_model = resolve_analyst_model(model)
    payload = assemble_analyst_input(batch_result, reports_dir=reports_dir, root_dir=root_dir)
    unique_skill = payload.get("unique_skill")
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    try:
        data = _invoke_llm(resolved_model, payload)
    except Exception as exc:
        return unavailable_diagnostics(batch_result, model=resolved_model, error=str(exc))

    attribution = validate_attribution(data.get("attribution_breakdown"))
    if attribution is None:
        attribution = {"unknown": 1.0}

    patch_payload = data.get("suggested_patch") if isinstance(data.get("suggested_patch"), dict) else {}
    new_content = patch_payload.get("new_file_content")
    patch = None
    if new_content and unique_skill:
        text = str(new_content)
        looks_like_diff = text.lstrip().startswith(("--- a/", "diff --git"))
        if not looks_like_diff:
            patch = apply_patch_gate(
                attribution,
                unique_skill,
                text,
                str(patch_payload.get("explanation") or data.get("root_cause_analysis") or ""),
            )

    return BatchAIDiagnostics(
        batch_id=batch_result.summary.batch_id,
        created_at=created_at,
        source="llm",
        model=resolved_model,
        summary_text=str(data.get("summary_text") or "完成横向诊断。"),
        attribution_breakdown=attribution,
        root_cause_analysis=str(data.get("root_cause_analysis") or ""),
        suggested_patch=patch,
    )
