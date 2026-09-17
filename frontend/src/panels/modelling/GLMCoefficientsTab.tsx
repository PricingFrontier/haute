/**
 * Coefficients table for GLM results.
 *
 * Sortable table showing each term's coefficient, standard error, z-value,
 * p-value, and significance stars. When RustyStats marks inference invalid
 * (penalties, constraints, smoothing) the statistic columns show dashes, the
 * reason is shown above the table, and terms keep their design order.
 */
import { useState, useMemo } from "react"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { formatFixed } from "../../utils/formatValue"

interface GLMCoefficientsTabProps {
  result: TrainResult
}

type SortKey = "feature" | "coefficient" | "std_error" | "z_value" | "p_value"
type SortDir = "asc" | "desc"

const STATISTIC_KEYS: ReadonlySet<SortKey> = new Set(["std_error", "z_value", "p_value"])
const DASH = "–"

const SIGNIF_COLORS: Record<string, string> = {
  "***": "var(--signif-high)",
  "**": "var(--signif-med)",
  "*": "var(--signif-low)",
  ".": "var(--signif-marginal)",
  "": "var(--text-muted)",
}

function pColor(p: number | null): string {
  if (p === null || !Number.isFinite(p)) return "var(--text-muted)"
  if (p < 0.001) return "var(--signif-high)"
  if (p < 0.01) return "var(--signif-med)"
  if (p < 0.05) return "var(--signif-low)"
  if (p < 0.1) return "var(--signif-marginal)"
  return "var(--text-muted)"
}

function formatStatistic(value: number | null, digits: number): string {
  return value === null ? DASH : formatFixed(value, digits)
}

function formatPValue(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return DASH
  return value < 0.0001 ? value.toExponential(2) : value.toFixed(4)
}

export function GLMCoefficientsTab({ result }: GLMCoefficientsTabProps) {
  const rows = result.glm_coefficients
  const inference = result.glm_inference
  const valid = inference?.valid ?? rows.every((row) => row.std_error !== null)
  const robust = valid && inference?.standard_errors && inference.standard_errors !== "model"
    ? inference.standard_errors
    : null
  // null keeps the design order; valid inference starts in p-value order.
  const [chosenSort, setChosenSort] = useState<{ key: SortKey; dir: SortDir } | null>(null)
  const sort = chosenSort && (valid || !STATISTIC_KEYS.has(chosenSort.key))
    ? chosenSort
    : valid ? { key: "p_value" as const, dir: "asc" as const } : null

  const sortKey = sort?.key ?? null
  const sortDir = sort?.dir ?? "asc"
  const sorted = useMemo(() => {
    if (sortKey === null) return rows
    return [...rows].sort((a, b) => {
      const av = a[sortKey]
      const bv = b[sortKey]
      if (typeof av === "string" && typeof bv === "string") {
        return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av)
      }
      const na = Number(av)
      const nb = Number(bv)
      return sortDir === "asc" ? na - nb : nb - na
    })
  }, [rows, sortKey, sortDir])

  if (rows.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-xs" style={{ color: "var(--text-muted)" }}>
        No coefficient data available
      </div>
    )
  }

  const sortable = (key: SortKey) => valid || !STATISTIC_KEYS.has(key)
  const handleSort = (key: SortKey) => {
    if (!sortable(key)) return
    setChosenSort(sort?.key === key ? { key, dir: sort.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" })
  }
  const sortIndicator = (key: SortKey) =>
    sort?.key === key ? (sort.dir === "asc" ? " ▲" : " ▼") : ""

  const columns: { key: SortKey; label: string; align: string }[] = [
    { key: "feature", label: "Term", align: "text-left" },
    { key: "coefficient", label: "Estimate", align: "text-right" },
    { key: "std_error", label: robust ? `Robust SE (${robust})` : "Std. Error", align: "text-right" },
    { key: "z_value", label: "z", align: "text-right" },
    { key: "p_value", label: "Pr(>|z|)", align: "text-right" },
  ]

  return (
    <div className="space-y-2">
      {!valid && (
        <p role="note" className="rounded-lg px-2.5 py-1.5 text-[11px]" style={{ background: "var(--warning-soft-subtle)", border: "1px solid var(--warning-border)", color: "var(--text-primary)" }}>
          {inference?.reason ?? "Standard errors and p-values are not available for this model."}
        </p>
      )}
      {robust && (
        <p className="text-[11px]" style={{ color: "var(--text-secondary)" }}>
          Standard errors, z-values, and p-values are heteroskedasticity-robust ({robust}).
        </p>
      )}
      <div className="overflow-auto" style={{ maxHeight: 480 }}>
        <table className="w-full text-xs font-mono" style={{ borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              {columns.map(col => (
                <th
                  key={col.key}
                  onClick={() => handleSort(col.key)}
                  aria-sort={sort?.key === col.key ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
                  className={`px-2 py-1.5 font-semibold select-none ${sortable(col.key) ? "cursor-pointer" : ""} ${col.align}`}
                  style={{ color: "var(--text-muted)", whiteSpace: "nowrap" }}
                >
                  {col.label}{sortIndicator(col.key)}
                </th>
              ))}
              <th className="px-2 py-1.5 text-center font-semibold" style={{ color: "var(--text-muted)" }}>Sig.</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((row, i) => (
              <tr
                key={row.feature}
                style={{
                  borderBottom: "1px solid var(--border)",
                  background: i % 2 === 0 ? "transparent" : "rgba(255,255,255,.02)",
                }}
              >
                <td className="px-2 py-1 text-left truncate" style={{ color: "var(--text-primary)", maxWidth: 200 }} title={row.feature}>
                  {row.feature}
                </td>
                <td className="px-2 py-1 text-right" style={{ color: "var(--text-primary)" }}>
                  {formatFixed(row.coefficient, 6)}
                </td>
                <td className="px-2 py-1 text-right" style={{ color: "var(--text-secondary)" }}>
                  {formatStatistic(row.std_error, 6)}
                </td>
                <td className="px-2 py-1 text-right" style={{ color: "var(--text-secondary)" }}>
                  {formatStatistic(row.z_value, 3)}
                </td>
                <td className="px-2 py-1 text-right" style={{ color: pColor(row.p_value) }}>
                  {formatPValue(row.p_value)}
                </td>
                <td className="px-2 py-1 text-center font-bold" style={{ color: SIGNIF_COLORS[row.significance ?? ""] || "var(--text-muted)" }}>
                  {row.significance === null ? DASH : row.significance}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap gap-3 text-[10px]" style={{ color: "var(--text-muted)" }}>
        {valid && (
          <>
            <span>Signif. codes:</span>
            <span style={{ color: "var(--signif-high)" }}>*** &lt; 0.001</span>
            <span style={{ color: "var(--signif-med)" }}>** &lt; 0.01</span>
            <span style={{ color: "var(--signif-low)" }}>* &lt; 0.05</span>
            <span style={{ color: "var(--signif-marginal)" }}>. &lt; 0.1</span>
          </>
        )}
        <span>{rows.length} term{rows.length !== 1 ? "s" : ""}</span>
      </div>
    </div>
  )
}
