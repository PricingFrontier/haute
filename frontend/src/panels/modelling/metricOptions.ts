/**
 * The metrics training reports, in one table for every family's picker.
 *
 * Each metric measures a regression or a classification. Tweedie deviance is
 * measured at the model's own variance power, so only a Tweedie objective
 * offers it; the backend refuses it for any other.
 */

export type MetricTask = "regression" | "classification"

export const METRIC_OPTIONS: ReadonlyArray<{ value: string; label: string; task: MetricTask }> = [
  { value: "gini", label: "Gini", task: "regression" },
  { value: "rmse", label: "RMSE", task: "regression" },
  { value: "mae", label: "MAE", task: "regression" },
  { value: "mse", label: "MSE", task: "regression" },
  { value: "r2", label: "R²", task: "regression" },
  { value: "poisson_deviance", label: "Poisson Deviance", task: "regression" },
  { value: "tweedie_deviance", label: "Tweedie Deviance", task: "regression" },
  { value: "gamma_deviance", label: "Gamma Deviance", task: "regression" },
  { value: "auc", label: "AUC", task: "classification" },
  { value: "logloss", label: "Logloss", task: "classification" },
]

/** A reported metric's value as every modelling surface shows it (four decimals). */
export function formatMetricValue(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(4) : "N/A"
}

export function metricLabel(metric: string): string {
  return METRIC_OPTIONS.find((option) => option.value === metric)?.label ?? metric
}

/** Whether *metric* can be reported for a *task* model trained under *objective*. */
export function metricCompatible(
  metric: string,
  task: MetricTask | undefined,
  objective: string,
): boolean {
  const option = METRIC_OPTIONS.find((entry) => entry.value === metric)
  if (option === undefined || option.task !== task) return false
  return metric !== "tweedie_deviance" || objective.toLowerCase() === "tweedie"
}

const GLM_CLASSIFICATION_FAMILIES = new Set(["binomial", "quasibinomial"])

/** The task and objective a configuration's metrics are measured under. */
export function metricObjective(
  config: Record<string, unknown>,
): { task: MetricTask | undefined; objective: string } {
  if (String(config.algorithm ?? "catboost").toLowerCase() === "glm") {
    const family = typeof config.family === "string" ? config.family.toLowerCase() : ""
    if (family === "") return { task: undefined, objective: "" }
    return {
      task: GLM_CLASSIFICATION_FAMILIES.has(family) ? "classification" : "regression",
      objective: family,
    }
  }
  const loss = typeof config.loss_function === "string" ? config.loss_function : ""
  const task = config.task === "classification" || config.task === "regression" ? config.task : undefined
  return { task: loss === "" ? undefined : task, objective: loss }
}
