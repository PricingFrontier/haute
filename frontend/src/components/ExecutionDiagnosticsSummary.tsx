import { AlertTriangle } from "lucide-react"
import type { ExecutionMetrics } from "../api/types"
import { buildExecutionDiagnostic } from "../utils/executionDiagnostics"

type ExecutionDiagnosticsSummaryProps = {
  metrics?: ExecutionMetrics | null
  status?: string | null
  terminalReason?: string | null
  errorCode?: string | null
}

export default function ExecutionDiagnosticsSummary({
  metrics,
  status,
  terminalReason,
  errorCode,
}: ExecutionDiagnosticsSummaryProps) {
  const diagnostic = buildExecutionDiagnostic(metrics, {
    status,
    terminalReason,
    errorCode,
  })
  if (!diagnostic) return null

  return (
    <div
      className="flex items-start gap-2 px-3 py-2 text-[11px]"
      style={{ background: "var(--warning-soft-subtle)", borderTop: "1px solid var(--warning-border)", borderBottom: "1px solid var(--warning-border)" }}
    >
      <AlertTriangle size={12} className="shrink-0 mt-0.5" style={{ color: "var(--warning-strong)" }} />
      <div className="min-w-0">
        <div className="font-medium" style={{ color: "var(--warning)" }}>{diagnostic.message}</div>
        <details className="mt-0.5">
          <summary className="cursor-pointer" style={{ color: "var(--text-muted)" }}>Technical details</summary>
          <div className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5 font-mono leading-5" style={{ color: "var(--text-secondary)" }}>
            {diagnostic.details.map((detail) => (
              <span key={detail}>{detail}</span>
            ))}
          </div>
        </details>
      </div>
    </div>
  )
}
