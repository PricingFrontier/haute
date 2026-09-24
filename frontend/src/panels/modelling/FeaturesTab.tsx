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
import { formatChartNumber } from "../../utils/chartHelpers"

interface FeaturesTabProps {
  result: TrainResult
}

type ImportanceType = "prediction" | "loss" | "shap"

const descriptions: Record<ImportanceType, string> = {
  prediction: "Relative feature importance reported by the fitted model.",
  loss: "Signed loss-based feature importance. Features are ranked by absolute magnitude.",
  shap: "SHAP importance is mean absolute SHAP value.",
}

export function FeaturesTab({ result }: FeaturesTabProps) {
  const [importanceType, setImportanceType] = useState<ImportanceType>("prediction")
  const [search, setSearch] = useState("")
  const [shown, setShown] = useState<"20" | "all">("20")

  const types: { key: "prediction" | "loss" | "shap"; label: string }[] = useMemo(
    () => [
      { key: "prediction", label: "Prediction" },
      ...(result.feature_importance_loss?.length ? [{ key: "loss" as const, label: "Loss" }] : []),
      ...(result.shap_summary?.length ? [{ key: "shap" as const, label: "SHAP" }] : []),
    ],
    [result.feature_importance_loss, result.shap_summary],
  )

  const allItems = useMemo(() => {
    if (importanceType === "shap") {
      return (result.shap_summary || []).map((s) => ({
        feature: s.feature,
        importance: s.mean_abs_shap,
      }))
    }
    if (importanceType === "loss") {
      return result.feature_importance_loss || []
    }
    return result.feature_importance
  }, [importanceType, result])
  const filtered = useMemo(
    () =>
      [...allItems]
        .filter((item) => item.feature.toLocaleLowerCase().includes(search.toLocaleLowerCase()))
        .sort(
          (a, b) =>
            Math.abs(b.importance) - Math.abs(a.importance) || a.feature.localeCompare(b.feature),
        ),
    [allItems, search],
  )
  const items = shown === "20" ? filtered.slice(0, 20) : filtered

  if (allItems.length === 0) {
    return (
      <div
        className="flex items-center justify-center h-full text-xs"
        style={{ color: "var(--text-muted)" }}
      >
        No feature importance data available
      </div>
    )
  }

  const maxVal = Math.max(...allItems.map((i) => Math.abs(i.importance)), 0)
  const hasNegative = allItems.some((item) => item.importance < 0)

  return (
    <section className="space-y-3" aria-label="Feature importance">
      <div className="flex flex-wrap items-center gap-2">
        {types.length > 1 && (
          <div className="flex gap-1">
            {types.map((t) => (
              <button
                key={t.key}
                type="button"
                aria-pressed={importanceType === t.key}
                onClick={() => setImportanceType(t.key)}
                className="focus-ring px-2 py-1 rounded text-[13px] font-medium"
                style={{
                  background:
                    importanceType === t.key ? MODEL_COLORS.accentSoft : "var(--chrome-hover)",
                  color: importanceType === t.key ? MODEL_COLORS.accent : "var(--text-muted)",
                }}
              >
                {t.label}
              </button>
            ))}
          </div>
        )}
        <label className="ml-auto text-[12px]" style={{ color: "var(--text-secondary)" }}>
          Features shown
          <select
            aria-label="Features shown"
            value={shown}
            onChange={(event) => setShown(event.target.value as "20" | "all")}
            className="ml-1 rounded border px-1 py-0.5 text-[13px]"
            style={{
              background: "var(--bg-input)",
              borderColor: "var(--border)",
              color: "var(--text-primary)",
            }}
          >
            <option value="20">Top 20</option>
            <option value="all">All</option>
          </select>
        </label>
      </div>
      <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>
        {descriptions[importanceType]}
      </p>
      <label className="block text-[13px]" style={{ color: "var(--text-secondary)" }}>
        Search importance features
        <input
          aria-label="Search importance features"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="mt-1 w-full rounded border px-2 py-1 text-[13px]"
          style={{
            background: "var(--bg-input)",
            borderColor: "var(--border)",
            color: "var(--text-primary)",
          }}
        />
      </label>
      <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>
        {filtered.length} matching feature{filtered.length === 1 ? "" : "s"}
        {shown === "20" && filtered.length > 20 ? "; showing top 20" : ""}
      </p>

      {filtered.length === 0 ? (
        <p role="status" className="text-[13px]" style={{ color: "var(--text-muted)" }}>
          No features match your search.
        </p>
      ) : (
        <div>
          <div className="space-y-0.5">
            {items.map((fi, i) => (
              <div
                key={`${fi.feature}-${i}`}
                className="grid items-center text-[13px]"
                style={{
                  gridTemplateColumns: "24px minmax(0, 1.2fr) minmax(48px, 2fr) 56px",
                  gap: 8,
                }}
              >
                <span
                  className="text-right text-[12px] tabular-nums"
                  style={{ color: "var(--text-muted)" }}
                >
                  {i + 1}
                </span>
                <span
                  className="break-words"
                  style={{ color: "var(--text-primary)", overflowWrap: "anywhere" }}
                >
                  {fi.feature}
                </span>
                <div
                  className="relative h-3"
                  aria-label={`${fi.feature}: ${fi.importance >= 0 && hasNegative ? "+" : ""}${formatImportance(fi.importance)}`}
                >
                  {hasNegative && (
                    <span
                      className="absolute inset-y-0 left-1/2 border-l"
                      style={{ borderColor: "var(--text-muted)" }}
                    />
                  )}
                  <div
                    className="absolute top-0 h-full rounded"
                    style={{
                      width:
                        maxVal > 0
                          ? `${hasNegative ? (Math.abs(fi.importance) / maxVal) * 50 : (Math.abs(fi.importance) / maxVal) * 100}%`
                          : "0%",
                      ...(hasNegative
                        ? fi.importance < 0
                          ? { right: "50%" }
                          : { left: "50%" }
                        : { left: 0 }),
                      background: fi.importance < 0 ? "var(--text-secondary)" : MODEL_COLORS.accent,
                    }}
                  />
                </div>
                <span
                  className="text-right tabular-nums"
                  style={{ color: "var(--text-secondary)" }}
                >
                  {fi.importance >= 0 && hasNegative ? "+" : ""}
                  {formatImportance(fi.importance)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

function formatImportance(value: number): string {
  return value !== 0 && Math.abs(value) < 0.1 ? formatChartNumber(value) : value.toFixed(1)
}
