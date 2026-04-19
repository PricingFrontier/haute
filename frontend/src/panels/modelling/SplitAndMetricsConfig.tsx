import { ChevronDown, ChevronRight } from "lucide-react"
import type { OnUpdateConfig } from "../editors"
import { configField, safeParseInt } from "../../utils/configField"

type Column = { name: string; dtype: string }


export type SplitAndMetricsConfigProps = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  columns: Column[]
  target: string
  weight: string
  exclude: string[]
  split: Record<string, unknown>
  mlflowOpen: boolean
  monotonicOpen: boolean
  toggleSection: (section: string) => void
  onSplitUpdate: (key: string, value: unknown) => void
}

export function SplitAndMetricsConfig({
  config,
  onUpdate,
  columns,
  target,
  weight,
  exclude,
  split,
  mlflowOpen,
  monotonicOpen,
  toggleSection,
  onSplitUpdate,
}: SplitAndMetricsConfigProps) {
  return (
    <>
      {/* Split Strategy */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Split Strategy</label>
        <div className="mt-1.5 space-y-2">
          <div className="flex gap-2">
            {["random", "temporal", "group"].map(s => (
              <button
                key={s}
                onClick={() => onSplitUpdate("strategy", s)}
                className="px-3 py-1 rounded-md text-xs font-medium transition-colors"
                style={{
                  background: split.strategy === s ? "var(--accent-soft)" : "var(--bg-input)",
                  color: split.strategy === s ? "var(--accent)" : "var(--text-secondary)",
                  border: `1px solid ${split.strategy === s ? "var(--accent)" : "var(--border)"}`,
                }}
              >
                {s}
              </button>
            ))}
          </div>
          {split.strategy === "random" && (
            <div className="space-y-2">
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Validation</label>
                  <input
                    type="number" step={0.05} min={0} max={0.5}
                    value={(split.validation_size as number) ?? (split.test_size as number) ?? 0.2}
                    onChange={(e) => onSplitUpdate("validation_size", parseFloat(e.target.value) || 0)}
                    className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                    style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                  />
                </div>
                <div>
                  <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Holdout</label>
                  <input
                    type="number" step={0.05} min={0} max={0.5}
                    value={(split.holdout_size as number) ?? 0}
                    onChange={(e) => onSplitUpdate("holdout_size", parseFloat(e.target.value) || 0)}
                    className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                    style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                  />
                </div>
                <div>
                  <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Seed</label>
                  <input
                    type="number"
                    value={(split.seed as number) ?? 42}
                    onChange={(e) => onSplitUpdate("seed", safeParseInt(e.target.value, 42))}
                    className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                    style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                  />
                </div>
              </div>
              {(() => {
                const valSize = (split.validation_size as number) ?? (split.test_size as number) ?? 0.2
                const holdoutSize = (split.holdout_size as number) ?? 0
                const trainSize = Math.max(0, 1 - valSize - holdoutSize)
                return (
                  <div className="flex gap-0.5 h-1.5 rounded-full overflow-hidden" style={{ background: "var(--chrome-hover)" }}>
                    <div style={{ width: `${trainSize * 100}%`, background: "#a855f7" }} title={`Train: ${(trainSize * 100).toFixed(0)}%`} />
                    {valSize > 0 && <div style={{ width: `${valSize * 100}%`, background: "#3b82f6" }} title={`Validation: ${(valSize * 100).toFixed(0)}%`} />}
                    {holdoutSize > 0 && <div style={{ width: `${holdoutSize * 100}%`, background: "#22c55e" }} title={`Holdout: ${(holdoutSize * 100).toFixed(0)}%`} />}
                  </div>
                )
              })()}
            </div>
          )}
          {split.strategy === "temporal" && (
            <div className="space-y-2">
              <div>
                <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Date column</label>
                <select
                  value={configField(split, "date_column", "")}
                  onChange={(e) => onSplitUpdate("date_column", e.target.value)}
                  className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
                  style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                >
                  <option value="">Select...</option>
                  {columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
                </select>
              </div>
              <div>
                <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Cutoff date</label>
                <input
                  type="date"
                  value={configField(split, "cutoff_date", "")}
                  onChange={(e) => onSplitUpdate("cutoff_date", e.target.value)}
                  className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
                  style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                />
              </div>
            </div>
          )}
          {split.strategy === "group" && (
            <div className="space-y-2">
              <div>
                <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Group column</label>
                <select
                  value={configField(split, "group_column", "")}
                  onChange={(e) => onSplitUpdate("group_column", e.target.value)}
                  className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
                  style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                >
                  <option value="">Select...</option>
                  {columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
                </select>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Validation</label>
                  <input
                    type="number" step={0.05} min={0} max={0.5}
                    value={(split.validation_size as number) ?? (split.test_size as number) ?? 0.2}
                    onChange={(e) => onSplitUpdate("validation_size", parseFloat(e.target.value) || 0)}
                    className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                    style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                  />
                </div>
                <div>
                  <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Holdout</label>
                  <input
                    type="number" step={0.05} min={0} max={0.5}
                    value={(split.holdout_size as number) ?? 0}
                    onChange={(e) => onSplitUpdate("holdout_size", parseFloat(e.target.value) || 0)}
                    className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                    style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                  />
                </div>
              </div>
            </div>
          )}
          {/* Row limit */}
          {(() => {
            const rowLimit = typeof config.row_limit === "number" ? config.row_limit : null
            return (
              <div className="flex items-center gap-2 mt-2">
                <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Row limit</label>
                <input
                  type="number" min={0} step={100000}
                  value={rowLimit ?? ""}
                  onChange={(e) => {
                    const v = e.target.value;
                    onUpdate("row_limit", v === "" ? null : Math.max(0, parseInt(v) || 0));
                  }}
                  placeholder="All rows"
                  className="w-32 px-2 py-0.5 rounded text-xs font-mono"
                  style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                />
                {rowLimit != null && rowLimit > 0 && (
                  <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
                    {rowLimit.toLocaleString()} rows
                  </span>
                )}
              </div>
            )
          })()}
        </div>
      </div>

      {/* MLflow (collapsible) */}
      <div>
        <button
          onClick={() => toggleSection("modelling.mlflow")}
          className="flex items-center gap-1 text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-muted)" }}
        >
          {mlflowOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          MLflow Logging
        </button>
        {mlflowOpen && (
          <div className="mt-1.5 space-y-2">
            <div>
              <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Experiment path</label>
              <input
                type="text"
                placeholder="/Shared/haute/experiment"
                value={configField(config, "mlflow_experiment", "")}
                onChange={(e) => onUpdate("mlflow_experiment", e.target.value)}
                className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
                style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              />
            </div>
            <div>
              <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Model name (registered model)</label>
              <input
                type="text"
                placeholder="Optional"
                value={configField(config, "model_name", "")}
                onChange={(e) => onUpdate("model_name", e.target.value)}
                className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
                style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              />
            </div>
          </div>
        )}
      </div>

      {/* Monotonic Constraints (collapsible) */}
      {columns.length > 0 && (
        <div>
          <button
            onClick={() => toggleSection("modelling.monotonic")}
            className="flex items-center gap-1 text-[11px] font-bold uppercase tracking-[0.08em]"
            style={{ color: "var(--text-muted)" }}
          >
            {monotonicOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            Monotonic Constraints
          </button>
          {monotonicOpen && (
            <div className="mt-1.5 space-y-1">
              <div className="text-[10px]" style={{ color: "var(--text-muted)" }}>Set per-feature constraints (numeric features only)</div>
              {columns
                .filter(c => c.name !== target && c.name !== weight && !exclude.includes(c.name) && !["Utf8", "Categorical", "String"].includes(c.dtype))
                .sort((a, b) => a.name.localeCompare(b.name))
                .map(c => {
                  const mc = configField<Record<string, number>>(config, "monotone_constraints", {})
                  const val = mc[c.name] ?? 0
                  return (
                    <div key={c.name} className="flex items-center gap-2">
                      <span className="text-[11px] font-mono flex-1 truncate" style={{ color: "var(--text-secondary)" }}>{c.name}</span>
                      {([-1, 0, 1] as const).map(v => (
                        <button
                          key={v}
                          onClick={() => {
                            const newMc = { ...mc }
                            if (v === 0) { delete newMc[c.name] } else { newMc[c.name] = v }
                            onUpdate("monotone_constraints", Object.keys(newMc).length > 0 ? newMc : null)
                          }}
                          className="px-1.5 py-0.5 rounded text-[10px] font-mono"
                          style={{
                            background: val === v ? (v === 1 ? "rgba(34,197,94,.15)" : v === -1 ? "rgba(239,68,68,.15)" : "var(--accent-soft)") : "var(--chrome-hover)",
                            color: val === v ? (v === 1 ? "#22c55e" : v === -1 ? "#ef4444" : "var(--accent)") : "var(--text-muted)",
                            border: `1px solid ${val === v ? (v === 1 ? "rgba(34,197,94,.3)" : v === -1 ? "rgba(239,68,68,.3)" : "var(--accent)") : "transparent"}`,
                          }}
                        >
                          {v === 1 ? "+1" : v === -1 ? "-1" : "0"}
                        </button>
                      ))}
                    </div>
                  )
                })}
            </div>
          )}
        </div>
      )}
    </>
  )
}
