/**
 * Summary tab for the ModellingPreview panel.
 *
 * Shows model info grid, metrics table, CV results, warning banner,
 * and MLflow export button.
 */
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { MlflowExportSection } from "./MlflowExportSection"

interface SummaryTabProps {
  result: TrainResult
  jobId: string
  mlflowBackend: { installed: boolean; backend: string; host: string } | null
  config: Record<string, unknown>
}

export function SummaryTab({ result, jobId, mlflowBackend, config }: SummaryTabProps) {
  const fmt = (v: unknown) => typeof v === 'number' && Number.isFinite(v) ? v.toFixed(4) : 'N/A'
  const featuresCount = result.features?.length ?? result.feature_importance.length
  const catFeaturesCount = result.cat_features?.length ?? 0
  const diagSet = result.diagnostics_set ?? "validation"
  const diagLabel = diagSet === "holdout" ? "Holdout" : diagSet === "train" ? "Train" : "Validation"

  return (
    <div className="flex gap-6 flex-wrap">
      {/* Warning banner */}
      {result.warning && (
        <div className="w-full flex items-start gap-2 px-3 py-2 rounded-lg text-xs" style={{ background: "rgba(245,158,11,.06)", border: "1px solid rgba(245,158,11,.2)" }}>
          <span className="shrink-0 mt-0.5" style={{ color: "#f59e0b" }}>&#9888;</span>
          <span style={{ color: "#fbbf24" }}>{result.warning}</span>
        </div>
      )}

      {/* Model info grid */}
      <div className="min-w-[200px]">
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
          Model Info
        </label>
        <div className="mt-1 space-y-0.5">
          {([
            ["Model path", result.model_path],
            ["Train rows", result.train_rows.toLocaleString()],
            ...(result.test_rows > 0 ? [["Validation rows", result.test_rows.toLocaleString()]] : []),
            ...(result.holdout_rows && result.holdout_rows > 0 ? [["Holdout rows", result.holdout_rows.toLocaleString()]] : []),
            ["Features", String(featuresCount)],
            ["Cat features", String(catFeaturesCount)],
            ...(result.best_iteration != null ? [["Best iteration", String(result.best_iteration)]] : []),
            ["Diagnostics on", diagLabel],
          ] as const).map(([label, value]) => (
            <div key={label} className="flex justify-between text-xs font-mono gap-4">
              <span style={{ color: "var(--text-secondary)" }}>{label}</span>
              <span className="text-right truncate" style={{ color: "var(--text-primary)", maxWidth: 200 }}>{value}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Metrics table — primary (from diagnostics set) */}
      {Object.keys(result.metrics).length > 0 && (
        <div className="min-w-[180px]">
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
            Metrics ({diagLabel})
          </label>
          <div className="mt-1 space-y-0.5">
            {Object.entries(result.metrics).map(([k, v]) => (
              <div key={k} className="flex justify-between text-xs font-mono gap-4">
                <span style={{ color: "var(--text-secondary)" }}>{k}</span>
                <span style={{ color: "var(--text-primary)" }}>{fmt(v)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Holdout metrics (shown separately when holdout exists and diagnostics are on validation) */}
      {result.holdout_metrics && Object.keys(result.holdout_metrics).length > 0 && diagSet !== "holdout" && (
        <div className="min-w-[180px]">
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
            Metrics (Holdout)
          </label>
          <div className="mt-1 space-y-0.5">
            {Object.entries(result.holdout_metrics).map(([k, v]) => (
              <div key={k} className="flex justify-between text-xs font-mono gap-4">
                <span style={{ color: "var(--text-secondary)" }}>{k}</span>
                <span style={{ color: "var(--text-primary)" }}>{fmt(v)}</span>
              </div>
            ))}
          </div>
        </div>
      )}


      {/* GLM fit statistics */}
      {result.glm_fit_statistics && Object.keys(result.glm_fit_statistics).length > 0 && (
        <div className="min-w-[180px]">
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
            Fit Statistics
          </label>
          <div className="mt-1 space-y-0.5">
            {Object.entries(result.glm_fit_statistics).map(([k, v]) => (
              <div key={k} className="flex justify-between text-xs font-mono gap-4">
                <span style={{ color: "var(--text-secondary)" }}>{k}</span>
                <span style={{ color: "var(--text-primary)" }}>{fmt(v)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* GLM regularization info */}
      {result.glm_regularization_path && (result.glm_regularization_path.selected_alpha != null || result.glm_regularization_path.n_nonzero != null) && (
        <div className="min-w-[160px]">
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
            Regularization
          </label>
          <div className="mt-1 space-y-0.5">
            {result.glm_regularization_path.selected_alpha != null && (
              <div className="flex justify-between text-xs font-mono gap-4">
                <span style={{ color: "var(--text-secondary)" }}>Alpha</span>
                <span style={{ color: "var(--text-primary)" }}>{typeof result.glm_regularization_path.selected_alpha === 'number' && Number.isFinite(result.glm_regularization_path.selected_alpha) ? result.glm_regularization_path.selected_alpha.toFixed(6) : 'N/A'}</span>
              </div>
            )}
            {result.glm_regularization_path.n_nonzero != null && (
              <div className="flex justify-between text-xs font-mono gap-4">
                <span style={{ color: "var(--text-secondary)" }}>Non-zero</span>
                <span style={{ color: "var(--text-primary)" }}>{result.glm_regularization_path.n_nonzero}</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* MLflow export */}
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
