/**
 * Summary tab for completed model training results.
 *
 * Final-test performance is deliberately separated from validation metrics
 * used for model or parameter selection.
 */
import { useId, type ReactNode } from "react"
import { Activity, ChartNoAxesCombined, Database, FlaskConical, SlidersHorizontal, Target, type LucideIcon } from "lucide-react"
import type {
  EvaluationMetricSummary,
  TuningReport,
} from "../../api/types"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { MODEL_COLORS } from "../../theme/colors"
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

const CARD_GRID_STYLE = {
  gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 280px), 1fr))",
}

function SummaryCard({
  title,
  icon: Icon,
  description,
  children,
  ariaLabel,
}: {
  title: string
  icon: LucideIcon
  description?: string
  children: ReactNode
  ariaLabel?: string
}) {
  const headingId = useId()
  return (
    <section
      aria-label={ariaLabel}
      aria-labelledby={ariaLabel ? undefined : headingId}
      className="min-w-0 rounded-lg p-3 space-y-3 [&_table]:tabular-nums [&_thead]:bg-[var(--bg-input)] [&_tbody_tr]:border-b [&_tbody_tr]:border-[var(--border)] [&_tbody_tr:last-child]:border-0 [&_th]:py-2 [&_td]:py-2"
      style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
    >
      <div>
        <h3 id={headingId} className="flex items-center gap-1.5 text-[11px] font-bold" style={{ color: MODEL_COLORS.accent }}>
          <Icon size={14} className="shrink-0" aria-hidden="true" />
          {title}
        </h3>
        {description && (
          <p className="mt-1 text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
            {description}
          </p>
        )}
      </div>
      {children}
    </section>
  )
}

function MetricsList({
  label,
  metrics,
  description,
  icon,
}: {
  label: string
  metrics: Record<string, number>
  description: string
  icon: LucideIcon
}) {
  if (Object.keys(metrics).length === 0) return null

  return (
    <SummaryCard title={label} description={description} icon={icon}>
      <dl className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 120px), 1fr))" }}>
        {Object.entries(metrics).map(([name, value]) => (
          <div key={name} className="min-w-0">
            <dt className="break-words text-[11px]" style={{ color: "var(--text-muted)" }}>{name}</dt>
            <dd className="mt-0.5 break-all text-xl font-semibold tabular-nums tracking-tight" style={{ color: "var(--text-primary)" }}>{formatNumber(value)}</dd>
          </div>
        ))}
      </dl>
    </SummaryCard>
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
    <div className="space-y-3">
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

      {result.final_test_rows === 0 && (
        <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
          No final test was reserved for this run.
        </p>
      )}

      <div className="grid gap-3" style={CARD_GRID_STYLE}>
        <MetricsList
          label="Final-test metrics"
          metrics={result.final_test_metrics}
          description="Performance on the untouched final test."
          icon={Target}
        />
        <MetricsList
          label={`${diagnosticsLabel} diagnostics`}
          metrics={result.diagnostic_metrics}
          description={result.diagnostics_set === "final_test"
            ? "Diagnostic measures evaluated on the final test."
            : "Diagnostics on development data, not held-out performance."}
          icon={Activity}
        />
        <MetricsList
          label="Fit statistics"
          metrics={result.glm_fit_statistics ?? {}}
          description="Statistics describing the fitted GLM."
          icon={ChartNoAxesCombined}
        />
      </div>

      <div className="grid gap-3" style={CARD_GRID_STYLE}>
        <SummaryCard title="Model Info" icon={Database}>
          <dl className="grid gap-x-6 gap-y-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 150px), 1fr))" }}>
            {([
              ["Development rows", result.development_rows.toLocaleString()],
              ...(result.final_test_rows > 0
                ? [["Final test rows", result.final_test_rows.toLocaleString()]]
                : []),
              ["Features", String(featuresCount)],
              ["Categorical features", String(catFeaturesCount)],
              ...(result.best_iteration != null
                ? [["Best iteration", String(result.best_iteration)]]
                : []),
              ["Diagnostics on", diagnosticsLabel],
              ...(evaluation
                ? [
                    ["Data structure", { random: "Random rows", group: "Keep entities together", temporal: "Respect time order" }[evaluation.strategy]],
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
              <div key={label} className="min-w-0">
                <dt className="text-[11px]" style={{ color: "var(--text-muted)" }}>{label}</dt>
                <dd
                  className="mt-0.5 break-words text-xs font-medium tabular-nums"
                  style={{ color: "var(--text-primary)" }}
                >
                  {value}
                </dd>
              </div>
            ))}
          </dl>
          <div className="pt-2" style={{ borderTop: "1px solid var(--border)" }}>
            <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>Model path</div>
            <div className="mt-1 break-all font-mono text-[11px] select-text" title={result.model_path} style={{ color: "var(--text-primary)" }}>
              {result.model_path}
            </div>
          </div>
        </SummaryCard>

        {result.glm_regularization_path &&
          (result.glm_regularization_path.selected_alpha != null ||
            result.glm_regularization_path.n_nonzero != null) && (
            <SummaryCard title="Regularization" icon={SlidersHorizontal} description="Selected penalty and retained coefficients.">
              <dl className="space-y-2">
                {result.glm_regularization_path.selected_alpha != null && (
                  <div className="flex justify-between text-xs gap-4">
                    <dt style={{ color: "var(--text-muted)" }}>Alpha</dt>
                    <dd className="font-mono tabular-nums" style={{ color: "var(--text-primary)" }}>
                      {result.glm_regularization_path.selected_alpha.toFixed(6)}
                    </dd>
                  </div>
                )}
                {result.glm_regularization_path.n_nonzero != null && (
                  <div className="flex justify-between text-xs gap-4">
                    <dt style={{ color: "var(--text-muted)" }}>Non-zero coefficients</dt>
                    <dd className="font-mono tabular-nums" style={{ color: "var(--text-primary)" }}>
                      {result.glm_regularization_path.n_nonzero}
                    </dd>
                  </div>
                )}
              </dl>
            </SummaryCard>
          )}
      </div>

      {evaluation && evaluation.validation_method !== "none" && (
        <SummaryCard
          title="Candidate selection"
          ariaLabel="Candidate selection results"
          icon={ChartNoAxesCombined}
          description="Validation results used to select the model, separate from final-test performance."
        >
          <p className="text-xs" style={{ color: "var(--text-primary)" }}>
            {validationLabel(
              evaluation.validation_method,
              evaluation.validation_fit_count,
            )}{" "}
            · {evaluation.validation_fit_count} selection {evaluation.validation_fit_count === 1 ? "fit" : "fits"}
          </p>

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
        </SummaryCard>
      )}

      {tuning && (
        <SummaryCard
          title="Tuning"
          ariaLabel="Tuning results"
          icon={SlidersHorizontal}
          description="Compare the winning trial with the baseline and inspect the final parameters."
        >
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <p className="text-xs font-medium" style={{ color: "var(--text-primary)" }}>
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
                className="focus-ring px-3 py-1.5 rounded text-xs font-medium transition-colors hover:bg-[var(--bg-hover)]"
                style={{
                  color: MODEL_COLORS.accent,
                  border: "1px solid var(--border)",
                }}
                onClick={() => onUseBestParameters(tuning.final_params)}
              >
                Use best as fixed parameters
              </button>
            )}
          </div>

          <div className="grid gap-3" style={CARD_GRID_STYLE}>
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
        </SummaryCard>
      )}

      {mlflowBackend?.installed && jobId && (
        <SummaryCard title="Experiment tracking" icon={FlaskConical} description="Log this trained model and its results to MLflow.">
          <div className="max-w-sm">
            <MlflowExportSection
              trainJobId={jobId}
              mlflowBackend={mlflowBackend}
              config={config}
            />
          </div>
        </SummaryCard>
      )}
    </div>
  )
}
