/**
 * Feature importance display with type switcher (prediction / loss / SHAP).
 * Extracted from ModellingConfig.tsx for readability.
 */
import { useState } from "react"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { MODEL_COLORS } from "../../theme/colors"

type FeatureImportanceProps = {
  trainResult: TrainResult
}

export function FeatureImportance({ trainResult }: FeatureImportanceProps) {
  const [importanceType, setImportanceType] = useState<"prediction" | "loss" | "shap">("prediction")

  if (trainResult.feature_importance.length === 0) return null

  const types: { key: "prediction" | "loss" | "shap"; label: string }[] = [
    { key: "prediction", label: "Prediction" },
    ...(trainResult.feature_importance_loss?.length ? [{ key: "loss" as const, label: "Loss" }] : []),
    ...(trainResult.shap_summary?.length ? [{ key: "shap" as const, label: "SHAP" }] : []),
  ]
  const items = importanceType === "shap"
    ? (trainResult.shap_summary || []).slice(0, 10).map(s => ({ feature: s.feature, importance: s.mean_abs_shap }))
    : importanceType === "loss"
      ? (trainResult.feature_importance_loss || []).slice(0, 10)
      : trainResult.feature_importance.slice(0, 10)
  const maxVal = items[0]?.importance || 1

  return (
    <div>
      <div className="flex items-center gap-2 mb-1">
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Top Features</label>
        {types.length > 1 && (
          <div className="flex gap-1">
            {types.map(t => (
              <button
                key={t.key}
                onClick={() => setImportanceType(t.key)}
                className="px-1.5 py-0.5 rounded text-[10px]"
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
      </div>
      <div className="space-y-0.5">
        {items.map((fi, i) => (
          <div key={i} className="flex items-center gap-2 text-xs font-mono">
            <span className="truncate flex-1" style={{ color: "var(--text-secondary)" }}>{fi.feature}</span>
            <div className="w-20 h-1.5 rounded-full overflow-hidden" style={{ background: "var(--chrome-hover)" }}>
              <div
                className="h-full rounded-full"
                style={{
                  width: `${(Math.abs(fi.importance) / Math.abs(maxVal)) * 100}%`,
                  background: MODEL_COLORS.accent,
                }}
              />
            </div>
            <span className="w-12 text-right" style={{ color: "var(--text-muted)" }}>{fi.importance.toFixed(1)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
