/**
 * Summary tab for completed model training results.
 *
 * Final-test performance is deliberately separated from validation metrics
 * used for model or parameter selection.
 */
import type {
  EvaluationMetricSummary,
  TuningReport,
} from "../../api/types"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { MlflowExportSection } from "./MlflowExportSection"

interface SummaryTabProps {
  result: TrainResult
  jobId: string
  mlflowBackend: { installed: boolean; backend: string; host: string } | null
  config: Record<string, unknown>
  onUseBestParameters?: (params: Record<string, unknown>) => void
  elapsedSeconds?: number | null
}

function formatDiagnosticLabel(diagnostic: string): string {
  switch (diagnostic) {
    case "glm_coefficients":
      return "GLM coefficients"
    case "pdp":
      return "PDP"
    case "shap":
      return "SHAP"
    default:
      return diagnostic
        .split("_")
        .filter(Boolean)
        .map(part => part.charAt(0).toUpperCase() + part.slice(1))
        .join(" ")
  }
}

function formatNumber(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(4)
    : "N/A"
}

function MetricsList({
  label,
  metrics,
}: {
  label: string
  metrics: Record<string, number>
}) {
  if (Object.keys(metrics).length === 0) return null

  return (
    <div className="min-w-[180px]">
      <label
        className="text-[11px] font-bold uppercase tracking-[0.08em]"
        style={{ color: "var(--text-muted)" }}
      >
        {label}
      </label>
      <div className="mt-1 space-y-0.5">
        {Object.entries(metrics).map(([name, value]) => (
          <div key={name} className="flex justify-between text-xs font-mono gap-4">
            <span style={{ color: "var(--text-secondary)" }}>{name}</span>
            <span style={{ color: "var(--text-primary)" }}>{formatNumber(value)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function validationLabel(
  method: "none" | "single" | "cross_validation",
  count: number,
): string {
  if (method === "none") return "No validation"
  if (method === "single") return "Single validation"
  return `${count}-fold cross-validation`
}

function sortedTopTrials(tuning: TuningReport) {
  return [...tuning.trials]
    .sort((left, right) => {
      const objectiveOrder = tuning.direction === "maximize"
        ? right.objective - left.objective
        : left.objective - right.objective
      return objectiveOrder || left.trial_index - right.trial_index
    })
    .slice(0, 10)
}

function SelectionMetricsTable({
  metrics,
}: {
  metrics: Record<string, EvaluationMetricSummary>
}) {
  const names = Object.keys(metrics).sort()
  if (names.length === 0) return null

  return (
    <div className="overflow-x-auto">
      <table
        aria-label="Selection aggregate metrics"
        className="w-full text-xs font-mono"
      >
        <thead>
          <tr style={{ color: "var(--text-muted)" }}>
            <th className="py-1 pr-3 text-left font-medium">Metric</th>
            <th className="px-2 py-1 text-right font-medium">Mean</th>
            <th className="px-2 py-1 text-right font-medium">Std dev</th>
            <th className="px-2 py-1 text-right font-medium">Min</th>
            <th className="px-2 py-1 text-right font-medium">Max</th>
            <th className="pl-2 py-1 text-right font-medium">Validation rows</th>
          </tr>
        </thead>
        <tbody>
          {names.map(name => {
            const summary = metrics[name]
            return (
              <tr key={name} style={{ color: "var(--text-primary)" }}>
                <th className="py-1 pr-3 text-left font-medium">{name}</th>
                <td className="px-2 py-1 text-right">{formatNumber(summary.mean)}</td>
                <td className="px-2 py-1 text-right">{formatNumber(summary.stddev)}</td>
                <td className="px-2 py-1 text-right">{formatNumber(summary.min)}</td>
                <td className="px-2 py-1 text-right">{formatNumber(summary.max)}</td>
                <td className="pl-2 py-1 text-right">
                  {summary.validation_rows.toLocaleString()}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function SummaryTab({
  result,
  jobId,
  mlflowBackend,
  config,
  onUseBestParameters,
  elapsedSeconds,
}: SummaryTabProps) {
  const featuresCount = result.features?.length ?? result.feature_importance.length
  const catFeaturesCount = result.cat_features?.length ?? 0
  const diagnosticsLabel = result.diagnostics_set === "final_test"
    ? "Final test"
    : "Development"
  const diagnosticsErrors = result.diagnostics_errors ?? []
  const evaluation = result.evaluation
  const tuning = result.tuning
  const selectionMetricNames = evaluation
    ? Object.keys(evaluation.selection_metrics).sort()
    : []
  const completedElapsedSeconds = (
    typeof elapsedSeconds === "number"
    && Number.isFinite(elapsedSeconds)
    && elapsedSeconds >= 0
  )
    ? elapsedSeconds
    : null

  return (
    <div className="flex gap-6 flex-wrap">
      {result.warning && (
        <div
          className="w-full flex items-start gap-2 px-3 py-2 rounded-lg text-xs"
          style={{
            background: "var(--warning-soft-subtle)",
            border: "1px solid var(--warning-border)",
          }}
        >
          <span
            className="shrink-0 mt-0.5"
            style={{ color: "var(--warning-strong)" }}
          >
            &#9888;
          </span>
          <span style={{ color: "var(--warning)" }}>{result.warning}</span>
        </div>
      )}

      {diagnosticsErrors.length > 0 && (
        <div
          role="alert"
          aria-label="Diagnostic issues"
          className="w-full px-3 py-2 rounded-lg text-xs"
          style={{
            background: "var(--warning-soft-subtle)",
            border: "1px solid var(--warning-border)",
          }}
        >
          <div className="flex items-center gap-2">
            <span className="shrink-0" style={{ color: "var(--warning-strong)" }}>
              &#9888;
            </span>
            <span className="font-semibold" style={{ color: "var(--warning)" }}>
              Diagnostics Issues
            </span>
          </div>
          <div className="mt-2 space-y-2">
            {diagnosticsErrors.map((diagnosticError, index) => (
              <div
                key={`${diagnosticError.diagnostic}-${index}`}
                className="grid gap-1"
                style={{ color: "var(--text-secondary)" }}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className="font-semibold"
                    style={{ color: "var(--text-primary)" }}
                  >
                    {formatDiagnosticLabel(diagnosticError.diagnostic)}
                  </span>
                  <span
                    className="font-mono text-[10px]"
                    style={{ color: "var(--text-muted)" }}
                  >
                    {diagnosticError.diagnostic}
                  </span>
                  <span
                    className="font-mono text-[10px] px-1.5 py-0.5 rounded"
                    style={{
                      color: "var(--warning)",
                      background: "var(--bg-input)",
                      border: "1px solid var(--warning-border)",
                    }}
                  >
                    {diagnosticError.error_type}
                  </span>
                </div>
                <div
                  className="break-words whitespace-pre-wrap"
                  style={{ color: "var(--warning)" }}
                >
                  {diagnosticError.error}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="min-w-[220px]">
        <label
          className="text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-muted)" }}
        >
          Model Info
        </label>
        <div className="mt-1 space-y-0.5">
          {([
            ["Model path", result.model_path],
            ["Development rows", result.development_rows.toLocaleString()],
            ...(result.final_test_rows > 0
              ? [["Final test rows", result.final_test_rows.toLocaleString()]]
              : []),
            ["Features", String(featuresCount)],
            ["Cat features", String(catFeaturesCount)],
            ...(result.best_iteration != null
              ? [["Best iteration", String(result.best_iteration)]]
              : []),
            ["Diagnostics on", diagnosticsLabel],
            ...(evaluation
              ? [
                  ["Data structure", evaluation.strategy],
                  [
                    "Candidate validation",
                    validationLabel(
                      evaluation.validation_method,
                      evaluation.validation_fit_count,
                    ),
                  ],
                  ["Total fits", evaluation.fit_count.toLocaleString()],
                ]
              : []),
          ] as const).map(([label, value]) => (
            <div key={label} className="flex justify-between text-xs font-mono gap-4">
              <span style={{ color: "var(--text-secondary)" }}>{label}</span>
              <span
                className="text-right truncate"
                style={{ color: "var(--text-primary)", maxWidth: 220 }}
              >
                {value}
              </span>
            </div>
          ))}
        </div>
      </div>

      <MetricsList label="Final-test metrics" metrics={result.final_test_metrics} />
      <MetricsList
        label={`${diagnosticsLabel} diagnostics`}
        metrics={result.diagnostic_metrics}
      />

      {result.glm_fit_statistics &&
        Object.keys(result.glm_fit_statistics).length > 0 && (
          <MetricsList label="Fit statistics" metrics={result.glm_fit_statistics} />
        )}

      {result.glm_regularization_path &&
        (result.glm_regularization_path.selected_alpha != null ||
          result.glm_regularization_path.n_nonzero != null) && (
          <div className="min-w-[160px]">
            <label
              className="text-[11px] font-bold uppercase tracking-[0.08em]"
              style={{ color: "var(--text-muted)" }}
            >
              Regularization
            </label>
            <div className="mt-1 space-y-0.5">
              {result.glm_regularization_path.selected_alpha != null && (
                <div className="flex justify-between text-xs font-mono gap-4">
                  <span style={{ color: "var(--text-secondary)" }}>Alpha</span>
                  <span style={{ color: "var(--text-primary)" }}>
                    {result.glm_regularization_path.selected_alpha.toFixed(6)}
                  </span>
                </div>
              )}
              {result.glm_regularization_path.n_nonzero != null && (
                <div className="flex justify-between text-xs font-mono gap-4">
                  <span style={{ color: "var(--text-secondary)" }}>Non-zero</span>
                  <span style={{ color: "var(--text-primary)" }}>
                    {result.glm_regularization_path.n_nonzero}
                  </span>
                </div>
              )}
            </div>
          </div>
        )}

      {evaluation && evaluation.validation_method !== "none" && (
        <section
          aria-label="Candidate selection results"
          className="w-full min-w-0 space-y-3"
        >
          <div>
            <h3
              className="text-[11px] font-bold uppercase tracking-[0.08em]"
              style={{ color: "var(--text-muted)" }}
            >
              Candidate selection
            </h3>
            <p className="mt-0.5 text-xs" style={{ color: "var(--text-secondary)" }}>
              {validationLabel(
                evaluation.validation_method,
                evaluation.validation_fit_count,
              )}{" "}
              · {evaluation.validation_fit_count} selection fits
            </p>
          </div>

          <SelectionMetricsTable metrics={evaluation.selection_metrics} />

          <div className="overflow-x-auto">
            <table
              aria-label="Selection fit metrics"
              className="w-full text-xs font-mono"
            >
              <thead>
                <tr style={{ color: "var(--text-muted)" }}>
                  <th className="py-1 pr-3 text-left font-medium">Fit</th>
                  <th className="px-2 py-1 text-right font-medium">Development rows</th>
                  <th className="px-2 py-1 text-right font-medium">Validation rows</th>
                  {selectionMetricNames.map(name => (
                    <th key={name} className="pl-2 py-1 text-right font-medium">
                      {name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {evaluation.selection_fits.map(fit => (
                  <tr key={fit.fit_index} style={{ color: "var(--text-primary)" }}>
                    <th className="py-1 pr-3 text-left font-medium">
                      {fit.fit_index + 1}
                    </th>
                    <td className="px-2 py-1 text-right">
                      {fit.train_rows.toLocaleString()}
                    </td>
                    <td className="px-2 py-1 text-right">
                      {fit.validation_rows.toLocaleString()}
                    </td>
                    {selectionMetricNames.map(name => (
                      <td key={name} className="pl-2 py-1 text-right">
                        {formatNumber(fit.metrics[name])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {tuning && (
        <section
          aria-label="Tuning results"
          className="w-full min-w-0 space-y-3"
        >
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h3
                className="text-[11px] font-bold uppercase tracking-[0.08em]"
                style={{ color: "var(--text-muted)" }}
              >
                Tuning
              </h3>
              <p className="mt-0.5 text-xs" style={{ color: "var(--text-secondary)" }}>
                Winning {tuning.metric}: {formatNumber(tuning.winner_objective)}
                {" "}· baseline {formatNumber(tuning.baseline_objective)}
                {" "}· improvement {formatNumber(tuning.improvement)}
              </p>
              <p className="mt-0.5 text-xs" style={{ color: "var(--text-secondary)" }}>
                {tuning.total_fit_count.toLocaleString()} total fits
                {completedElapsedSeconds !== null && (
                  <> · {completedElapsedSeconds.toFixed(1)}s elapsed</>
                )}
                {" "}· final tree count{" "}
                {tuning.final_tree_count.toLocaleString()}
              </p>
            </div>
            {onUseBestParameters && (
              <button
                type="button"
                className="px-3 py-1.5 rounded text-xs font-medium"
                style={{
                  color: "var(--text-primary)",
                  background: "var(--bg-input)",
                  border: "1px solid var(--border)",
                }}
                onClick={() => onUseBestParameters(tuning.final_params)}
              >
                Use best as fixed parameters
              </button>
            )}
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            <div className="min-w-0">
              <h4
                className="text-[11px] font-semibold uppercase tracking-[0.06em]"
                style={{ color: "var(--text-muted)" }}
              >
                Best sampled parameters
              </h4>
              <pre
                className="mt-1 overflow-x-auto rounded p-2 text-[11px]"
                style={{
                  color: "var(--text-primary)",
                  background: "var(--bg-input)",
                }}
              >
                {JSON.stringify(tuning.best_sampled_params, null, 2)}
              </pre>
            </div>
            <div className="min-w-0">
              <h4
                className="text-[11px] font-semibold uppercase tracking-[0.06em]"
                style={{ color: "var(--text-muted)" }}
              >
                Final parameters
              </h4>
              <pre
                className="mt-1 overflow-x-auto rounded p-2 text-[11px]"
                style={{
                  color: "var(--text-primary)",
                  background: "var(--bg-input)",
                }}
              >
                {JSON.stringify(tuning.final_params, null, 2)}
              </pre>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table aria-label="Top tuning trials" className="w-full text-xs font-mono">
              <thead>
                <tr style={{ color: "var(--text-muted)" }}>
                  <th className="py-1 pr-3 text-left font-medium">Rank</th>
                  <th className="px-2 py-1 text-right font-medium">Trial</th>
                  <th className="px-2 py-1 text-left font-medium">Type</th>
                  <th className="px-2 py-1 text-right font-medium">{tuning.metric}</th>
                </tr>
              </thead>
              <tbody>
                {sortedTopTrials(tuning).map((trial, index) => (
                  <tr key={trial.trial_index} style={{ color: "var(--text-primary)" }}>
                    <th className="py-1 pr-3 text-left font-medium">{index + 1}</th>
                    <td className="px-2 py-1 text-right">{trial.trial_index}</td>
                    <td className="px-2 py-1 text-left">{trial.label}</td>
                    <td className="px-2 py-1 text-right">
                      {formatNumber(trial.objective)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {mlflowBackend?.installed && jobId && (
        <div className="min-w-[200px]">
          <MlflowExportSection
            trainJobId={jobId}
            mlflowBackend={mlflowBackend}
            config={config}
          />
        </div>
      )}
    </div>
  )
}
