import { useEffect, useRef, useState } from "react";
import ScenarioForm from "./ScenarioForm";
import ScenarioManager from "./ScenarioManager";
import BatchAnalystView from "./BatchAnalystView";

// VITE_API_BASE 为空 → 相对路径，生产走 nginx 同源反代 /api；本地 dev 由 vite server.proxy 转发到后端
const API_BASE = import.meta.env.VITE_API_BASE || ""; // 要指定独立后端时传 VITE_API_BASE=http://host:8000

async function fetchJson(path, options) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    const error = new Error(text || `Request failed: ${response.status}`);
    error.status = response.status;
    error.body = text;
    throw error;
  }
  return response.json();
}

const SCENARIOS_PER_PAGE = 7;
const HISTORY_PER_PAGE = 10;

function Pager({ page, totalPages, onPageChange }) {
  if (totalPages <= 1) return null;
  return (
    <div className="pager">
      <button className="pager-btn" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
        ‹ 上一页
      </button>
      <span className="pager-info">
        第 {page} / {totalPages} 页
      </span>
      <button className="pager-btn" disabled={page >= totalPages} onClick={() => onPageChange(page + 1)}>
        下一页 ›
      </button>
    </div>
  );
}

function ResultDetail({ result }) {
  const fr = result.final_output?.final_response || "";
  const toolCalls = result.tool_calls || [];
  const conversation = result.conversation || [];
  const assertions = result.assertions || [];
  const errors = result.errors || [];
  const externalInteractions = result.final_output?.external_interactions || [];
  const judge = result.judge || {};
  const hasJudge = typeof judge === "object" && Object.keys(judge).length > 0;
  const scorePct = judge.score != null ? Math.round(judge.score * 100) : null;
  // judge_mode 为迭代 2（LLM judge）新增字段；老数据没有时按 reason 反推状态标签
  const modeLabel = hasJudge
    ? {
        llm: "LLM 判定",
        "rule-skill": "规则判定·skill契约",
        "rule-agent": "规则判定·宽松",
        disabled: "已禁用",
        error: "判定错误",
      }[judge.judge_mode] ||
      (typeof judge.reason === "string" && judge.reason.includes("Judge disabled")
        ? "已禁用"
        : "规则判定")
    : "";

  const jsonBlock = (obj) =>
    obj && typeof obj === "object" ? JSON.stringify(obj, null, 2) : String(obj ?? "");

  return (
    <div className="result-detail">
      <div className="result-section">
        <h4>工具调用</h4>
        {toolCalls.length === 0 ? (
          <p className="muted">(无工具调用)</p>
        ) : (
          <ul className="list compact">
            {toolCalls.map((call, index) => (
              <li key={index}>
                <details className="tool-item">
                  <summary>
                    <span className="tool-arrow">▸</span>
                    <strong>{call.tool_name}</strong>
                    <span className={call.status === "success" ? "pill ok" : "pill bad"}>{call.status}</span>
                  </summary>
                  <div className="json-label">入参</div>
                  <pre>{jsonBlock(call.arguments)}</pre>
                  {call.result ? (
                    <>
                      <div className="json-label">出参</div>
                      <pre>{jsonBlock(call.result)}</pre>
                    </>
                  ) : null}
                </details>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="result-section">
        <h4>最终回答</h4>
        <pre className="result-text">{fr || "(空)"}</pre>
      </div>
      <div className="result-section">
        <h4>判定结果</h4>
        {!hasJudge ? (
          <p className="muted">(无 judge 结果)</p>
        ) : (
          <>
            <div className="judge-header">
              <span className={judge.passed ? "pill ok" : "pill bad"}>
                {judge.passed ? "passed" : "failed"}
              </span>
              {scorePct != null && <span className="judge-score">{scorePct}%</span>}
              <span className="pill mode">{modeLabel}</span>
              {judge.model ? <span className="muted">模型: {judge.model}</span> : null}
            </div>
            <div className="json-label">原因</div>
            <pre className="result-text">{judge.reason || ""}</pre>
            {judge.issues?.length ? (
              <>
                <div className="json-label">问题</div>
                <ul className="list compact">
                  {judge.issues.map((item, index) => (
                    <li key={index} className="assertion bad">
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
            {judge.suggestions?.length ? (
              <>
                <div className="json-label">建议</div>
                <ul className="list compact">
                  {judge.suggestions.map((item, index) => (
                    <li key={index}>
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </>
        )}
      </div>
      <div className="result-section">
        <h4>完整对话</h4>
        {conversation.length === 0 ? (
          <p className="muted">(无对话记录)</p>
        ) : (
          <ul className="list compact">
            {conversation.map((message, index) => (
              <li key={index}>
                <strong className={message.role === "assistant" ? "role-assistant" : "role-user"}>
                  {message.role}
                </strong>
                <pre className="msg-content">{jsonBlock(message.content)}</pre>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="result-section">
        <h4>外部智能体交互</h4>
        {externalInteractions.length === 0 ? (
          <p className="muted">(无外部交互记录)</p>
        ) : (
          <ul className="list compact">
            {externalInteractions.map((interaction, index) => (
              <li key={index}>
                <strong className="role-user">指令 {interaction.turn}</strong>
                <pre className="msg-content">{interaction.instruction || ""}</pre>
                <strong className="role-assistant">外部回答</strong>
                <pre className="msg-content">{interaction.response || ""}</pre>
                {interaction.tool_calls?.length ? (
                  <p className="muted">
                    外部工具调用: {interaction.tool_calls.map((call) => call.tool_name).join(", ")}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="result-section">
        <h4>断言</h4>
        {assertions.length === 0 ? (
          <p className="muted">(无断言)</p>
        ) : (
          <ul className="list compact">
            {assertions.map((item, index) => (
              <li key={index} className={item.passed ? "assertion ok" : "assertion bad"}>
                <span className="pill">{item.passed ? "passed" : "failed"}</span>
                <span>
                  {item.type}{item.backend ? ` [${item.backend}]` : ""}: {item.message}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="result-section">
        <h4>错误</h4>
        {errors.length === 0 ? (
          <p className="muted">(无错误)</p>
        ) : (
          errors.map((error, index) => <pre key={index} className="error-box">{error}</pre>)
        )}
      </div>
      <details className="result-section">
        <summary>原始 JSON</summary>
        <pre>{JSON.stringify(result, null, 2)}</pre>
      </details>
    </div>
  );
}

export default function App() {
  const [scenarios, setScenarios] = useState([]);
  const [skills, setSkills] = useState([]);
  const [reports, setReports] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [runHistory, setRunHistory] = useState([]); // 来自 DB 的持久化历史（/api/runs）
  const [historyDbError, setHistoryDbError] = useState("");
  const [batches, setBatches] = useState([]);
  const [selectedBatchId, setSelectedBatchId] = useState("");
  const [batchDetail, setBatchDetail] = useState(null);
  const [diagnostics, setDiagnostics] = useState(null);
  const [analystLoading, setAnalystLoading] = useState(false);
  const [analystError, setAnalystError] = useState("");
  const [repeatCount, setRepeatCount] = useState(3);
  const [selectedPath, setSelectedPath] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [showManage, setShowManage] = useState(false);
  const [memoryEnabled, setMemoryEnabled] = useState(false);
  const [validation, setValidation] = useState(null);
  const [tools, setTools] = useState([]);
  const [currentTask, setCurrentTask] = useState(null);
  const [currentBatch, setCurrentBatch] = useState(null);
  const [batchStageResults, setBatchStageResults] = useState({});
  const [runResult, setRunResult] = useState(null);
  const [scenarioPage, setScenarioPage] = useState(1);
  const [historyPage, setHistoryPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const eventSourceRef = useRef(null);

  useEffect(() => {
    loadInitial();
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  async function loadInitial() {
    try {
      const [scenarioData, skillData, reportData, taskData] = await Promise.all([
        fetchJson("/api/scenarios"),
        fetchJson("/api/skills"),
        fetchJson("/api/reports"),
        fetchJson("/api/tasks"),
      ]);
      setScenarios(scenarioData);
      setSkills(skillData);
      setReports(reportData);
      setTasks(taskData);
      if (scenarioData.length > 0) {
        setSelectedPath(scenarioData[0].path);
      }
      await loadRunHistory();
      await loadBatches();
    } catch (err) {
      setError(err.message);
    }
  }

  async function loadBatches() {
    try {
      const data = await fetchJson("/api/batches");
      setBatches(data.batches || []);
    } catch (err) {
      setError(err.message);
    }
  }

  function normalizeBatchDetail(detail) {
    if (!detail) return detail;
    if (typeof detail.result_json === "string") {
      try {
        const parsed = JSON.parse(detail.result_json);
        return { ...detail, ...parsed };
      } catch {
        return detail;
      }
    }
    return detail;
  }

  function batchSummaryOf(batch) {
    if (!batch) return {};
    const normalized = normalizeBatchDetail(batch);
    if (normalized.summary && typeof normalized.summary === "object") return normalized.summary;
    if (normalized.result?.summary) return normalized.result.summary;
    return normalized;
  }

  async function selectBatch(batchId) {
    setSelectedBatchId(batchId);
    setAnalystError("");
    setDiagnostics(null);
    try {
      const detail = await fetchJson(`/api/batches/${batchId}`);
      setBatchDetail(normalizeBatchDetail(detail));
    } catch (err) {
      setError(err.message);
      setBatchDetail(null);
    }
    try {
      const diag = await fetchJson(`/api/batches/${batchId}/diagnostics`);
      setDiagnostics(diag);
    } catch {
      setDiagnostics(null);
    }
  }

  async function analyzeSelectedBatch() {
    if (!selectedBatchId) return;
    setAnalystLoading(true);
    setAnalystError("");
    try {
      const diag = await fetchJson(`/api/batches/${selectedBatchId}/analyze`, { method: "POST" });
      setDiagnostics(diag);
    } catch (err) {
      let message = err.message;
      try {
        const parsed = JSON.parse(err.body || err.message);
        const detail = parsed.detail;
        if (detail && typeof detail === "object") {
          if (detail.source) setDiagnostics(detail);
          message = detail.error || detail.summary_text || message;
        }
      } catch {
        // 保持原始错误文本
      }
      setAnalystError(message);
    } finally {
      setAnalystLoading(false);
    }
  }

  async function createRepeatBatch() {
    if (!selectedPath) return;
    const count = Number(repeatCount);
    if (!Number.isInteger(count) || count < 2) {
      setError("Repeat 次数至少为 2");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const created = await fetchJson("/api/batches", {
        method: "POST",
        body: JSON.stringify({
          scenarios: [selectedPath],
          repeat_count: count,
          memory_enabled: memoryEnabled,
        }),
      });
      setCurrentTask(null);
      setBatchStageResults({});
      setCurrentBatch(created);
      await loadBatches();
      if (created.batch_id) {
        await selectBatch(created.batch_id);
        subscribeToBatch(created.batch_id);
      }
    } catch (err) {
      setError(err.message);
      setLoading(false);
    }
  }

  async function loadRunHistory() {
    try {
      const data = await fetchJson("/api/runs");
      if (data.available) {
        setRunHistory(data.runs);
        setHistoryDbError("");
      } else {
        setRunHistory([]);
        setHistoryDbError(data.error || "数据库不可用");
      }
    } catch (err) {
      setHistoryDbError(err.message);
    }
  }

  async function loadRunDetail(runId) {
    try {
      const data = await fetchJson(`/api/runs/${runId}`);
      // 后端返回 { run_id, scenario_id, json, md, ... }，json 是报告全文 JSON 字符串
      if (data.json) {
        setRunResult(JSON.parse(data.json));
      }
    } catch (err) {
      setError(err.message);
    }
  }

  async function validateScenario() {
    if (!selectedPath) return;
    setLoading(true);
    setError("");
    try {
      const data = await fetchJson("/api/validate", {
        method: "POST",
        body: JSON.stringify({ path: selectedPath }),
      });
      setValidation(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function listTools() {
    if (!selectedPath) return;
    setLoading(true);
    setError("");
    try {
      const data = await fetchJson("/api/list-tools", {
        method: "POST",
        body: JSON.stringify({ path: selectedPath }),
      });
      setTools(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function runScenario() {
    if (!selectedPath) return;
    setLoading(true);
    setError("");
    setRunResult(null);
    setCurrentBatch(null);
    setBatchStageResults({});
    try {
      const task = await fetchJson("/api/tasks", {
        method: "POST",
        body: JSON.stringify({
          path: selectedPath,
          output_dir: "reports",
          memory_enabled: memoryEnabled,
        }),
      });
      setCurrentTask(task);
      setTasks((previous) => [task, ...previous.filter((item) => item.task_id !== task.task_id)]);
      subscribeToTask(task.task_id);
    } catch (err) {
      setError(err.message);
      setLoading(false);
    }
  }

  function subscribeToTask(taskId) {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }
    const source = new EventSource(`${API_BASE}/api/tasks/${taskId}/events`);
    eventSourceRef.current = source;

    const handleEvent = (event) => {
      const payload = JSON.parse(event.data);
      const task = payload.task;
      if (task) {
        setCurrentTask(task);
        setTasks((previous) => [task, ...previous.filter((item) => item.task_id !== task.task_id)]);
      }
      if (payload.payload?.result) {
        setRunResult(payload.payload.result);
      }
      if (payload.type === "task_finished") {
        if (task?.result) {
          setRunResult(task.result);
        }
        loadReportsOnly();
        loadRunHistory();
        setLoading(false);
        source.close();
      }
      if (payload.type === "error") {
        setError(payload.payload?.message || "Task failed.");
      }
    };

    source.addEventListener("task_created", handleEvent);
    source.addEventListener("task_started", handleEvent);
    source.addEventListener("executor_session", handleEvent);
    source.addEventListener("stage", handleEvent);
    source.addEventListener("executor_step", handleEvent);
    source.addEventListener("agent_result", handleEvent);
    source.addEventListener("assertions", handleEvent);
    source.addEventListener("judge", handleEvent);
    source.addEventListener("error", handleEvent);
    source.addEventListener("result", handleEvent);
    source.addEventListener("task_finished", handleEvent);
    source.onerror = () => {
      source.close();
      setLoading(false);
    };
  }

  function subscribeToBatch(batchId) {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }
    const source = new EventSource(`${API_BASE}/api/batches/${batchId}/events`);
    eventSourceRef.current = source;

    const handleEvent = (event) => {
      const payload = JSON.parse(event.data);
      const batch = payload.batch;
      if (batch) {
        setCurrentBatch(batch);
        setBatches((previous) => [batch, ...previous.filter((item) => item.batch_id !== batch.batch_id)]);
      }
      const inner = payload.payload?.event || payload.payload || {};
      const stageResults = inner.stage_results || inner.event?.stage_results;
      if (stageResults) {
        setBatchStageResults(stageResults);
      }
      if (payload.type === "batch_item_start") {
        setBatchStageResults({});
      }
      if (payload.type === "batch_finished") {
        loadBatches();
        loadRunHistory();
        loadReportsOnly();
        if (batchId) {
          selectBatch(batchId);
        }
        setLoading(false);
        source.close();
      }
    };

    [
      "batch_created",
      "batch_started",
      "batch_item_start",
      "batch_item_event",
      "batch_item_complete",
      "batch_complete",
      "batch_finished",
      "stage",
    ].forEach((name) => source.addEventListener(name, handleEvent));
    source.onerror = () => {
      source.close();
      setLoading(false);
    };
  }

  async function loadReportsOnly() {
    try {
      const reportData = await fetchJson("/api/reports");
      setReports(reportData);
    } catch (err) {
      setError(err.message);
    }
  }

  async function loadScenariosOnly() {
    try {
      const scenarioData = await fetchJson("/api/scenarios");
      setScenarios(scenarioData);
      // 保留当前选中；若所选场景已不存在（如覆盖改名）则回退到第一个
      setSelectedPath((previous) =>
        scenarioData.some((scenario) => scenario.path === previous)
          ? previous
          : scenarioData[0]?.path || "",
      );
    } catch (err) {
      setError(err.message);
    }
  }

  const currentScenario = scenarios.find((scenario) => scenario.path === selectedPath);
  const stageResults = currentBatch ? batchStageResults : currentTask?.stage_results || {};
  const batchIteration = currentBatch?.current_iteration;
  const batchTotal = currentBatch?.total_runs;
  const batchCompleted = currentBatch?.completed_runs ?? 0;
  const progressTitle = currentBatch
    ? batchIteration
      ? `Task Progress · 批次第 ${batchIteration}/${batchTotal || "?"} 轮`
      : `Task Progress · 批次 ${currentBatch.status || "running"}`
    : "Task Progress";
  // 分页：safePage 防止列表变化后当前页越界（slice 用 safePage，Pager 展示 safePage）
  const scenarioTotalPages = Math.max(1, Math.ceil(scenarios.length / SCENARIOS_PER_PAGE));
  const scenarioSafePage = Math.min(scenarioPage, scenarioTotalPages);
  const scenarioPageItems = scenarios.slice(
    (scenarioSafePage - 1) * SCENARIOS_PER_PAGE,
    scenarioSafePage * SCENARIOS_PER_PAGE,
  );
  const historyTotalPages = Math.max(1, Math.ceil(runHistory.length / HISTORY_PER_PAGE));
  const historySafePage = Math.min(historyPage, historyTotalPages);
  const historyPageItems = runHistory.slice(
    (historySafePage - 1) * HISTORY_PER_PAGE,
    historySafePage * HISTORY_PER_PAGE,
  );
  // agent_test（评测外部 agent 模式）不做数据准备/MCP 连接/技能装载，隐藏这三个与 agent 无关的阶段
  const isAgentMode = currentScenario?.type === "agent_test";
  const AGENT_HIDDEN_STAGES = ["PREPARE_DATA", "CONNECT_MCP", "LOAD_SKILL"];
  const visibleStages = Object.entries(stageResults).filter(
    ([stage]) => !(isAgentMode && AGENT_HIDDEN_STAGES.includes(stage)),
  );

  return (
    <div className="app-shell">
      <header className="hero">
        <div>
          <p className="eyebrow">GeoSkillBench</p>
          <h1>GIS Agent Skill Evaluation Console</h1>
          <p className="lede">
            Task-based evaluation UI with a pluggable executor layer and live SSE progress.
          </p>
        </div>
        <div className="hero-card">
          <span>Backend</span>
          <strong>{API_BASE}</strong>
          <span className="status-pill">{currentBatch?.status || currentTask?.status || "idle"}</span>
        </div>
      </header>

      <main className="layout">
        <section className="panel">
          <div className="panel-head">
            <h2>Scenario</h2>
            <div className="panel-head-actions">
              <button
                className="btn-outline"
                onClick={() => {
                  setShowForm(false);
                  setShowManage(true);
                }}
              >
                管理
              </button>
              <button
                className="btn-outline"
                onClick={() => {
                  setShowManage(false);
                  setShowForm(true);
                }}
              >
                新建 Scenario
              </button>
            </div>
          </div>
          {showManage && <ScenarioManager scenarios={scenarios} onClose={() => setShowManage(false)} onSaved={loadScenariosOnly} />}
          {showForm && <ScenarioForm onClose={() => setShowForm(false)} onSaved={loadScenariosOnly} />}
          <label className="field">
            <span>Select scenario</span>
            <select value={selectedPath} onChange={(event) => setSelectedPath(event.target.value)}>
              {scenarios.map((scenario) => (
                <option key={scenario.path} value={scenario.path}>
                  {scenario.name}
                </option>
              ))}
            </select>
          </label>
          <div className="config-grid">
            <label className="field">
              <span>Executor runtime</span>
              <div className="readonly-value">{currentScenario?.executor || "—"}</div>
            </label>
            <label className="checkbox-field">
              <input
                type="checkbox"
                checked={memoryEnabled}
                onChange={(event) => setMemoryEnabled(event.target.checked)}
              />
              <span>Memory enabled</span>
            </label>
          </div>
          <div className="button-row">
            <button onClick={validateScenario} disabled={loading || !selectedPath}>
              Validate
            </button>
            <button onClick={listTools} disabled={loading || !selectedPath}>
              List Tools
            </button>
            <button className="primary" onClick={runScenario} disabled={loading || !selectedPath}>
              Create Task
            </button>
            <label className="repeat-field">
              <span>Repeat</span>
              <input
                type="number"
                min="2"
                max="20"
                value={repeatCount}
                onChange={(event) => setRepeatCount(event.target.value)}
              />
            </label>
            <button onClick={createRepeatBatch} disabled={loading || !selectedPath}>
              创建批次
            </button>
          </div>
          {error ? <pre className="error-box">{error}</pre> : null}
          <div className="meta-grid">
            <div>
              <h3>Available scenarios</h3>
              <ul className="list">
                {scenarioPageItems.map((scenario) => (
                  <li key={scenario.path}>
                    <strong>{scenario.name}</strong>
                    <span>{scenario.path}</span>
                  </li>
                ))}
              </ul>
              <Pager page={scenarioSafePage} totalPages={scenarioTotalPages} onPageChange={setScenarioPage} />
            </div>
            <div>
              <h3>Available skills</h3>
              <ul className="list">
                {skills.map((skill) => (
                  <li key={skill.path}>
                    <strong>{skill.name}</strong>
                    <span>{skill.id}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <section className="panel">
          <h2>{progressTitle}</h2>
          {currentBatch ? (
            <div className="progress-meta">
              <span className="chip">已完成 {batchCompleted}/{batchTotal || "?"}</span>
              {batchIteration ? <span className="chip">正在跑第 {batchIteration} 轮</span> : null}
              <span className="chip">{currentBatch.status || "running"}</span>
              {currentBatch.current_scenario ? <span className="chip">{currentBatch.current_scenario}</span> : null}
            </div>
          ) : null}
          <div className="result-grid">
            <article>
              <h3>Stage state</h3>
              <ul className="list compact">
                {visibleStages.length ? (
                  visibleStages.map(([stage, status]) => (
                    <li key={stage} className={`stage-${String(status).toLowerCase()}`}>
                      <strong>
                        {stage}
                        <span className="stage-dot" />
                      </strong>
                      <span>{status}</span>
                    </li>
                  ))
                ) : (
                  <li className="stage-pending">
                    <strong>No stages yet</strong>
                    <span>{currentBatch ? "创建批次后这里会显示当前轮次的阶段。" : "Create a task to stream progress."}</span>
                  </li>
                )}
              </ul>
            </article>
            <article>
              <h3>{currentBatch ? "当前批次" : "当前任务"}</h3>
              {currentBatch ? (
                <dl className="stat-grid">
                  <div className="stat-card">
                    <dt>批次</dt>
                    <dd>{currentBatch.batch_id}</dd>
                  </div>
                  <div className="stat-card">
                    <dt>轮次</dt>
                    <dd>{batchIteration || batchCompleted || 0}<span className="stat-sub"> / {batchTotal || "?"}</span></dd>
                  </div>
                  <div className="stat-card">
                    <dt>状态</dt>
                    <dd>{currentBatch.status || "running"}</dd>
                  </div>
                </dl>
              ) : currentTask ? (
                <dl className="stat-grid">
                  <div className="stat-card">
                    <dt>任务</dt>
                    <dd>{currentTask.task_id?.slice(0, 8) || "—"}</dd>
                  </div>
                  <div className="stat-card">
                    <dt>状态</dt>
                    <dd>{currentTask.status || "idle"}</dd>
                  </div>
                  <div className="stat-card">
                    <dt>阶段</dt>
                    <dd>{currentTask.current_stage || "—"}</dd>
                  </div>
                </dl>
              ) : (
                <p className="muted">No active task.</p>
              )}
            </article>
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>Batches</h2>
            <button className="btn-outline" onClick={loadBatches}>刷新</button>
          </div>
          {batches.length === 0 ? (
            <p className="muted">暂无批次。上方选场景后点「创建批次」，或查看历史批次。</p>
          ) : (
            <ul className="list batch-list">
              {batches.map((batch) => {
                const summary = batchSummaryOf(batch);
                const passRate = summary.pass_rate ?? batch.pass_rate;
                const total = summary.total_runs ?? batch.total_runs;
                return (
                  <li
                    key={batch.batch_id}
                    onClick={() => selectBatch(batch.batch_id)}
                    style={{ cursor: "pointer" }}
                    className={`batch-item ${selectedBatchId === batch.batch_id ? "is-selected" : ""}`}
                  >
                    <strong>{batch.batch_id}</strong>
                    <div className="batch-item-meta">
                      <span className={batch.status === "succeeded" || batch.status === "completed" ? "pill ok" : "pill bad"}>
                        {batch.status}
                      </span>
                      <span>{total ?? "—"} runs</span>
                      {passRate != null ? <span>pass {(Number(passRate) * 100).toFixed(0)}%</span> : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          {batchDetail ? (
            <div className="batch-detail">
              <h3>批次统计</h3>
              {(() => {
                const summary = batchSummaryOf(batchDetail);
                const variance = summary.overall_variance || {};
                const duration = variance.duration_ms || {};
                const tools = variance.tool_usage_breakdown || [];
                const runs = batchDetail.runs || batchDetail.result?.runs || [];
                const passPct = summary.pass_rate != null ? `${(summary.pass_rate * 100).toFixed(1)}%` : "—";
                return (
                  <>
                    <dl className="stat-grid">
                      <div className="stat-card">
                        <dt>通过率</dt>
                        <dd>{passPct}</dd>
                      </div>
                      <div className="stat-card">
                        <dt>轨迹熵</dt>
                        <dd>{variance.trajectory_entropy ?? "—"}</dd>
                      </div>
                      <div className="stat-card">
                        <dt>耗时均值</dt>
                        <dd>{duration.mean ?? "—"}<span className="stat-sub"> ms</span></dd>
                      </div>
                      <div className="stat-card">
                        <dt>耗时标准差</dt>
                        <dd>{duration.std_dev ?? "—"}<span className="stat-sub"> ms</span></dd>
                      </div>
                    </dl>
                    <div>
                      <h4>工具覆盖</h4>
                      {tools.length === 0 ? (
                        <p className="muted">无工具统计</p>
                      ) : (
                        <ul className="list compact">
                          {tools.map((tool) => (
                            <li key={tool.tool_name} className="batch-item">
                              <strong>{tool.tool_name}</strong>
                              <span>覆盖 {(tool.usage_rate * 100).toFixed(0)}% · 均 {tool.mean_calls_per_run}</span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                    {runs.length ? (
                      <details>
                        <summary>子运行（{runs.length}）</summary>
                        <ul className="list compact">
                          {runs.map((run) => (
                            <li key={run.run_id} className="batch-item">
                              <strong>#{run.iteration} {run.run_id}</strong>
                              <span>{run.evaluation_verdict} · {run.duration_ms} ms</span>
                            </li>
                          ))}
                        </ul>
                      </details>
                    ) : null}
                  </>
                );
              })()}
              <BatchAnalystView
                batchId={selectedBatchId}
                diagnostics={diagnostics}
                loading={analystLoading}
                error={analystError}
                onAnalyze={analyzeSelectedBatch}
              />
            </div>
          ) : (
            <p className="muted">点选一个批次后，可查看方差统计并手动触发 AI 诊断。</p>
          )}
        </section>

        <section className="panel">
          <h2>Inspector</h2>
          <div className="result-grid">
            <article>
              <h3>Validation</h3>
              <pre>{validation ? JSON.stringify(validation, null, 2) : "No validation yet."}</pre>
            </article>
            <article>
              <h3>Tools</h3>
              <pre>{tools.length ? JSON.stringify(tools, null, 2) : "No tool listing yet."}</pre>
            </article>
          </div>
        </section>

        <section className="panel">
          <h2>Run Result</h2>
          {runResult ? <ResultDetail result={runResult} /> : "No run completed yet."}
        </section>

        <section className="panel">
          <h2>Task History</h2>
          {historyDbError ? (
            <pre className="error-box">{historyDbError}</pre>
          ) : runHistory.length === 0 ? (
            <p className="muted">No runs recorded yet.</p>
          ) : (
            <>
              <ul className="list">
                {historyPageItems.map((run) => (
                  <li key={run.run_id} onClick={() => loadRunDetail(run.run_id)} style={{ cursor: "pointer" }}>
                    <strong>{run.scenario_name || run.scenario_id}</strong>
                    <span>
                      {run.run_id.slice(0, 8)} · <span className={run.status === "passed" ? "pill ok" : "pill bad"}>{run.status}</span> ·{" "}
                      {run.executor || "—"} · {run.created_at?.slice(0, 19)?.replace("T", " ")}
                    </span>
                    <span className="muted">点击查看该次运行报告</span>
                  </li>
                ))}
              </ul>
              <Pager page={historySafePage} totalPages={historyTotalPages} onPageChange={setHistoryPage} />
            </>
          )}
        </section>

        <section className="panel">
          <h2>Reports</h2>
          <ul className="list">
            {reports.map((report) => (
              <li key={report.scenario_id}>
                <strong>{report.scenario_id}</strong>
                <span>{report.json_path}</span>
              </li>
            ))}
          </ul>
        </section>
      </main>
    </div>
  );
}
