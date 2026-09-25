/**
 * Per-quote detail of an online optimiser result: which scenario the solve
 * chose for each quote, for the current publish target (the solved result or
 * one frontier point).
 *
 * Loaded when the tab opens and again when the target changes. The server
 * bounds the rows it returns, so the tab states both the total and the cap.
 * Viewing detail never consumes the result: saving and logging still work.
 */

import { useEffect, useState } from "react"
import { AlertCircle, Loader2 } from "lucide-react"
import { applyOptimiser } from "../../api/client"
import { apiErrorMessage } from "../../api/errors"
import type { ApplyOptimiserResponse } from "../../api/types"
import { formatNumber } from "../../utils/formatValue"

type QuotesState =
  | { status: "loading"; key: string }
  | { status: "loaded"; key: string; data: ApplyOptimiserResponse }
  | { status: "error"; key: string; error: string }

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return ""
  if (typeof value === "number") return formatNumber(value)
  return String(value)
}

export default function QuotesTab({ jobId, pointIndex }: { jobId: string; pointIndex: number | null }) {
  const key = `${jobId}:${pointIndex ?? "solved"}`
  const [state, setState] = useState<QuotesState>({ status: "loading", key })
  // Adjust-on-render: a new target shows loading at once, never the old rows.
  if (state.key !== key) setState({ status: "loading", key })

  useEffect(() => {
    const controller = new AbortController()
    applyOptimiser(
      { job_id: jobId, ...(pointIndex !== null ? { point_index: pointIndex } : {}) },
      { signal: controller.signal },
    )
      .then((data) => {
        if (!controller.signal.aborted) setState({ status: "loaded", key, data })
      })
      .catch((error) => {
        if (!controller.signal.aborted) setState({ status: "error", key, error: apiErrorMessage(error, "The request failed.") })
      })
    return () => controller.abort()
  }, [jobId, key, pointIndex])

  if (state.status === "loading") {
    return (
      <div className="flex items-center gap-2 text-xs" style={{ color: "var(--text-muted)" }}>
        <Loader2 size={14} className="animate-spin" />
        Loading per-quote detail...
      </div>
    )
  }
  if (state.status === "error") {
    return (
      <div role="alert" className="flex items-start gap-2 text-xs px-3 py-2 rounded" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>
        <AlertCircle size={14} className="mt-0.5 shrink-0" />
        <span>Per-quote detail could not be loaded: {state.error}</span>
      </div>
    )
  }

  const { data } = state
  const rows = data.preview
  const columns = rows.length > 0 ? Object.keys(rows[0]) : []
  const shown = data.preview_row_count ?? rows.length
  const total = data.row_count ?? shown
  return (
    <div className="space-y-2">
      <div className="text-[11px]" style={{ color: "var(--text-secondary)" }}>
        {`${shown.toLocaleString()} of ${total.toLocaleString()} quotes, with the scenario the ${pointIndex === null ? "solved result" : `frontier point ${pointIndex + 1}`} chose for each`}
        {data.preview_truncated && data.preview_row_limit != null ? ` (capped at ${data.preview_row_limit.toLocaleString()} rows)` : ""}
      </div>
      {rows.length === 0 ? (
        <div className="text-xs" style={{ color: "var(--text-muted)" }}>No quotes were returned.</div>
      ) : (
        <div className="overflow-auto rounded" style={{ border: "1px solid var(--border)", maxHeight: 360 }}>
          <table className="w-full text-[11px] font-mono" aria-label="Per-quote detail">
            <thead style={{ position: "sticky", top: 0, background: "var(--bg-elevated)" }}>
              <tr>
                {columns.map((column) => (
                  <th key={column} scope="col" className="px-2 py-1 text-left font-semibold whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={index} style={{ borderTop: "1px solid var(--border)" }}>
                  {columns.map((column) => (
                    <td key={column} className="px-2 py-0.5 whitespace-nowrap" style={{ color: "var(--text-primary)" }}>
                      {formatCell(row[column])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
