/**
 * Per-quote detail of an online optimiser result: which scenario the solve
 * chose for each quote, for the current publish target (the solved result or
 * one frontier point).
 *
 * Loaded when the tab opens and again when the target changes, through the
 * result store's `/apply` cache: a response is kept under its full request
 * identity (job, frontier generation, target and query), so reopening the tab
 * for the same target makes no request, while a new job or a recomputed
 * frontier loads afresh. A response that arrives after the identity changed
 * is dropped. The server bounds the rows it returns, so the tab states both
 * the total and the cap. Viewing detail never consumes the result: saving and
 * logging still work.
 */

import { useEffect, useMemo, useState } from "react"
import { AlertCircle, Loader2 } from "lucide-react"
import { applyOptimiser } from "../../api/client"
import { apiErrorMessage } from "../../api/errors"
import useNodeResultsStore, {
  optimiserApplyKey,
  type OptimiserApplyIdentity,
  type OptimiserApplyQuery,
} from "../../stores/useNodeResultsStore"
import { formatNumber } from "../../utils/formatValue"

/** `/apply` takes no query beyond its target yet (OPT-V12 adds one). */
const APPLY_QUERY: OptimiserApplyQuery = {}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return ""
  if (typeof value === "number") return formatNumber(value)
  return String(value)
}

interface QuotesTabProps {
  nodeId: string
  jobId: string
  /** The solve's frontier generation: point indices are only meaningful within one. */
  frontierGeneration: number
  /** The publish target: a frontier point, or null for the solved result. */
  pointIndex: number | null
}

export default function QuotesTab({ nodeId, jobId, frontierGeneration, pointIndex }: QuotesTabProps) {
  const identity = useMemo<OptimiserApplyIdentity>(
    () => ({ jobId, frontierGeneration, target: pointIndex ?? "solved", query: APPLY_QUERY }),
    [jobId, frontierGeneration, pointIndex],
  )
  const key = optimiserApplyKey(identity)
  const cached = useNodeResultsStore((s) => s.optimiserApplyCache.find((entry) => entry.key === key))
  const recordApply = useNodeResultsStore((s) => s.recordOptimiserApply)
  const touchApply = useNodeResultsStore((s) => s.touchOptimiserApply)
  const [failure, setFailure] = useState<{ key: string; error: string } | null>(null)
  const hasCached = cached !== undefined
  const failed = failure !== null && failure.key === key

  useEffect(() => {
    if (hasCached) {
      touchApply(key)
      return
    }
    // A failed request is reissued only by Retry, which clears the failure.
    if (failed) return
    const controller = new AbortController()
    applyOptimiser(
      { job_id: identity.jobId, ...(identity.target !== "solved" ? { point_index: identity.target } : {}) },
      { signal: controller.signal },
    )
      .then((response) => {
        // Aborted means this tab's identity (its query included) moved on; the
        // store also refuses an identity whose job, generation or target did.
        if (!controller.signal.aborted) recordApply(nodeId, identity, response)
      })
      .catch((error) => {
        if (!controller.signal.aborted) setFailure({ key, error: apiErrorMessage(error, "The request failed.") })
      })
    return () => controller.abort()
  }, [failed, hasCached, identity, key, nodeId, recordApply, touchApply])

  if (failed) {
    return (
      <div role="alert" className="flex items-start gap-2 text-xs px-3 py-2 rounded" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>
        <AlertCircle size={14} className="mt-0.5 shrink-0" />
        <span className="flex-1">Per-quote detail could not be loaded: {failure.error}</span>
        <button
          type="button"
          onClick={() => setFailure(null)}
          className="shrink-0 rounded px-2 py-0.5 text-[11px] font-medium"
          style={{ border: "1px solid var(--danger-border-strong)", color: "var(--danger)" }}
        >
          Retry
        </button>
      </div>
    )
  }
  if (!cached) {
    return (
      <div className="flex items-center gap-2 text-xs" style={{ color: "var(--text-muted)" }}>
        <Loader2 size={14} className="animate-spin" />
        Loading per-quote detail...
      </div>
    )
  }

  const data = cached.response
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
