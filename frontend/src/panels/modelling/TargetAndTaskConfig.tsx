import type { OnUpdateConfig } from "../editors"
import { configField } from "../../utils/configField"
import { toggleButtonStyle } from "./styles"
import { FailoverHelp } from "./FailoverHelp"
import { OffsetFieldLabel } from "./OffsetFieldLabel"

const TWEEDIE_HELP =
  "Tweedie interpolates between Poisson (power 1) and Gamma (power 2); the " +
  "variance power sets where. New selections start at the 1.5 midpoint. You " +
  "can adjust it here; the value is kept if you switch loss and back."

type Column = { name: string; dtype: string }

// Default reported metrics per loss — mirrors the GLM family buttons and the
// backend's default_metrics(): the headline metrics follow the objective.
const LOSSES: ReadonlyArray<{
  value: string
  task: "regression" | "classification"
  defaultMetrics: string[]
}> = [
  { value: "RMSE", task: "regression", defaultMetrics: ["gini", "rmse"] },
  { value: "MAE", task: "regression", defaultMetrics: ["gini", "rmse"] },
  { value: "Poisson", task: "regression", defaultMetrics: ["gini", "poisson_deviance"] },
  { value: "Tweedie", task: "regression", defaultMetrics: ["gini", "tweedie_deviance"] },
  { value: "Logloss", task: "classification", defaultMetrics: ["auc", "logloss"] },
  { value: "CrossEntropy", task: "classification", defaultMetrics: ["auc", "logloss"] },
]
const REGRESSION_METRICS = ["gini", "rmse", "mae", "mse", "r2", "poisson_deviance", "tweedie_deviance"]
const CLASSIFICATION_METRICS = ["auc", "logloss"]
const METRICS = [...REGRESSION_METRICS, ...CLASSIFICATION_METRICS]

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
  metrics: string[]
}

export function TargetAndTaskConfig({ config, onUpdate, columns, target, weight, metrics }: TargetAndTaskConfigProps) {
  const currentLoss = configField(config, "loss_function", "")
  const variancePower = configField(config, "variance_power", 1.5)
  const compatibleTask = LOSSES.find(loss => loss.value === currentLoss)?.task
  const compatibleMetrics = new Set(
    compatibleTask === "regression"
      ? REGRESSION_METRICS
      : compatibleTask === "classification"
        ? CLASSIFICATION_METRICS
        : [],
  )
  return (
    <div>
      <p className="text-[10px] mb-1" aria-label="Selected algorithm">Algorithm: <strong>CatBoost</strong></p>
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
        {/* Loss Function */}
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Loss Function</label>
          <div role="group" aria-label="Loss functions" className="mt-1.5 flex flex-wrap gap-1.5">
            {LOSSES.map(loss => {
              const selected = currentLoss === loss.value
              return (
                <button
                  type="button"
                  key={loss.value}
                  aria-pressed={selected}
                  onClick={() => {
                    if (selected) {
                      onUpdate("loss_function", null)
                    } else {
                      onUpdate({
                        loss_function: loss.value,
                        task: loss.task,
                        metrics: [...loss.defaultMetrics],
                        ...(loss.value === "Tweedie" && config.variance_power == null
                          ? { variance_power: 1.5 }
                          : {}),
                      })
                    }
                  }}
                  className="px-2.5 py-1 rounded-md text-xs font-mono transition-colors"
                  style={toggleButtonStyle(selected)}
                >
                  {loss.value}
                </button>
              )
            })}
          </div>
          {configField(config, "loss_function", "") === "Tweedie" && (
            <div className="mt-2">
              <label className="flex items-center gap-1 text-[11px]" style={{ color: "var(--text-muted)" }}>
                Variance power (1.0=Poisson, 2.0=Gamma)
                <FailoverHelp label={TWEEDIE_HELP} />
              </label>
              <input
                type="range" min={1.0} max={2.0} step={0.05}
                value={variancePower}
                onChange={(e) => onUpdate("variance_power", parseFloat(e.target.value))}
                className="w-full mt-0.5"
              />
              <div className="text-[11px] font-mono text-right" style={{ color: "var(--text-muted)" }}>
                {variancePower.toFixed(2)}
              </div>
            </div>
          )}
        </div>
        {/* Metrics */}
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Metrics</label>
          <div role="group" aria-label="Metrics" className="mt-1.5 flex flex-wrap gap-1.5">
            {METRICS.map(m => {
              const selected = metrics.includes(m)
              const compatible = compatibleMetrics.has(m)
              const unavailableTitle = currentLoss
                ? `${METRIC_LABELS[m] ?? m} is not available with ${currentLoss}`
                : "Choose a loss function to enable compatible metrics"
              return (
                <button
                  type="button"
                  key={m}
                  aria-pressed={selected}
                  disabled={!compatible}
                  title={compatible ? undefined : unavailableTitle}
                  onClick={() => {
                    if (!compatible) return
                    const newMetrics = selected ? metrics.filter(x => x !== m) : [...metrics, m]
                    onUpdate("metrics", newMetrics)
                  }}
                  className="px-2.5 py-1 rounded-md text-xs font-mono transition-colors disabled:cursor-not-allowed disabled:opacity-40"
                  style={toggleButtonStyle(compatible && selected)}
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
