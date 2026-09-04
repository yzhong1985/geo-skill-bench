import { useEffect, useMemo, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || ""; // 空→相对路径（生产 nginx 反代 /api）；独立后端时传 VITE_API_BASE

async function getJson(path) {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) throw new Error(`请求失败: ${response.status}`);
  return response.json();
}

async function postJson(path, body) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = text;
  }
  if (!response.ok) {
    const detail = typeof data === "object" && data.detail ? data.detail : text;
    throw new Error(detail);
  }
  return data;
}

function fieldDefault(field) {
  if (field.type === "switch") return Boolean(field.default);
  if (field.type === "number") return field.default ?? "";
  return field.default ?? "";
}

// 值等于默认值（含空串/空数字）→ 灰字标记"未自定义"
function isDefaultValue(field, value) {
  if (field.type === "switch") return value === fieldDefault(field);
  if (field.type === "number") return value === "" || value === field.default;
  return value === fieldDefault(field);
}

// 数值输入：本地字符串渲染，保留 "0."、"0.75" 等中间输入不被数字折叠；
// min/max/step 约束原生上下按钮（点一下按 step 增减、不越上下限）；
// 干净的输入按 precision 就地取整（顺带清掉原生上下按钮的浮点残值如 0.9500000000000001）；失焦时夹到上下限后以数字提交
function NumberInput({ value, onChange, min, max, step, precision, className }) {
  const [text, setText] = useState(value === "" || value == null ? "" : String(value));
  // 外部 value 变化（切断言 type / 载入场景）时同步本地显示；同值重渲染不触发，不打断正在输入的内容
  useEffect(() => {
    setText(value === "" || value == null ? "" : String(value));
  }, [value]);

  const round = (num) => {
    if (precision != null && precision >= 0) {
      const factor = 10 ** precision;
      num = Math.round(num * factor) / factor;
    }
    return num;
  };
  // 失焦提交：空值保留为空（沿用默认/后端兜底），否则夹到上下限并按精度取整
  const commit = () => {
    if (text === "") return onChange("");
    const num = Number(text);
    if (Number.isNaN(num)) return;
    let valueNum = round(num);
    if (min != null && valueNum < min) valueNum = min;
    if (max != null && valueNum > max) valueNum = max;
    onChange(valueNum);
  };

  return (
    <input
      type="number"
      className={className}
      value={text}
      min={min}
      max={max}
      step={step}
      onChange={(e) => {
        const raw = e.target.value;
        setText(raw);
        // 输入中间态（以 . / - / e 结尾）不动，等下一字符；其余就地取整
        if (raw !== "" && !/[.\-eE]$/.test(raw) && precision != null && precision >= 0) {
          const num = Number(raw);
          if (!Number.isNaN(num)) setText(String(round(num)));
        }
      }}
      onBlur={commit}
    />
  );
}

function FieldInput({ field, value, onChange }) {
  const isDefault = isDefaultValue(field, value);
  const className = `form-input${isDefault ? " is-default" : ""}`;

  if (field.type === "select") {
    return (
      <select className={className} value={value} onChange={(event) => onChange(event.target.value)}>
        {field.options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
  }

  if (field.type === "textarea") {
    return (
      <textarea
        className={className}
        rows={field.rows || 3}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }

  if (field.type === "switch") {
    return (
      <label className="switch-label">
        <input type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} />
        <span className="switch-text">{value ? "开" : "关"}</span>
      </label>
    );
  }

  if (field.type === "number") {
    return (
      <NumberInput
        className={className}
        value={value}
        onChange={onChange}
        min={field.min}
        max={field.max}
        step={field.step}
        precision={field.precision}
      />
    );
  }

  return <input type="text" className={className} value={value} onChange={(event) => onChange(event.target.value)} />;
}

function FieldRow({ field, value, onChange }) {
  return (
    <label className="field-row">
      <span className="form-label">
        {field.label}
        {field.required && <span className="required-star"> *</span>}
        {field.help && <span className="field-help">{field.help}</span>}
      </span>
      <FieldInput field={field} value={value} onChange={onChange} />
    </label>
  );
}

function FixtureEditor({ listDef, fixtures, onChange }) {
  const fields = listDef.fields;
  const updateRow = (index, key, value) => {
    onChange(fixtures.map((row, i) => (i === index ? { ...row, [key]: value } : row)));
  };
  const addRow = () => {
    const row = {};
    for (const field of fields) row[field.key] = fieldDefault(field);
    onChange([...fixtures, row]);
  };
  const removeRow = (index) => onChange(fixtures.filter((_, i) => i !== index));

  return (
    <div className="fixture-editor">
      <span className="form-label">{listDef.label}</span>
      <table className="fixture-table">
        <thead>
          <tr>
            {fields.map((field) => (
              <th key={field.key}>
                {field.label}
                {field.required && <span className="required-star"> *</span>}
              </th>
            ))}
            <th />
          </tr>
        </thead>
        <tbody>
          {fixtures.length === 0 ? (
            <tr>
              <td className="muted" colSpan={fields.length + 1}>
                暂无数据集，点击下方"添加"新增
              </td>
            </tr>
          ) : (
            fixtures.map((row, index) => (
              <tr key={index}>
                {fields.map((field) => (
                  <td key={field.key}>
                    <FieldInput
                      field={field}
                      value={row[field.key]}
                      onChange={(value) => updateRow(index, field.key, value)}
                    />
                  </td>
                ))}
                <td className="fixture-remove">
                  <button className="btn-danger" onClick={() => removeRow(index)} title="删除该行">
                    删除
                  </button>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
      <button className="btn-outline" onClick={addRow}>
        + 添加 {listDef.row_label || "数据"}
      </button>
    </div>
  );
}

// 默认开关：开 = 不自定义（不提交对应字段），关 = 展示下方自定义编辑器
function DefaultSwitch({ switchDef, checked, onChange }) {
  return (
    <label className="field-row">
      <span className="form-label">
        {switchDef.label}
        {switchDef.help && <span className="field-help">{switchDef.help}</span>}
      </span>
      <label className="switch-label">
        <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
        <span className="switch-text">{checked ? "开" : "关"}</span>
      </label>
    </label>
  );
}

// 断言编辑器：每条先选 type（下拉），再按该 type 需要的字段填参数
// fixtureOptions：可选，供 source:"reference" 字段（结果断言 reference）渲染成下拉；
//   选项={value,label}（label 显示数据集名称，value 存 id），也兼容旧版纯 id 数组
function AssertionEditor({ listDef, mode, assertions, onChange, fixtureOptions = [] }) {
  const types = (listDef.types || []).filter((t) => t.modes.includes(mode));
  const findType = (value) => types.find((t) => t.value === value) || types[0];
  // 参考数据集选项统一成 {value,label}
  const refOptions = fixtureOptions.map((opt) =>
    typeof opt === "string" ? { value: opt, label: opt } : opt
  );
  const blankRow = (typeValue) => {
    const typeDef = findType(typeValue);
    if (!typeDef) return {};
    const row = { type: typeDef.value };
    for (const field of typeDef.fields) row[field.key] = fieldDefault(field);
    return row;
  };
  const updateField = (index, key, value) =>
    onChange(assertions.map((row, i) => (i === index ? { ...row, [key]: value } : row)));
  // 切换 type 时重建该行参数（不同 type 字段不同，避免残留）
  const changeType = (index, typeValue) =>
    onChange(assertions.map((row, i) => (i === index ? blankRow(typeValue) : row)));
  const addRow = () => onChange([...assertions, blankRow(types[0]?.value)]);
  const removeRow = (index) => onChange(assertions.filter((_, i) => i !== index));

  return (
    <div className="fixture-editor assertion-editor">
      <span className="form-label">{listDef.label}</span>
      {assertions.length === 0 ? (
        <p className="muted">暂无断言，点击下方"添加"新增</p>
      ) : (
        <div className="assertion-list">
          {assertions.map((row, index) => {
            const typeDef = findType(row.type);
            return (
              <div key={index} className="assertion-row">
                <div className="assertion-head">
                  <select className="form-input" value={row.type || ""} onChange={(e) => changeType(index, e.target.value)}>
                    {types.map((t) => (
                      <option key={t.value} value={t.value}>
                        {t.label}
                      </option>
                    ))}
                  </select>
                  <button className="btn-danger" onClick={() => removeRow(index)} title="删除该断言">
                    删除
                  </button>
                </div>
                {typeDef?.fields?.length ? (
                  <div className="assertion-fields">
                    {typeDef.fields.map((field) => {
                      const isDefault = isDefaultValue(field, row[field.key]);
                      const className = `form-input${isDefault ? " is-default" : ""}`;
                      return (
                        <label className="field-row" key={field.key}>
                          <span className="form-label">{field.label}</span>
                          {field.type === "number" ? (
                            <NumberInput
                              className={className}
                              value={row[field.key] ?? ""}
                              onChange={(value) => updateField(index, field.key, value)}
                              min={field.min}
                              max={field.max}
                              step={field.step}
                              precision={field.precision}
                            />
                          ) : field.source === "reference" ? (
                            // 参考数据集：从 data.reference 里选（显示名称，存 id），避免手打
                            <select
                              className={className}
                              value={row[field.key] ?? ""}
                              onChange={(e) => updateField(index, field.key, e.target.value)}
                            >
                              <option value="">选择数据集…</option>
                              {/* 旧场景已存的 id 不在当前选项里时回显原值，避免显示成空 */}
                              {row[field.key] && !refOptions.some((o) => o.value === row[field.key]) && (
                                <option value={row[field.key]}>{row[field.key]}</option>
                              )}
                              {refOptions.map((opt) => (
                                <option key={opt.value} value={opt.value}>
                                  {opt.label}
                                </option>
                              ))}
                            </select>
                          ) : field.type === "select" ? (
                            <select
                              className={className}
                              value={row[field.key] ?? ""}
                              onChange={(e) => updateField(index, field.key, e.target.value)}
                            >
                              {field.options.map((opt) => (
                                <option key={opt.value} value={opt.value}>
                                  {opt.label}
                                </option>
                              ))}
                            </select>
                          ) : (
                            <input
                              type="text"
                              className={className}
                              value={row[field.key] ?? ""}
                              placeholder={field.type === "list_text" ? "多个值用逗号分隔" : ""}
                              onChange={(e) => updateField(index, field.key, e.target.value)}
                            />
                          )}
                        </label>
                      );
                    })}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
      <button className="btn-outline" onClick={addRow}>
        + 添加 {listDef.row_label || "断言"}
      </button>
    </div>
  );
}

// 文本列表编辑器：rubric 评分维度逐条添加（无 type 选择，一行一条文本）
function TextListEditor({ listDef, items, onChange }) {
  const updateRow = (index, value) => onChange(items.map((item, i) => (i === index ? value : item)));
  const addRow = () => onChange([...items, ""]);
  const removeRow = (index) => onChange(items.filter((_, i) => i !== index));

  return (
    <div className="fixture-editor">
      <span className="form-label">{listDef.label}</span>
      {items.length === 0 ? (
        <p className="muted">暂无评分维度，点击下方"添加"新增</p>
      ) : (
        <div className="assertion-list">
          {items.map((item, index) => (
            <div key={index} className="assertion-row">
              <div className="assertion-fields">
                <input
                  type="text"
                  className="form-input"
                  value={item}
                  placeholder="评分维度，如：是否正确调用 create_buffer"
                  onChange={(e) => updateRow(index, e.target.value)}
                />
              </div>
              <div className="assertion-head">
                <button className="btn-danger" onClick={() => removeRow(index)} title="删除该评分维度">
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      <button className="btn-outline" onClick={addRow}>
        + 添加 {listDef.row_label || "评分维度"}
      </button>
    </div>
  );
}

function fixtureRowHasSource(row) {
  if (!row.id?.trim()) return false;
  return Boolean(
    row.evaluation_id?.trim() ||
    row.catalog_id?.trim() ||
    row.table?.trim() ||
    row.path?.trim()
  );
}

// fixture 行 → yml：ID + evaluation_id / catalog_id / table / path 之一
function cleanFixtureRows(rows) {
  return rows
    .filter((row) => fixtureRowHasSource(row))
    .map((row) => {
      const clean = {};
      for (const [k, v] of Object.entries(row)) {
        if (v !== undefined && v !== null && v !== "") clean[k] = v;
      }
      return clean;
    });
}

// server 行 → yml 提交对象：id 必填；transport 默认 mock；url 仅非 mock 时必需；
// 空值字段剔除（name 等留空时后端用 id 兜底）
function cleanMcpServersRows(rows) {
  return rows
    .filter((row) => row.id?.trim())
    .map((row) => {
      const clean = {};
      for (const [k, v] of Object.entries(row)) {
        if (v !== undefined && v !== null && v !== "") clean[k] = v;
      }
      if (!clean.transport) clean.transport = "mock";
      return clean;
    });
}

// 断言行 → yml 提交对象：type 必填；空参数跳过；values/sequence 逗号分隔转数组；
// 纯数字字符串 value 转数值（避免 "500" ≠ 500 导致断言不匹配）
function cleanAssertionRows(rows) {
  return rows
    .filter((row) => row && row.type)
    .map((row) => {
      const clean = {};
      for (const [k, v] of Object.entries(row)) {
        if (k === "type" || v === undefined || v === null || v === "") continue;
        if (k === "values" || k === "sequence") {
          clean[k] = Array.isArray(v) ? v : String(v).split(/[,，]/).map((s) => s.trim()).filter(Boolean);
        } else if (k === "value" && typeof v === "string" && v.trim() !== "" && !Number.isNaN(Number(v))) {
          clean[k] = Number(v);
        } else {
          clean[k] = v;
        }
      }
      return { type: row.type, ...clean };
    })
    .filter((row) => Object.keys(row).length > 1); // 至少 type + 一个参数
}

export default function ScenarioForm({ onClose, onSaved }) {
  const [schema, setSchema] = useState([]);
  const [schemaError, setSchemaError] = useState("");
  const [type, setType] = useState("agent_skill_test");
  const [values, setValues] = useState({});
  const [fixtures, setFixtures] = useState([]);
  const [referenceFixtures, setReferenceFixtures] = useState([]);
  const [mcpServers, setMcpServers] = useState([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [overwritePending, setOverwritePending] = useState(null);
  // 断言（过程/结果两个独立开关 + 编辑器）/ judge 评分标准
  const [useDefaultProcessAssertions, setUseDefaultProcessAssertions] = useState(true);
  const [processAssertions, setProcessAssertions] = useState([]);
  const [useResultAssertions, setUseResultAssertions] = useState(false);
  const [resultAssertions, setResultAssertions] = useState([]);
  const [useDefaultRubric, setUseDefaultRubric] = useState(true);
  const [judgeRubric, setJudgeRubric] = useState([]);

  useEffect(() => {
    getJson("/api/scenarios/schema")
      .then((data) => setSchema(data))
      .catch((err) => setSchemaError(err.message));
  }, []);

  // 对指定模式的字段补默认值（不覆盖用户已填的）
  function ensureDefaults(mode) {
    setValues((prev) => {
      const next = { ...prev };
      for (const group of schema) {
        if (!group.modes.includes(mode)) continue;
        for (const field of group.fields || []) {
          if (!(field.key in next)) next[field.key] = fieldDefault(field);
        }
      }
      // 执行器跟随模式：skill 模式→skill，agent 模式→orchestrator（模式的核心配置，切模式时同步）
      next["runtime.executor"] = mode === "agent_test" ? "orchestrator" : "skill";
      return next;
    });
  }

  // schema 加载完成后初始化默认值
  useEffect(() => {
    if (schema.length > 0) ensureDefaults(type);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schema]);

  // 提交时可见字段的必填预检
  const missingRequired = useMemo(() => {
    const missing = [];
    for (const group of schema) {
      if (!group.modes.includes(type)) continue;
      for (const field of group.fields || []) {
        if (!field.required) continue;
        const value = values[field.key];
        const empty = value === undefined || value === null || value === "";
        if (empty) missing.push(field.label);
      }
    }
    if (type === "agent_skill_test" || type === "agent_test") {
      const invalidRow = (rows, label) => {
        if (rows.length === 0) return false;
        const bad = rows.some((row) => row.id?.trim() && !fixtureRowHasSource(row));
        if (bad) missing.push(`${label}（每行 ID 必填，且需 evaluation_id / catalog_id / 表名 / 路径之一）`);
        return bad;
      };
      if (type === "agent_skill_test") invalidRow(fixtures, "输入数据集");
      invalidRow(referenceFixtures, "参考数据集");
    }
    return missing;
  }, [schema, type, values, fixtures, referenceFixtures]);

  function buildPayload() {
    const payload = {};
    const blocks = {};
    for (const [key, value] of Object.entries(values)) {
      if (value === undefined || value === null) continue;
      // 只提交当前模式（agent_test / agent_skill_test）下可见的字段，
      // 避免把另一个模式的残留字段（如 skill 段的 skill.load_mode）拼进残缺对象 → 后端校验失败
      if (!visibleFieldKeys.has(key)) continue;
      const field = allFields[key];
      let out = value;
      if (field?.type === "number") {
        if (value === "") out = fieldDefault(field);
      } else if ((field?.type === "text" || field?.type === "textarea") && value === "") {
        continue; // 空文本不提交，后端用模型默认值兜底
      }
      if (key.includes(".")) {
        const [block, fieldKey] = key.split(".");
        blocks[block] = blocks[block] || {};
        blocks[block][fieldKey] = out;
      } else {
        payload[key] = out;
      }
    }
    const dataBlock = {};
    if (type === "agent_skill_test") {
      const inputRows = cleanFixtureRows(fixtures);
      if (inputRows.length > 0) dataBlock.fixtures = inputRows;
    }
    const referenceRows = cleanFixtureRows(referenceFixtures);
    if (referenceRows.length > 0) dataBlock.reference = referenceRows;
    if (Object.keys(dataBlock).length > 0) blocks.data = dataBlock;
    // MCP servers：skill 模式下从编辑器行组装（工具授权层已取消，不生成 mcp.tools）
    if (type === "agent_skill_test") {
      const serverRows = cleanMcpServersRows(mcpServers);
      if (serverRows.length > 0) blocks.mcp = { servers: serverRows };
    }
    // 断言：过程断言（默认过程开关关闭时）+ 结果断言（结果开关开启时）合并写入 yml（组合生效）
    const mergedAssertions = [];
    if (!useDefaultProcessAssertions) mergedAssertions.push(...processAssertions);
    if (useResultAssertions) mergedAssertions.push(...resultAssertions);
    const rows = cleanAssertionRows(mergedAssertions);
    if (rows.length > 0) blocks.assertions = rows;
    // 自定义评分标准（默认开关关闭时提交）：空行过滤后写 judge.rubric
    if (!useDefaultRubric) {
      const rubric = judgeRubric.map((s) => (s || "").trim()).filter(Boolean);
      if (rubric.length > 0) {
        blocks.judge = blocks.judge || {};
        blocks.judge.rubric = rubric;
      }
    }
    return { ...payload, ...blocks };
  }

  const allFields = useMemo(() => {
    const map = {};
    for (const group of schema) {
      for (const field of group.fields || []) map[field.key] = field;
    }
    return map;
  }, [schema]);

  // 字段 → 可见的模式列表（决定 buildPayload 只提交当前 type 的字段）
  const fieldModes = useMemo(() => {
    const map = {};
    for (const group of schema) {
      for (const field of group.fields || []) map[field.key] = group.modes;
    }
    return map;
  }, [schema]);

  // 当前 type 模式下可见的字段 key 集合
  const visibleFieldKeys = useMemo(() => {
    const set = new Set();
    for (const group of schema) {
      if (!group.modes.includes(type)) continue;
      for (const field of group.fields || []) set.add(field.key);
    }
    return set;
  }, [schema, type]);

  async function handleSubmit(overwrite) {
    setError("");
    if (missingRequired.length > 0) {
      setError(`必填项未填写：${missingRequired.join("、")}`);
      return;
    }
    setSaving(true);
    try {
      const payload = buildPayload();
      const result = await postJson("/api/scenarios", { scenario: payload, overwrite: Boolean(overwrite) });
      onSaved(result);
      onClose();
    } catch (err) {
      if (String(err.message).includes("已存在")) {
        setOverwritePending(buildPayload().id || "");
      }
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  if (schemaError) {
    return <pre className="error-box">加载表单定义失败：{schemaError}</pre>;
  }

  const visibleGroups = schema.filter((group) => group.modes.includes(type));

  return (
    <div className="scenario-form">
      <div className="form-head">
        <h3>新建 Scenario</h3>
        <button className="btn-outline" onClick={onClose} disabled={saving}>
          关闭
        </button>
      </div>

      {visibleGroups.map((group) => (
        <section className="form-group" key={group.key}>
          <h4>{group.label}</h4>
          {group.fields?.map((field) => (
            <FieldRow
              key={field.key}
              field={field}
              value={field.key in values ? values[field.key] : fieldDefault(field)}
              onChange={(value) => {
                if (field.key === "type") {
                  setValues((prev) => ({ ...prev, [field.key]: value }));
                  setType(value);
                  ensureDefaults(value);
                  // 断言/rubric 强依赖评测模式，切模式时清空，避免残留模式专属的 type
                  setProcessAssertions([]);
                  setResultAssertions([]);
                  setJudgeRubric([]);
                } else {
                  setValues((prev) => ({ ...prev, [field.key]: value }));
                }
              }}
            />
          ))}
          {group.key === "data" && (
            <>
              {group.list && (!group.list.modes || group.list.modes.includes(type)) && (
                <FixtureEditor listDef={group.list} fixtures={fixtures} onChange={setFixtures} />
              )}
              {group.reference_list && (
                <FixtureEditor
                  listDef={group.reference_list}
                  fixtures={referenceFixtures}
                  onChange={setReferenceFixtures}
                />
              )}
            </>
          )}
          {group.key === "mcp" && group.list && (
            <FixtureEditor listDef={group.list} fixtures={mcpServers} onChange={setMcpServers} />
          )}
          {group.key === "assertions" && (
            <>
              <DefaultSwitch
                switchDef={group.default_switch}
                checked={useDefaultProcessAssertions}
                onChange={setUseDefaultProcessAssertions}
              />
              {!useDefaultProcessAssertions && (
                <AssertionEditor
                  listDef={group.list}
                  mode={type}
                  assertions={processAssertions}
                  onChange={setProcessAssertions}
                />
              )}
              <>
                <DefaultSwitch
                  switchDef={group.result_switch}
                  checked={useResultAssertions}
                  onChange={setUseResultAssertions}
                />
                {useResultAssertions && (
                  <AssertionEditor
                    listDef={group.result_list}
                    mode={type}
                    assertions={resultAssertions}
                    onChange={setResultAssertions}
                    fixtureOptions={referenceFixtures
                      .filter((f) => f.id?.trim())
                      .map((f) => ({ value: f.id.trim(), label: f.name?.trim() || f.id.trim() }))}
                  />
                )}
              </>
            </>
          )}
          {group.key === "judge" && (
            <>
              <DefaultSwitch
                switchDef={group.default_switch}
                checked={useDefaultRubric}
                onChange={setUseDefaultRubric}
              />
              {!useDefaultRubric && (
                <TextListEditor listDef={group.list} items={judgeRubric} onChange={setJudgeRubric} />
              )}
            </>
          )}
        </section>
      ))}

      {overwritePending && (
        <div className="overwrite-bar">
          <span>场景 <code>{overwritePending}</code> 已存在。</span>
          <button className="btn-danger" disabled={saving} onClick={() => handleSubmit(true)}>
            覆盖保存
          </button>
          <button className="btn-outline" disabled={saving} onClick={() => setOverwritePending(null)}>
            取消
          </button>
        </div>
      )}

      {error ? <pre className="error-box">{error}</pre> : null}

      <div className="form-actions">
        <button className="primary" onClick={() => handleSubmit(false)} disabled={saving}>
          {saving ? "保存中…" : "保存场景"}
        </button>
      </div>
    </div>
  );
}
