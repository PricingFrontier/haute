/**
 * Full feature importance tab for the ModellingPreview panel.
 *
 * Unlike the existing FeatureImportance.tsx (which caps at top 10 and is
 * designed for the sidebar), this displays ALL features with a full-width
 * horizontal bar chart and a type switcher.
 */
import { useState, useMemo } from "react"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { MODEL_COLORS } from "../../theme/colors"

interface FeaturesTabProps {
  result: TrainResult
}

const BAR_COLOR = MODEL_COLORS.accent

export function FeaturesTab({ result }: FeaturesTabProps) {
  const [importanceType, setImportanceType] = useState<"prediction" | "loss" | "shap">("prediction")

  const types: { key: "prediction" | "loss" | "shap"; label: string }[] = useMemo(() => [
    { key: "prediction", label: "Prediction" },
    ...(result.feature_importance_loss?.length ? [{ key: "loss" as const, label: "Loss" }] : []),
    ...(result.shap_summary?.length ? [{ key: "shap" as const, label: "SHAP" }] : []),
  ], [result.feature_importance_loss, result.shap_summary])

  // All features (no cap)
  const items = useMemo(() => {
    if (importanceType === "shap") {
      return (result.shap_summary || []).map(s => ({ feature: s.feature, importance: s.mean_abs_shap }))
    }
    if (importanceType === "loss") {
      return result.feature_importance_loss || []
    }
    return result.feature_importance
  }, [importanceType, result])

  if (items.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-xs" style={{ color: "var(--text-muted)" }}>
        No feature importance data available
      </div>
    )
  }

  const maxVal = items.map(i => Math.abs(i.importance)).reduce((a, b) => Math.max(a, b), -Infinity)

  return (
    <div className="space-y-2">
      {/* Type switcher */}
      {types.length > 1 && (
        <div className="flex gap-1">
          {types.map(t => (
            <button
              key={t.key}
              onClick={() => setImportanceType(t.key)}
              className="px-2 py-0.5 rounded text-[10px] font-medium"
              style={{
                background: importanceType === t.key ? "var(--accent-soft)" : "var(--chrome-hover)",
                color: importanceType === t.key ? "var(--accent)" : "var(--text-muted)",
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}

      {/* Feature bars - scrollable */}
      <div className="overflow-y-auto" style={{ maxHeight: 400 }}>
        <div className="space-y-0.5">
          {items.map((fi, i) => (
            <div key={`${fi.feature}-${i}`} className="flex items-center gap-2 text-xs font-mono group">
              {/* Rank */}
              <span className="w-5 text-right shrink-0 text-[10px]" style={{ color: "var(--text-muted)" }}>
                {i + 1}
              </span>
              {/* Feature name */}
              <span
                className="truncate shrink-0"
                style={{ color: "var(--text-secondary)", width: 140 }}
                title={fi.feature}
              >
                {fi.feature}
              </span>
              {/* Bar */}
              <div className="flex-1 h-2 rounded-full overflow-hidden" style={{ background: "var(--chrome-hover)" }}>
                <div
                  className="h-full rounded-full"
                  style={{
                    width: maxVal > 0 ? `${(Math.abs(fi.importance) / maxVal) * 100}%` : "0%",
                    background: BAR_COLOR,
                    opacity: 0.7,
                  }}
                />
              </div>
              {/* Value */}
              <span className="w-14 text-right shrink-0" style={{ color: "var(--text-muted)" }}>
                {fi.importance.toFixed(1)}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="text-[10px]" style={{ color: "var(--text-muted)" }}>
        {items.length} feature{items.length !== 1 ? "s" : ""}
      </div>
    </div>
  )
}
