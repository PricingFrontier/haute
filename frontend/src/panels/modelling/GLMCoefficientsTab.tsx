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
import { MODEL_COLORS } from "../../theme/colors"
import { formatFixed } from "../../utils/formatValue"

interface GLMCoefficientsTabProps {
  result: TrainResult
}

type SortKey = "feature" | "coefficient" | "std_error" | "z_value" | "p_value"
type SortDir = "asc" | "desc"

const STATISTIC_KEYS: ReadonlySet<SortKey> = new Set(["std_error", "z_value", "p_value"])
const DASH = "–"

const NEUTRAL_STATISTIC_COLOR = "var(--text-secondary)"

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
  const intervalAvailable =
    valid &&
    rows.some(
      (row) =>
        Number.isFinite(row.coefficient) &&
        Number.isFinite(row.std_error) &&
        (row.std_error ?? -1) >= 0,
    )
  const [search, setSearch] = useState("")
  const [view, setView] = useState<"table" | "interval">("table")
  const robust =
    valid && inference?.standard_errors && inference.standard_errors !== "model"
      ? inference.standard_errors
      : null
  // null keeps the design order; valid inference starts in p-value order.
  const [chosenSort, setChosenSort] = useState<{ key: SortKey; dir: SortDir } | null>(null)
  const sort =
    chosenSort && (valid || !STATISTIC_KEYS.has(chosenSort.key))
      ? chosenSort
      : valid
        ? { key: "p_value" as const, dir: "asc" as const }
        : null

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
  const visibleRows = useMemo(
    () =>
      sorted.filter((row) => row.feature.toLocaleLowerCase().includes(search.toLocaleLowerCase())),
    [search, sorted],
  )

  if (rows.length === 0) {
    return (
      <div
        className="flex items-center justify-center h-full text-xs"
        style={{ color: "var(--text-muted)" }}
      >
        No coefficient data available
      </div>
    )
  }

  const sortable = (key: SortKey) => valid || !STATISTIC_KEYS.has(key)
  const handleSort = (key: SortKey) => {
    if (!sortable(key)) return
    setChosenSort(
      sort?.key === key ? { key, dir: sort.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" },
    )
  }
  const sortIndicator = (key: SortKey) =>
    sort?.key === key ? (sort.dir === "asc" ? " ▲" : " ▼") : ""

  const columns: { key: SortKey; label: string; align: string }[] = [
    { key: "feature", label: "Term", align: "text-left" },
    { key: "coefficient", label: "Estimate", align: "text-right" },
    {
      key: "std_error",
      label: robust ? `Robust SE (${robust})` : "Std. Error",
      align: "text-right",
    },
    { key: "z_value", label: "z", align: "text-right" },
    { key: "p_value", label: "Pr(>|z|)", align: "text-right" },
  ]

  return (
    <div className="space-y-2">
      {!valid && (
        <p
          role="note"
          className="rounded-lg px-2.5 py-1.5 text-[12px]"
          style={{
            background: "var(--warning-soft-subtle)",
            border: "1px solid var(--warning-border)",
            color: "var(--text-primary)",
          }}
        >
          {inference?.reason ?? "Standard errors and p-values are not available for this model."}
        </p>
      )}
      {robust && (
        <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
          Standard errors, z-values, and p-values are heteroskedasticity-robust ({robust}).
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <label className="min-w-0 flex-1 text-[13px]" style={{ color: "var(--text-secondary)" }}>
          Search coefficient terms
          <input
            aria-label="Search coefficient terms"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            className="mt-1 block w-full rounded border px-2 py-1 text-[13px]"
            style={{
              background: "var(--bg-input)",
              borderColor: "var(--border)",
              color: "var(--text-primary)",
            }}
          />
        </label>
        {intervalAvailable && (
          <div className="flex gap-1">
            <button
              type="button"
              aria-pressed={view === "table"}
              onClick={() => setView("table")}
              className="focus-ring rounded px-2 py-1 text-[13px]"
              style={{ color: view === "table" ? MODEL_COLORS.accent : "var(--text-secondary)" }}
            >
              Table
            </button>
            <button
              type="button"
              aria-pressed={view === "interval"}
              onClick={() => setView("interval")}
              className="focus-ring rounded px-2 py-1 text-[13px]"
              style={{ color: view === "interval" ? MODEL_COLORS.accent : "var(--text-secondary)" }}
            >
              Intervals
            </button>
          </div>
        )}
      </div>
      {visibleRows.length === 0 ? (
        <p role="status" className="text-[13px]" style={{ color: "var(--text-muted)" }}>
          No coefficient terms match your search.
        </p>
      ) : view === "interval" && intervalAvailable ? (
        <IntervalView rows={visibleRows} />
      ) : (
        <>
          <div className="overflow-auto" style={{ maxHeight: 480 }}>
            <table
              className="w-full text-[13px] font-sans [&_td]:tabular-nums"
              style={{ borderCollapse: "collapse" }}
            >
              <thead
                className="sticky top-0"
                style={{ background: "var(--bg-elevated)", zIndex: 1 }}
              >
                <tr style={{ borderBottom: "1px solid var(--border)" }}>
                  {columns.map((col) => (
                    <th
                      key={col.key}
                      aria-sort={
                        sort?.key === col.key
                          ? sort.dir === "asc"
                            ? "ascending"
                            : "descending"
                          : undefined
                      }
                      className={`px-2 py-1.5 font-semibold ${col.align}`}
                      style={{ color: "var(--text-muted)", whiteSpace: "nowrap" }}
                    >
                      <button
                        type="button"
                        disabled={!sortable(col.key)}
                        onClick={() => handleSort(col.key)}
                        className="focus-ring disabled:cursor-not-allowed"
                      >
                        {col.label}
                        {sortIndicator(col.key)}
                      </button>
                    </th>
                  ))}
                  <th
                    className="px-2 py-1.5 text-center font-semibold"
                    style={{ color: "var(--text-muted)" }}
                  >
                    Sig.
                  </th>
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row, i) => (
                  <tr
                    key={row.feature}
                    style={{
                      borderBottom: "1px solid var(--border)",
                      background: i % 2 === 0 ? "transparent" : "rgba(255,255,255,.02)",
                    }}
                  >
                    <td
                      className="px-2 py-1 text-left break-words"
                      style={{ color: "var(--text-primary)", maxWidth: 300 }}
                    >
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
                    <td className="px-2 py-1 text-right" style={{ color: NEUTRAL_STATISTIC_COLOR }}>
                      {formatPValue(row.p_value)}
                    </td>
                    <td
                      className="px-2 py-1 text-center font-bold"
                      style={{ color: NEUTRAL_STATISTIC_COLOR }}
                    >
                      {row.significance === null ? DASH : row.significance}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <div className="flex flex-wrap gap-3 text-[12px]" style={{ color: "var(--text-muted)" }}>
        {valid && (
          <>
            <span>Signif. codes:</span>
            <span>*** &lt; 0.001</span>
            <span>** &lt; 0.01</span>
            <span>* &lt; 0.05</span>
            <span>. &lt; 0.1</span>
          </>
        )}
        <span>
          {search
            ? `${visibleRows.length} of ${rows.length} terms`
            : `${rows.length} term${rows.length !== 1 ? "s" : ""}`}
        </span>
      </div>
    </div>
  )
}

function IntervalView({ rows }: { rows: TrainResult["glm_coefficients"] }) {
  const intervals = rows
    .filter(
      (row) =>
        Number.isFinite(row.coefficient) &&
        Number.isFinite(row.std_error) &&
        (row.std_error ?? -1) >= 0,
    )
    .map((row) => ({
      ...row,
      lower: row.coefficient - 1.96 * row.std_error!,
      upper: row.coefficient + 1.96 * row.std_error!,
    }))
  const max = Math.max(...intervals.flatMap((row) => [Math.abs(row.lower), Math.abs(row.upper)]), 1)
  return (
    <section aria-label="95% Wald intervals" className="space-y-3">
      <h3 className="text-[13px] font-semibold" style={{ color: "var(--text-secondary)" }}>
        95% Wald intervals
      </h3>
      <div
        className="validation-coefficient-interval validation-coefficient-reference text-[12px]"
        style={{ color: "var(--text-muted)" }}
      >
        <span>Term</span>
        <span className="relative text-center">
          <span
            className="absolute left-1/2 top-full h-1 border-l"
            style={{ borderColor: "var(--text-muted)" }}
          />
          0
        </span>
        <span>Estimate [95% interval]</span>
      </div>
      {intervals.map((row) => (
        <div key={row.feature} className="validation-coefficient-interval text-[13px]">
          <span className="break-words">{row.feature}</span>
          <div className="relative h-4">
            <span
              className="absolute inset-y-0 left-1/2 border-l"
              style={{ borderColor: "var(--text-muted)" }}
            />
            <span
              className="absolute top-1.5 h-0.5"
              style={{
                left: `${50 + (row.lower / max) * 50}%`,
                width: `${((row.upper - row.lower) / max) * 50}%`,
                background: MODEL_COLORS.accent,
              }}
            />
            <span
              className="absolute top-0.5 h-3 border-l"
              style={{
                left: `${50 + (row.lower / max) * 50}%`,
                borderColor: MODEL_COLORS.accent,
              }}
            />
            <span
              className="absolute top-0.5 h-3 border-l"
              style={{
                left: `${50 + (row.upper / max) * 50}%`,
                borderColor: MODEL_COLORS.accent,
              }}
            />
            <span
              className="absolute top-1 h-1.5 w-1.5 rounded-full"
              style={{
                left: `calc(${50 + (row.coefficient / max) * 50}% - 3px)`,
                background: MODEL_COLORS.accent,
              }}
            />
          </div>
          <span className="tabular-nums">
            {row.coefficient.toFixed(3)} [{row.lower.toFixed(3)}, {row.upper.toFixed(3)}]
          </span>
        </div>
      ))}
    </section>
  )
}
