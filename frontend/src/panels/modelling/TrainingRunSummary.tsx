import type { EvaluationPreview } from "../../api/types"
import { configField } from "../../utils/configField"
import { algorithmCapability } from "./algorithmCapabilities"
import { roleColumnReasons, type ModellingColumn } from "./featureSelection"
import {
  columnContext,
  interactionEntryIssue,
  modelMembership,
  type InteractionSpec,
  type Terms,
} from "./glmTerms"
import { glmCrossValidates } from "./glmFamilies"
import { trainingFitBudget } from "./trainingFitBudget"
import { evaluationConfigurationIssues } from "../../utils/trainingObjective"

export function TrainingRunSummary({
  config,
  columns,
  preview,
}: {
  config: Record<string, unknown>
  columns: ModellingColumn[]
  preview: EvaluationPreview | null
}) {
  const glm = config.algorithm === "glm"
  const roles = roleColumnReasons(config)
  const eligible = columns.filter((column) => !roles.has(column.name))
  const excluded = configField<string[]>(config, "exclude", [])
  const interactions = configField<unknown[]>(
    config,
    "interactions",
    [],
  ).filter(
    (entry): entry is InteractionSpec => interactionEntryIssue(entry) === null,
  )
  const featureCount = glm
    ? modelMembership(
        configField<Terms>(config, "terms", {}),
        interactions,
        columnContext(columns, roles),
      ).inModel.size
    : eligible.filter((column) => !excluded.includes(column.name)).length
  const evaluation = configField<Record<string, unknown>>(
    config,
    "evaluation",
    {},
  )
  const validation = configField<Record<string, unknown>>(
    evaluation,
    "validation",
    {},
  )
  const test = configField<Record<string, unknown> | null>(
    evaluation,
    "test",
    null,
  )
  const tuning = configField<Record<string, unknown> | null>(
    config,
    "tuning",
    null,
  )
  const refitOnDevelopment = config.refit_on_development !== false
  const budget = trainingFitBudget(evaluation, tuning, refitOnDevelopment)
  const fitBudget = !budget
    ? "Complete evaluation settings"
    : !refitOnDevelopment && budget.selection === 1
      ? "1 validation fit (saved model)"
      : budget.selection === 0
      ? "1 final fit"
      : `${budget.total} total fits: ${budget.selection} ${tuning ? "tuning" : "validation"} ${budget.selection === 1 ? "fit" : "fits"} + 1 final fit`
  const params = configField<Record<string, unknown>>(config, "params", {})
  const method =
    validation.method === "cross_validation"
      ? `${validation.fold_count}-fold cross-validation`
      : validation.method === "single"
        ? "Holdout validation"
        : validation.method === "none"
          ? "No validation"
          : "Choose validation"
  const trainingPoolLabel = validation.method === "single" ? "training + validation" : "training"
  let allocation = preview
    ? `${preview.development_rows.toLocaleString()} ${trainingPoolLabel} / ${preview.final_test_rows.toLocaleString()} test rows`
    : test === null
      ? "No test set reserved"
      : evaluation.strategy === "temporal"
        ? `Test starts ${String(test.start || "—")}`
        : `${(Number(test.size) * 100).toLocaleString()}% reserved for the test set`
  if (evaluationConfigurationIssues(evaluation).length > 0) {
    allocation = "Complete the split settings"
  } else if (validation.method === "single") {
    if (
      preview?.min_selection_train_rows !== undefined &&
      preview.min_selection_validation_rows !== undefined
    ) {
      allocation = `${preview.min_selection_train_rows.toLocaleString()} training / ${preview.min_selection_validation_rows.toLocaleString()} validation / ${preview.final_test_rows.toLocaleString()} test rows`
    } else if (evaluation.strategy !== "temporal") {
      const validationPercent = Number(validation.size) * 100
      const testPercent = test ? Number(test.size) * 100 : 0
      allocation = `${Number((100 - validationPercent - testPercent).toFixed(2))}% training / ${validationPercent}% validation / ${testPercent}% test`
    }
  }
  return (
    <section
      aria-label="Training run summary"
      className="rounded-xl border p-4"
      style={{ borderColor: "var(--border)", background: "var(--bg-input)" }}
    >
      <h3 className="text-sm font-semibold">Run summary</h3>
      <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-[13px]">
        <dt style={{ color: "var(--text-muted)" }}>Model</dt>
        <dd>
          {algorithmCapability(String(config.algorithm ?? ""))?.label ?? String(config.algorithm ?? "")} ·{" "}
          {String(
            glm
              ? (config.family ?? "Choose family")
              : (config.loss_function ?? "Choose loss"),
          )}
        </dd>
        <dt style={{ color: "var(--text-muted)" }}>Target</dt>
        <dd className="break-all font-mono">
          {String(config.target || "Choose target")}
        </dd>
        <dt style={{ color: "var(--text-muted)" }}>Inputs</dt>
        <dd>
          {columns.length > 0
            ? `${featureCount} features`
            : "Waiting for columns"}
        </dd>
        <dt style={{ color: "var(--text-muted)" }}>Evaluation</dt>
        <dd>
          {method}
          <span
            className="mt-0.5 block text-xs"
            style={{ color: "var(--text-muted)" }}
          >
            {allocation}
          </span>
        </dd>
        <dt style={{ color: "var(--text-muted)" }}>Fit budget</dt>
        <dd>
          {fitBudget}
          {glm && glmCrossValidates(config) && (
            <span
              className="mt-0.5 block text-xs"
              style={{ color: "var(--text-muted)" }}
            >
              Each fit also selects a penalty using{" "}
              {String(config.cv_folds ?? "—")} internal CV folds.
            </span>
          )}
        </dd>
        <dt style={{ color: "var(--text-muted)" }}>Compute</dt>
        <dd>{config.algorithm === "catboost" && params.task_type === "GPU" ? "GPU (CUDA)" : "CPU"}</dd>
      </dl>
    </section>
  )
}
