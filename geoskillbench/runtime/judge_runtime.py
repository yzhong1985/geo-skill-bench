from __future__ import annotations

from geoskillbench.models.result import AssertionResult, JudgeResult
from geoskillbench.models.scenario import Scenario
from geoskillbench.models.test_context import TestContext
from geoskillbench.recorder.execution_recorder import ExecutionRecorder
from geoskillbench.runtime.askback import classify_external_reply
from geoskillbench.runtime.llm import build_llm, load_models_config
from geoskillbench.runtime.llm_judge import LlmJudgeUnavailable, run_llm_judge


class JudgeEngine:
    def evaluate(
        self,
        scenario: Scenario,
        test_context: TestContext,
        recorder: ExecutionRecorder,
        assertion_result: AssertionResult,
    ) -> JudgeResult:
        if not scenario.judge.enabled:
            # 显式关闭 judge 时直接以断言结果通过，跳过针对内部 skill 产物的启发式扣分
            return JudgeResult(
                score=assertion_result.score,
                passed=assertion_result.passed,
                reason="Judge disabled by scenario config.",
                judge_mode="disabled",
                status="skipped",
            )

        # 1) 显式规则 Judge：rule-based-* 是用户主动选择的判定模式，不是 LLM fallback。
        judge_model = scenario.runtime.judge_model or scenario.runtime.agent_model
        if not judge_model:
            return self._unavailable_judge("未配置真实 judge 模型（judge_model/agent_model 为空）")
        if judge_model.startswith("rule-based-"):
            return self._rule_judge(scenario, assertion_result, recorder)

        try:
            llm = build_llm(judge_model, temperature=0.0, config=load_models_config())
            llm_result = run_llm_judge(scenario, recorder, assertion_result, llm, judge_model=judge_model)
            llm_result.passed = (
                llm_result.score >= scenario.pass_criteria.judge_score_min
                and assertion_result.status != "failed"
            )
            llm_result.status = "passed" if llm_result.passed else "failed"
            llm_result.status = "passed" if llm_result.passed else "failed"
            llm_result.status = "passed" if llm_result.passed else "failed"
            llm_result.status = "passed" if llm_result.passed else "failed"
            llm_result.status = "passed" if llm_result.passed else "failed"
            llm_result.status = "passed" if llm_result.passed else "failed"
            llm_result.status = "passed" if llm_result.passed else "failed"
            llm_result.status = "passed" if llm_result.passed else "failed"
            llm_result.status = "passed" if llm_result.passed else "failed"
            llm_result.status = "passed" if llm_result.passed else "failed"
            llm_result.status = "passed" if llm_result.passed else "failed"
            llm_result.status = "passed" if llm_result.passed else "failed"
            llm_result.status = "passed" if llm_result.passed else "failed"
            if not llm_result.reason:
                llm_result.reason = (
                    "智能体按场景完成了主要流程。"
                    if llm_result.passed
                    else "智能体未满足全部通过标准。"
                )
            return llm_result
        except LlmJudgeUnavailable as exc:
            return self._unavailable_judge(str(exc), judge_model)
        except Exception as exc:
            return self._unavailable_judge(f"LLM 构建/调用异常：{exc}", judge_model)

    @staticmethod
    def _unavailable_judge(reason: str, model: str = "") -> JudgeResult:
        return JudgeResult(
            score=0.0,
            passed=False,
            reason="LLM Judge unavailable; evaluation is not evaluable.",
            issues=[reason],
            judge_mode="error",
            model=model,
            status="unavailable",
            fallback_reason=reason,
        )

    def _rule_judge(
        self,
        scenario: Scenario,
        assertion_result: AssertionResult,
        recorder: ExecutionRecorder,
    ) -> JudgeResult:
        issues: list[str] = []
        suggestions: list[str] = []
        score = assertion_result.score

        # "结果数据/句柄/CRS" 契约扣分只对内部 skill 评测有意义（外部 agent 不会用平台内部句柄措辞），
        # agent_test 场景跳过，避免误扣。两类都保留 should_not 违禁词扣分。
        use_skill_contracts = scenario.type == "agent_skill_test"
        judge_mode = "rule-skill" if use_skill_contracts else "rule-agent"

        final_response = recorder.final_output.get("final_response", "")
        for item in scenario.expected_behavior.should_not:
            if item and item in final_response:
                issues.append(f"Final response violated expected behavior: {item}")
                score = max(0.0, score - 0.2)

        if not use_skill_contracts and scenario.judge.penalize_no_ask_back:
            # 外部 agent 反问维度（规则镜像，与 executor 分类同源 classify_external_reply==ask）：
            # 全程无任何反问 → 连续扣分。用 classify 而非 looks_like_question 单独判定，
            # 避免"完成回复含'数据集/格式'关键词"被误判为反问（complete 优先语义与 executor 一致）。
            asked_back = any(
                classify_external_reply(str(it.get("response", ""))) == "ask"
                for it in recorder.external_interactions
            )
            if not asked_back:
                issues.append("External agent did not ask clarifying questions for missing required info before executing.")
                suggestions.append("Configure the external agent to ask back when required info is missing.")
                score = max(0.0, score - 0.2)

        if use_skill_contracts:
            if "结果数据" not in final_response and "句柄" not in final_response:
                issues.append("Final response does not clearly mention the result dataset handle.")
                suggestions.append("Strengthen the skill output contract for result dataset handles.")
                score = max(0.0, score - 0.1)

            if "CRS" not in final_response and "crs" not in final_response:
                suggestions.append("Include CRS explicitly in the final response.")
                score = max(0.0, score - 0.05)

        score = round(max(0.0, min(1.0, score)), 2)
        passed = score >= scenario.pass_criteria.judge_score_min and assertion_result.status != "failed"
        reason = "智能体按场景完成了主要流程。" if passed else "智能体未满足全部通过标准。"
        if not issues and not passed:
            issues.append("Assertion coverage or final response quality did not reach the pass threshold.")
        return JudgeResult(
            score=score,
            passed=passed,
            reason=reason,
            issues=issues,
            suggestions=suggestions,
            judge_mode=judge_mode,
            status="passed" if passed else "failed",
        )
