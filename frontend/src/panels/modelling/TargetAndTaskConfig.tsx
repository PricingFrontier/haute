import type { OnUpdateConfig } from "../editors"
import { configField } from "../../utils/configField"
import { toggleButtonStyle } from "./styles"
import { OffsetFieldLabel } from "./OffsetFieldLabel"

type Column = { name: string; dtype: string }

const REGRESSION_LOSSES = ["RMSE", "MAE", "Poisson", "Tweedie"]
const CLASSIFICATION_LOSSES = ["Logloss", "CrossEntropy"]
const REGRESSION_METRICS = ["gini", "rmse", "mae", "mse", "r2", "poisson_deviance", "tweedie_deviance"]
const CLASSIFICATION_METRICS = ["auc", "logloss"]

const METRIC_LABELS: Record<string, string> = {
  gini: "Gini",
  rmse: "RMSE",
  mae: "MAE",
  mse: "MSE",
  r2: "R²",
  poisson_deviance: "Poisson Deviance",
  tweedie_deviance: "Tweedie Deviance",
  auc: "AUC",
  logloss: "Logloss",
}

export type TargetAndTaskConfigProps = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  columns: Column[]
  target: string
  weight: string
  task: string
  metrics: string[]
}

export function TargetAndTaskConfig({ config, onUpdate, columns, target, weight, task, metrics }: TargetAndTaskConfigProps) {
  const availableLosses = task === "classification" ? CLASSIFICATION_LOSSES : REGRESSION_LOSSES
  const availableMetrics = task === "classification" ? CLASSIFICATION_METRICS : REGRESSION_METRICS
  return (
    <div>
      <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Target & Weight</label>
      <div className="mt-1.5 space-y-2">
        <div>
          <label className="text-xs" style={{ color: "var(--text-secondary)" }}>Target column</label>
          <select
            value={target}
            onChange={(e) => onUpdate("target", e.target.value)}
            className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
            style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
          >
            <option value="">Select target...</option>
            {columns.map(c => <option key={c.name} value={c.name}>{c.name} ({c.dtype})</option>)}
          </select>
        </div>
        <div>
          <label className="text-xs" style={{ color: "var(--text-secondary)" }}>Weight column (optional)</label>
          <select
            value={weight}
            onChange={(e) => onUpdate("weight", e.target.value)}
            className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
            style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
          >
            <option value="">None</option>
            {columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
          </select>
        </div>
        <div>
          <OffsetFieldLabel />
          <select
            value={configField(config, "offset", "")}
            onChange={(e) => onUpdate("offset", e.target.value || null)}
            className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
            style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
          >
            <option value="">None</option>
            {columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
          </select>
        </div>
        <div>
          <label className="text-xs" style={{ color: "var(--text-secondary)" }}>Task</label>
          <div className="flex gap-2 mt-0.5">
            {["regression", "classification"].map(t => (
              <button
                key={t}
                onClick={() => {
                  onUpdate({
                    task: t,
                    metrics: t === "regression" ? ["gini", "rmse"] : ["auc", "logloss"],
                    loss_function: null,
                  })
                }}
                className="px-3 py-1 rounded-md text-xs font-medium transition-colors"
                style={{
                  background: task === t ? "var(--accent-soft)" : "var(--bg-input)",
                  color: task === t ? "var(--accent)" : "var(--text-secondary)",
                  border: `1px solid ${task === t ? "var(--accent)" : "var(--border)"}`,
                }}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
        {/* Loss Function */}
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Loss Function</label>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {availableLosses.map(l => {
              const currentLoss = configField(config, "loss_function", "")
              const selected = currentLoss === l
              return (
                <button
                  key={l}
                  onClick={() => onUpdate("loss_function", selected ? null : l)}
                  className="px-2.5 py-1 rounded-md text-xs font-mono transition-colors"
                  style={toggleButtonStyle(selected)}
                >
                  {l}
                </button>
              )
            })}
          </div>
          {configField(config, "loss_function", "") === "Tweedie" && (
            <div className="mt-2">
              <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Variance power (1.0=Poisson, 2.0=Gamma)</label>
              <input
                type="range" min={1.0} max={2.0} step={0.05}
                value={configField(config, "variance_power", 1.5)}
                onChange={(e) => onUpdate("variance_power", parseFloat(e.target.value))}
                className="w-full mt-0.5"
              />
              <div className="text-[11px] font-mono text-right" style={{ color: "var(--text-muted)" }}>
                {configField(config, "variance_power", 1.5).toFixed(2)}
              </div>
            </div>
          )}
        </div>
        {/* Metrics */}
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Metrics</label>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {availableMetrics.map(m => {
              const selected = metrics.includes(m)
              return (
                <button
                  key={m}
                  onClick={() => {
                    const newMetrics = selected ? metrics.filter(x => x !== m) : [...metrics, m]
                    onUpdate("metrics", newMetrics)
                  }}
                  className="px-2.5 py-1 rounded-md text-xs font-mono transition-colors"
                  style={toggleButtonStyle(selected)}
                >
                  {METRIC_LABELS[m] ?? m}
                </button>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
