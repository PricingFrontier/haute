import { AlertCircle, AlertTriangle } from "lucide-react"
import type { ExecutionMetrics } from "../api/types"
import { buildExecutionDiagnostic } from "../utils/executionDiagnostics"

type ExecutionDiagnosticsIndicatorProps = {
  metrics?: ExecutionMetrics | null
}

type IndicatorContent = {
  severity: "warning" | "error"
  title: string
  explanation: string
  remediation?: string
}

function strategyIndicator(metrics: ExecutionMetrics): IndicatorContent | null {
  const strategy = metrics.execution_strategy
  if (!strategy || (strategy.status !== "boundary" && strategy.status !== "rejected")) {
    return null
  }

  const location = strategy.blocking_node_id
    ? ` at '${strategy.blocking_node_id}'${strategy.blocking_operator ? ` (${strategy.blocking_operator})` : ""}`
    : ""
  if (strategy.status === "rejected") {
    return {
      severity: "error",
      title: "Execution could not use a safe strategy",
      explanation: `Haute stopped execution${location} because it could not produce a safe plan.`,
      remediation: strategy.remediation ?? undefined,
    }
  }

  return {
    severity: "warning",
    title: "Column projection was limited",
    explanation: `Haute could not safely push the requested columns through the pipeline${location}, so that section stayed full-width. The preview result is still correct, but it may read more columns and use more memory than necessary.`,
    remediation: strategy.remediation ?? undefined,
  }
}

export default function ExecutionDiagnosticsIndicator({ metrics }: ExecutionDiagnosticsIndicatorProps) {
  if (!metrics) return null
  const strategy = strategyIndicator(metrics)
  const pressure = buildExecutionDiagnostic(metrics)
  const content: IndicatorContent | null = strategy?.severity === "error"
    ? strategy
    : strategy && pressure
      ? {
          ...strategy,
          explanation: `${strategy.explanation} ${pressure.message}`,
          remediation: [strategy.remediation, ...pressure.details].filter(Boolean).join("; "),
        }
      : strategy ?? (pressure
        ? {
        severity: "warning",
        title: "Preview memory pressure",
        explanation: pressure.message,
        remediation: pressure.details.join("; "),
          }
        : null)
  if (!content) return null

  const isError = content.severity === "error"
  const Icon = isError ? AlertCircle : AlertTriangle
  const color = isError ? "var(--danger)" : "var(--warning)"
  const label = isError ? "Preview execution error details" : "Preview execution warning details"

  return (
    <details className="relative shrink-0">
      <summary
        aria-label={label}
        title={content.title}
        className="flex cursor-pointer list-none items-center [&::-webkit-details-marker]:hidden"
      >
        <Icon size={14} style={{ color }} />
      </summary>
      <div
        role="status"
        className="absolute left-1/2 top-full z-50 mt-2 w-80 -translate-x-1/2 rounded-md p-3 text-[11px] shadow-lg"
        style={{ background: "var(--bg-elevated)", border: `1px solid ${color}`, color: "var(--text-secondary)" }}
      >
        <div className="font-semibold" style={{ color }}>{content.title}</div>
        <p className="mt-1 leading-4">{content.explanation}</p>
        {content.remediation && (
          <p className="mt-1 leading-4"><span className="font-medium">Suggested action:</span> {content.remediation}</p>
        )}
      </div>
    </details>
  )
}
