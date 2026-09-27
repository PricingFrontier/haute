import { AlertTriangle, Scan } from "lucide-react"
import type { TraceRequestState } from "../hooks/useTracing"
import PanelShell from "./PanelShell"

// The trace request's loading and failure surface. Kept apart from TracePanel,
// which loads lazily, so a request's progress and errors never wait for that chunk.

interface TraceStatePanelProps {
  state: Exclude<TraceRequestState, { status: "idle" } | { status: "ready" }>
  onCancel: () => void
  onRetry: () => void
  onClose: () => void
}

/** Compact exceptional-latency and persistent failure surface for tracing. */
export function TraceStatePanel({ state, onCancel, onRetry, onClose }: TraceStatePanelProps) {
  if (state.status === "loading" && !state.progressVisible) return null
  const loading = state.status === "loading"
  return (
    <PanelShell testId="trace-state-panel">
      <div className="p-4 space-y-3">
        <div className="flex items-center gap-2" style={{ color: loading ? "var(--text-primary)" : "var(--danger)" }}>
          {loading ? <Scan size={16} className="animate-pulse" /> : <AlertTriangle size={16} />}
          <span className="text-sm font-semibold">{loading ? "Tracing this value…" : state.message}</span>
        </div>
        {loading ? (
          <button type="button" className="text-xs underline" onClick={onCancel}>Cancel</button>
        ) : (
          <>
            <details className="text-xs" style={{ color: "var(--text-muted)" }}>
              <summary>Technical details</summary>
              <pre className="mt-2 whitespace-pre-wrap font-mono">{state.detail}</pre>
            </details>
            <div className="flex gap-3">
              {state.retryable && <button type="button" className="text-xs underline" onClick={onRetry}>Retry</button>}
              <button type="button" className="text-xs underline" onClick={onClose}>Close</button>
            </div>
          </>
        )}
      </div>
    </PanelShell>
  )
}
