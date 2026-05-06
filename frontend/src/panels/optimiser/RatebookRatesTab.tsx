import { useMemo, useState } from "react"
import {
  formatFactorLevel,
  numericRate,
  RATE_COLUMN,
  type FactorTableRow,
  type FactorTables,
} from "./ratebookFactorTables"

interface RatebookRatesTabProps {
  factorTables: FactorTables
}

const EMPTY_FACTOR_ROWS: FactorTableRow[] = []

function formatRate(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(4) : "N/A"
}

function rateSummary(rows: FactorTableRow[]): { min: number | null; max: number | null } {
  let min: number | null = null
  let max: number | null = null
  for (const row of rows) {
    const value = numericRate(row)
    if (value == null) continue
    min = min == null ? value : Math.min(min, value)
    max = max == null ? value : Math.max(max, value)
  }
  return { min, max }
}

export default function RatebookRatesTab({ factorTables }: RatebookRatesTabProps) {
  const entries = useMemo(
    () => Object.entries(factorTables).filter(([, rows]) => Array.isArray(rows) && rows.length > 0),
    [factorTables],
  )
  const [selectedFactor, setSelectedFactor] = useState(entries[0]?.[0] ?? "")
  const selectedFactorName = entries.some(([name]) => name === selectedFactor)
    ? selectedFactor
    : entries[0]?.[0] ?? ""
  const selectedRows = entries.find(([name]) => name === selectedFactorName)?.[1] ?? EMPTY_FACTOR_ROWS
  const levelCount = entries.reduce((total, [, rows]) => total + rows.length, 0)
  const { min: minRate, max: maxRate } = useMemo(() => rateSummary(selectedRows), [selectedRows])

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <label className="flex min-w-[220px] flex-1 flex-col gap-1 text-[11px] font-medium" style={{ color: "var(--text-muted)" }}>
          Factor
          <select
            aria-label="Rate factor"
            value={selectedFactorName}
            onChange={(event) => setSelectedFactor(event.target.value)}
            className="h-8 min-w-0 rounded border px-2 text-xs outline-none"
            style={{
              background: "var(--bg-panel)",
              borderColor: "var(--border)",
              color: "var(--text-primary)",
            }}
          >
            {entries.map(([factorName, rows]) => (
              <option key={factorName} value={factorName}>
                {factorName} ({rows.length.toLocaleString()} levels)
              </option>
            ))}
          </select>
        </label>

        <div className="flex flex-wrap gap-2 text-[11px]">
          <RateStat label="Factors" value={entries.length.toLocaleString()} />
          <RateStat label="Levels" value={levelCount.toLocaleString()} />
          <RateStat label="Shown" value={selectedRows.length.toLocaleString()} />
          <RateStat label="Min" value={minRate == null ? "N/A" : formatRate(minRate)} />
          <RateStat label="Max" value={maxRate == null ? "N/A" : formatRate(maxRate)} />
        </div>
      </div>

      <section className="min-w-0">
        <div
          className="flex items-center justify-between gap-3 pb-1.5 mb-1.5"
          style={{ borderBottom: "1px solid var(--border)" }}
        >
          <h3
            className="min-w-0 truncate text-xs font-bold"
            style={{ color: "var(--text-primary)" }}
            title={selectedFactorName}
          >
            {selectedFactorName}
          </h3>
          <span className="shrink-0 text-[10px]" style={{ color: "var(--text-muted)" }}>
            {selectedRows.length.toLocaleString()} levels
          </span>
        </div>

        <div className="overflow-auto max-h-80">
          <table className="w-full border-separate border-spacing-0 text-xs font-mono">
            <thead className="sticky top-0" style={{ background: "var(--bg-panel)" }}>
              <tr>
                <th className="text-left font-medium py-1 pr-3" style={{ color: "var(--text-muted)" }}>
                  Level
                </th>
                <th className="text-right font-medium py-1 pl-3" style={{ color: "var(--text-muted)" }}>
                  Rate
                </th>
              </tr>
            </thead>
            <tbody>
              {selectedRows.map((row, index) => {
                const level = formatFactorLevel(row, index)
                return (
                  <tr key={`${level}-${index}`}>
                    <td className="py-1 pr-3 truncate" style={{ color: "var(--text-secondary)" }} title={level}>
                      {level}
                    </td>
                    <td className="py-1 pl-3 text-right tabular-nums" style={{ color: "var(--text-primary)" }}>
                      {formatRate(row[RATE_COLUMN])}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}

function RateStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-14">
      <div style={{ color: "var(--text-muted)" }}>{label}</div>
      <div className="font-mono tabular-nums" style={{ color: "var(--text-primary)" }}>
        {value}
      </div>
    </div>
  )
}
