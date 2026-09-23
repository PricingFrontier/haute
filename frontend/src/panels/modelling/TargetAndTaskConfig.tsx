import { ConfigSection } from "../../components/form"
import type { OnUpdateConfig } from "../editors"
import { configField } from "../../utils/configField"
import { isNumericDtype } from "../../utils/polarsDtypes"
import { toggleButtonStyle } from "./styles"
import { FailoverHelp } from "./FailoverHelp"
import { OffsetFieldLabel } from "./OffsetFieldLabel"
import { ColumnSelector } from "./ColumnSelector"
import { NumberField } from "./NumberField"

const TWEEDIE_HELP =
  "Tweedie interpolates between Poisson (power 1) and Gamma (power 2); new selections start at the 1.5 midpoint."
type Column = { name: string; dtype: string }
const LOSSES: ReadonlyArray<{
  value: string
  task: "regression" | "classification"
  defaultMetrics: string[]
}> = [
  { value: "RMSE", task: "regression", defaultMetrics: ["gini", "rmse"] },
  { value: "MAE", task: "regression", defaultMetrics: ["gini", "rmse"] },
  {
    value: "Poisson",
    task: "regression",
    defaultMetrics: ["gini", "poisson_deviance"],
  },
  {
    value: "Tweedie",
    task: "regression",
    defaultMetrics: ["gini", "tweedie_deviance"],
  },
  {
    value: "Logloss",
    task: "classification",
    defaultMetrics: ["auc", "logloss"],
  },
  {
    value: "CrossEntropy",
    task: "classification",
    defaultMetrics: ["auc", "logloss"],
  },
]
const REGRESSION_METRICS = [
  "gini",
  "rmse",
  "mae",
  "mse",
  "r2",
  "poisson_deviance",
  "tweedie_deviance",
]
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

export function TargetAndTaskConfig({
  config,
  onUpdate,
  columns,
  target,
  weight,
  metrics,
}: TargetAndTaskConfigProps) {
  const currentLoss = configField(config, "loss_function", "")
  const offset = configField(config, "offset", "")
  const variancePower =
    typeof config.variance_power === "number"
      ? config.variance_power
      : undefined
  const validVariancePower =
    variancePower !== undefined && variancePower > 1 && variancePower < 2
  const compatibleTask = LOSSES.find((loss) => loss.value === currentLoss)?.task
  const compatibleMetrics = new Set(
    compatibleTask === "regression"
      ? REGRESSION_METRICS
      : compatibleTask === "classification"
        ? CLASSIFICATION_METRICS
        : [],
  )
  const targetColumns = columns.filter(
    (column) => column.name !== weight && column.name !== offset,
  )
  const weightColumns = columns.filter(
    (column) =>
      isNumericDtype(column.dtype) &&
      column.name !== target &&
      column.name !== offset,
  )
  const offsetColumns = columns.filter(
    (column) =>
      isNumericDtype(column.dtype) &&
      column.name !== target &&
      column.name !== weight,
  )
  return (
    <div>
      <p className="mb-1 text-[10px]" aria-label="Selected algorithm">
        Algorithm: <strong>CatBoost</strong>
      </p>
      <div className="space-y-3">
        <ConfigSection title="Target and objective">
          <div className="space-y-2">
            <div>
              <label
                className="text-[13px]"
                style={{ color: "var(--text-secondary)" }}
              >
                Target column
              </label>
              <ColumnSelector
                label="Target column"
                value={target}
                columns={targetColumns}
                onChange={(next) => onUpdate("target", next)}
                placeholder="Select target…"
              />
            </div>
            <div>
              <h4
                className="text-[13px]"
                style={{ color: "var(--text-secondary)" }}
              >
                Objective
              </h4>
              <div
                role="group"
                aria-label="Loss functions"
                className="mt-1.5 flex flex-wrap gap-1.5"
              >
                {LOSSES.map((loss) => {
                  const selected = currentLoss === loss.value
                  return (
                    <button
                      type="button"
                      key={loss.value}
                      aria-pressed={selected}
                      onClick={() =>
                        selected
                          ? onUpdate("loss_function", null)
                          : onUpdate({
                              loss_function: loss.value,
                              task: loss.task,
                              metrics: [...loss.defaultMetrics],
                              ...(loss.value === "Tweedie" &&
                              config.variance_power == null
                                ? { variance_power: 1.5 }
                                : {}),
                            })
                      }
                      className="rounded-md px-2.5 py-1 text-xs font-mono transition-colors"
                      style={toggleButtonStyle(selected)}
                    >
                      {loss.value}
                    </button>
                  )
                })}
              </div>
              {currentLoss === "Tweedie" && (
                <div className="mt-2">
                  <label
                    className="flex items-center gap-1 text-[13px]"
                    style={{ color: "var(--text-secondary)" }}
                  >
                    Variance power <FailoverHelp label={TWEEDIE_HELP} />
                  </label>
                  {validVariancePower && (
                    <input
                      aria-label="Adjust variance power"
                      type="range"
                      min={1.01}
                      max={1.99}
                      step={0.01}
                      value={variancePower}
                      aria-valuetext={String(variancePower)}
                      onChange={(event) =>
                        onUpdate(
                          "variance_power",
                          parseFloat(event.target.value),
                        )
                      }
                      className="mt-0.5 w-full accent-[var(--model-accent)]"
                    />
                  )}
                  <div className="mt-1 flex items-center gap-2">
                    <NumberField
                      label="Variance power"
                      value={variancePower}
                      min={1}
                      max={2}
                      exclusiveMin
                      exclusiveMax
                      integer={false}
                      required
                      step="any"
                      onCommit={(value) =>
                        onUpdate("variance_power", value ?? null)
                      }
                    />
                    <span
                      className="text-[12px]"
                      style={{ color: "var(--text-muted)" }}
                    >
                      Choose a value greater than 1 and less than 2. Poisson and
                      Gamma are the limiting cases.
                    </span>
                  </div>
                  {variancePower !== undefined &&
                    !(variancePower > 1 && variancePower < 2) && (
                      <p
                        role="alert"
                        className="text-[12px]"
                        style={{ color: "var(--danger)" }}
                      >
                        Saved variance power must be greater than 1 and less
                        than 2.
                      </p>
                    )}
                </div>
              )}
            </div>
          </div>
        </ConfigSection>
        <ConfigSection title="Weight and offset">
          <div className="space-y-2">
            <div>
              <label
                className="text-[13px]"
                style={{ color: "var(--text-secondary)" }}
              >
                Weight column (optional)
              </label>
              <ColumnSelector
                label="Weight column"
                value={weight}
                columns={weightColumns}
                onChange={(next) => onUpdate("weight", next)}
                optional
              />
            </div>
            <div>
              <OffsetFieldLabel />
              <ColumnSelector
                label="Offset column"
                value={offset}
                columns={offsetColumns}
                onChange={(next) => onUpdate("offset", next || null)}
                optional
              />
            </div>
          </div>
        </ConfigSection>
        <ConfigSection title="Metrics">
          <div
            role="group"
            aria-label="Metrics"
            className="flex flex-wrap gap-1.5"
          >
            {METRICS.map((metric) => {
              const selected = metrics.includes(metric)
              const compatible = compatibleMetrics.has(metric)
              return (
                <button
                  type="button"
                  key={metric}
                  aria-pressed={selected}
                  disabled={!compatible}
                  title={
                    compatible
                      ? undefined
                      : currentLoss
                        ? `${METRIC_LABELS[metric]} is not available with ${currentLoss}`
                        : "Choose an objective to enable compatible metrics"
                  }
                  onClick={() =>
                    compatible &&
                    onUpdate(
                      "metrics",
                      selected
                        ? metrics.filter((entry) => entry !== metric)
                        : [...metrics, metric],
                    )
                  }
                  className="rounded-md px-2.5 py-1 text-xs font-mono transition-colors disabled:cursor-not-allowed disabled:opacity-40"
                  style={toggleButtonStyle(compatible && selected)}
                >
                  {METRIC_LABELS[metric]}
                </button>
              )
            })}
          </div>
        </ConfigSection>
      </div>
    </div>
  )
}
