import { useState } from "react";

const ATTRIBUTION_LABELS = {
  skill_prompt_issue: "Skill 约束不足",
  agent_drift: "Agent 随机漂移",
  env_error: "环境 / 工具失败",
  assertion_or_scenario: "断言或场景",
  harness_variance: "平台自身抖动",
  unknown: "未知",
};

function attributionEntries(breakdown) {
  return Object.entries(breakdown || {}).sort((a, b) => b[1] - a[1]);
}

export default function BatchAnalystView({ batchId, diagnostics, loading, error, onAnalyze }) {
  const [tab, setTab] = useState("cause");
  const [copied, setCopied] = useState(false);
  const patch = diagnostics?.suggested_patch;
  const unavailable = diagnostics?.source === "unavailable";

  async function copyPatch() {
    if (!patch?.diff_content) return;
    try {
      await navigator.clipboard.writeText(patch.diff_content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch (err) {
      console.error(err);
    }
  }

  return (
    <div className="analyst-view">
      <div className="panel-head">
        <h3>批次 AI 诊断</h3>
        <button className="primary" onClick={onAnalyze} disabled={!batchId || loading}>
          {loading ? "分析中…" : diagnostics ? "重新分析" : "开始分析"}
        </button>
      </div>
      <p className="muted">辅助分析，不改变正式通过/失败。补丁只可复制，不会写回 Skill 文件。</p>
      {error ? <pre className="error-box">{error}</pre> : null}
      {!diagnostics && !loading ? <p className="muted">尚未分析。批次结束后可手动触发。</p> : null}
      {diagnostics ? (
        <>
          {unavailable ? (
            <p className="muted">诊断引擎不可用，仅保留统计摘要。{diagnostics.error ? `原因：${diagnostics.error}` : ""}</p>
          ) : null}
          <p className="analyst-summary">{diagnostics.summary_text}</p>
          <div className="attribution-list">
            {attributionEntries(diagnostics.attribution_breakdown).map(([key, value]) => (
              <div key={key} className="attribution-row">
                <span>{ATTRIBUTION_LABELS[key] || key}</span>
                <div className="attribution-bar">
                  <div className="attribution-fill" style={{ width: `${Math.round(value * 100)}%` }} />
                </div>
                <strong>{Math.round(value * 100)}%</strong>
              </div>
            ))}
          </div>
          {patch ? (
            <div className="patch-card">
              <div className="panel-head">
                <h4>Skill 建议补丁</h4>
                <button onClick={copyPatch}>{copied ? "已复制" : "复制 Patch"}</button>
              </div>
              <p className="muted">{patch.target_file}</p>
              <p>{patch.explanation}</p>
              <pre className="diff-block">{patch.diff_content}</pre>
            </div>
          ) : (
            <p className="muted">本批次未生成 Skill 补丁（主因不是 Skill，或无法定位唯一 Skill 文件）。</p>
          )}
          <div className="analyst-tabs">
            <button className={tab === "cause" ? "primary" : ""} onClick={() => setTab("cause")}>
              根因
            </button>
            <button className={tab === "meta" ? "primary" : ""} onClick={() => setTab("meta")}>
              元数据
            </button>
          </div>
          {tab === "cause" ? (
            <pre className="result-text">{diagnostics.root_cause_analysis || "(无根因文本)"}</pre>
          ) : (
            <pre className="result-text">
              {JSON.stringify(
                {
                  source: diagnostics.source,
                  model: diagnostics.model,
                  created_at: diagnostics.created_at,
                  error: diagnostics.error,
                },
                null,
                2,
              )}
            </pre>
          )}
        </>
      ) : null}
    </div>
  );
}
