import { AlertCircle, AlertTriangle } from "lucide-react"
import type { ExecutionMetrics } from "../api/types"
import {
  buildMemoryPressureDiagnostic,
  executionProjectionWarning,
  executionStrategyLocation,
} from "../utils/executionDiagnostics"

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
  if (
    !strategy
    || (strategy.status !== "boundary" && strategy.status !== "rejected" && strategy.status !== "warned")
  ) {
    return null
  }

  if (strategy.status === "warned") {
    const location = executionStrategyLocation(strategy)
    return {
      severity: "warning",
      title: "Execution ran without a memory estimate",
      explanation: `Haute could not estimate the memory needed${location}, so it ran that step under the run's full reserved memory envelope inside a hard-capped worker. The result is correct, but the run may use more memory and time than an estimated plan.`,
      remediation: strategy.remediation ?? undefined,
    }
  }

  if (strategy.status === "rejected") {
    const location = executionStrategyLocation(strategy)
    return {
      severity: "error",
      title: "Execution could not use a safe strategy",
      explanation: `Haute stopped execution${location} because it could not produce a safe plan.`,
      remediation: strategy.remediation ?? undefined,
    }
  }

  const projectionWarning = executionProjectionWarning(metrics)
  if (!projectionWarning) return null
  const { boundary: projectionBoundary, nodeId, operator } = projectionWarning
  const location = nodeId ? ` at '${nodeId}'${operator ? ` (${operator})` : ""}` : ""

  return {
    severity: "warning",
    title: "Column projection was limited",
    explanation: `Haute could not safely push the requested columns through the pipeline${location}, so that section stayed full-width. The preview result is still correct, but it may read more columns and use more memory than necessary.`,
    remediation: projectionBoundary && strategy.strategy === "materialisation-boundary"
      ? "Give this node an explicit column contract, or rewrite the transform so Haute can prove its input columns."
      : strategy.remediation ?? undefined,
  }
}

export default function ExecutionDiagnosticsIndicator({ metrics }: ExecutionDiagnosticsIndicatorProps) {
  if (!metrics) return null
  const strategy = strategyIndicator(metrics)
  const pressure = buildMemoryPressureDiagnostic(metrics)
  const content: IndicatorContent | null = strategy?.severity === "error"
    ? strategy
    : strategy && pressure
      ? {
          ...strategy,
          // The memory-pressure finding is the terminal one: it names the
          // indicator even when a warned strategy is also rendered.
          title: "Preview memory pressure",
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
