import { useState, useMemo } from "react"
import { AlertTriangle, Copy, Download, Printer, X, Scan } from "lucide-react"
import type { TraceRequestState } from "../hooks/useTracing"
import type { TraceOmission, TraceResult } from "../types/trace"
import PanelShell from "./PanelShell"
import { StepCard } from "../trace/StepCard"
import { formatTraceValue, traceValuePresentation } from "../trace/traceFormatting"
import {
  defaultExpandedStepIds,
  traceStoryKey,
  traceStoryPreserveStepIds,
} from "./trace/traceStoryView"
import {
  findTargetStep,
  collapsePassthroughs,
  type CollapsedEntry,
} from "./trace/traceGrouping"

interface TracePanelProps {
  trace: TraceResult
  onClose: () => void
}

function isTraceOmission(value: CollapsedEntry | TraceOmission): value is TraceOmission {
  return "reason" in value
}

function evidenceRank(value: CollapsedEntry | TraceOmission): number {
  if (isTraceOmission(value)) return value.topological_rank
  if ("collapsed" in value) {
    return Math.min(...value.collapsed.map((step) => step.topological_rank))
  }
  return value.topological_rank
}

function loadTraceExport() {
  return import("../trace/traceExport")
}

function omissionSummary(reason: string): string {
  if (reason.includes("ambiguous") || reason.includes("duplicate")) {
    return "One upstream row could not be identified unambiguously."
  }
  if (reason.includes("unsupported")) {
    return "This upstream row uses a key type that tracing cannot compare safely."
  }
  if (reason.includes("source_frame")) {
    return "The contributing source frame could not be identified safely."
  }
  return "This upstream row could not be correlated safely."
}

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

export default function TracePanel({ trace, onClose }: TracePanelProps) {
  const storyKey = traceStoryKey(trace)
  const [showHidden, setShowHidden] = useState(false)
  const [exportStatus, setExportStatus] = useState<"idle" | "copied" | "error">("idle")
  const omittedDiagnosticIndices = useMemo(
    () => new Set(trace.omissions.map((omission) => omission.diagnostic_index)),
    [trace.omissions],
  )
  const correlationDiagnostics = trace.correlation_diagnostics.filter(
    (_diagnostic, index) => !omittedDiagnosticIndices.has(index),
  )

  const targetStep = useMemo(() => findTargetStep(trace.steps, trace.column), [trace.steps, trace.column])
  const preserveStepIds = useMemo(
    () => traceStoryPreserveStepIds(trace.steps, targetStep, trace.column),
    [trace.steps, targetStep, trace.column],
  )
  const expandedStepIds = useMemo(
    () => defaultExpandedStepIds(trace.steps, targetStep, trace.column),
    [trace.steps, targetStep, trace.column],
  )
  const focusedStoryEntries = useMemo<CollapsedEntry[]>(() => {
    if (!trace.column) return trace.steps
    return collapsePassthroughs(
      trace.steps,
      trace.column,
      preserveStepIds,
      { collapseUnpreserved: targetStep != null },
    )
  }, [trace.steps, trace.column, preserveStepIds, targetStep])
  const hiddenStepCount = useMemo(
    () => focusedStoryEntries.reduce((count, entry) => count + ("collapsed" in entry ? entry.collapsed.length : 0), 0),
    [focusedStoryEntries],
  )
  const storyEntries = useMemo<CollapsedEntry[]>(() => {
    if (showHidden) return trace.steps
    if (targetStep) {
      return focusedStoryEntries.filter((entry) => !("collapsed" in entry))
    }
    return focusedStoryEntries
  }, [focusedStoryEntries, showHidden, targetStep, trace.steps])
  const evidenceEntries = useMemo<Array<CollapsedEntry | TraceOmission>>(
    () => [...storyEntries, ...trace.omissions].sort(
      (left, right) => evidenceRank(left) - evidenceRank(right),
    ),
    [storyEntries, trace.omissions],
  )
  const stepIndexById = useMemo(
    () => new Map(trace.steps.map((step, index) => [step.node_id, index])),
    [trace.steps],
  )
  const outputPresentation = traceValuePresentation(trace.output_value, trace.column ?? "result")
  const rowIdPresentation = traceValuePresentation(trace.row_id_value, trace.row_id_column ?? "row")

  const copyTrace = async () => {
    try {
      const { copyTraceMarkdown } = await loadTraceExport()
      await copyTraceMarkdown(trace, (value) => navigator.clipboard.writeText(value))
      setExportStatus("copied")
    } catch {
      setExportStatus("error")
    }
  }

  const downloadTrace = async (extension: "md" | "csv") => {
    try {
      const [exporter, { downloadTextFile }] = await Promise.all([
        loadTraceExport(),
        import("./editors/shared/tableClipboard"),
      ])
      const markdown = extension === "md"
      const downloaded = downloadTextFile(
        markdown ? exporter.traceToMarkdown(trace) : exporter.traceToCsv(trace),
        exporter.traceExportFilename(trace, extension),
        markdown ? "text/markdown;charset=utf-8" : "text/csv;charset=utf-8",
      )
      if (!downloaded) setExportStatus("error")
    } catch {
      setExportStatus("error")
    }
  }

  const printTrace = async () => {
    const printWindow = window.open("", "haute-trace-print", "width=900,height=700")
    if (!printWindow) {
      setExportStatus("error")
      return
    }
    try {
      const { printTraceReport } = await loadTraceExport()
      printTraceReport(trace, () => printWindow)
    } catch {
      printWindow.close()
      setExportStatus("error")
    }
  }

  return (
    <PanelShell testId="trace-panel">
      {/* Header */}
      <div
        className="px-4 py-3 flex items-center gap-2 shrink-0"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
        <Scan size={14} style={{ color: "var(--accent)" }} />
        <div className="flex-1 min-w-0">
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1 text-xs font-bold" style={{ color: "var(--text-primary)" }}>
            <span className="truncate">Trace{trace.column ? `: ${trace.column}` : ""}</span>
            {trace.column && (
              <span
                className="font-mono text-[11px] font-semibold"
                data-testid="trace-target-summary"
                title={outputPresentation.title}
                aria-label={outputPresentation.ariaLabel}
                style={{ color: "var(--accent)", fontVariantNumeric: "tabular-nums" }}
              >
                = {outputPresentation.display}
              </span>
            )}
          </div>
          <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>
            {trace.row_id_column && trace.row_id_value != null ? (
              <><span className="font-mono">{trace.row_id_column}</span> = <span className="font-mono font-medium" title={rowIdPresentation.title} aria-label={rowIdPresentation.ariaLabel} style={{ color: "var(--text-secondary)" }}>{rowIdPresentation.display}</span></>
            ) : (
              <>Row {trace.row_index}</>
            )}
            {" "}&middot; {trace.nodes_in_trace} of {trace.total_nodes_in_pipeline} nodes
            {targetStep && (
              <>
                {" "}&middot; created by <span className="font-mono" style={{ color: "var(--text-secondary)" }}>{targetStep.node_name}</span>
              </>
            )}
            {hiddenStepCount > 0 && (
              <>
                {" "}&middot;{" "}
                <button
                  type="button"
                  data-testid="trace-show-full"
                  onClick={() => setShowHidden((value) => !value)}
                  className="underline-offset-2 hover:underline"
                  style={{ color: "var(--accent)" }}
                >
                  {showHidden ? "show focused trace" : "show full trace"}
                </button>
              </>
            )}
          </div>
        </div>
        <div
          className="trace-export-actions flex items-center gap-0.5"
          aria-label="Export trace"
          onPointerEnter={() => { void loadTraceExport() }}
          onFocus={() => { void loadTraceExport() }}
        >
          <button
            type="button"
            onClick={() => { void copyTrace() }}
            aria-label="Copy trace as Markdown"
            title="Copy Markdown"
            className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)]"
            style={{ color: "var(--text-muted)" }}
          >
            <Copy size={13} />
          </button>
          <button
            type="button"
            onClick={() => { void downloadTrace("md") }}
            aria-label="Download trace as Markdown"
            title="Download Markdown"
            className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)]"
            style={{ color: "var(--text-muted)" }}
          >
            <Download size={13} />
          </button>
          <button
            type="button"
            onClick={() => { void downloadTrace("csv") }}
            aria-label="Download trace as CSV"
            title="Download CSV"
            className="px-1 py-0.5 rounded text-[9px] font-semibold transition-colors hover:bg-[var(--bg-hover)]"
            style={{ color: "var(--text-muted)" }}
          >
            CSV
          </button>
          <button
            type="button"
            onClick={() => { void printTrace() }}
            aria-label="Print trace"
            title="Print"
            className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)]"
            style={{ color: "var(--text-muted)" }}
          >
            <Printer size={13} />
          </button>
        </div>
        <button
          onClick={onClose}
          aria-label="Close trace"
          className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)]"
          style={{ color: "var(--text-muted)" }}
        >
          <X size={14} />
        </button>
      </div>

      <div
        className="flex-1 overflow-y-auto p-3 space-y-2"
        data-testid="trace-story"
        style={{ background: "var(--bg-panel)" }}
      >
        {exportStatus === "copied" && (
          <div role="status" className="text-[11px]" style={{ color: "var(--success-hover)" }}>
            Trace copied as Markdown.
          </div>
        )}
        {exportStatus === "error" && (
          <div role="alert" className="text-[11px]" style={{ color: "var(--danger-text)" }}>
            The trace could not be exported. Check browser permissions and try again.
          </div>
        )}
        {correlationDiagnostics.length > 0 && (
          <div
            role="alert"
            data-testid="trace-correlation-diagnostics"
            className="flex gap-2 rounded px-2.5 py-2 text-[11px]"
            style={{
              background: "var(--warning-soft-emphasis)",
              border: "1px solid var(--warning-border-strong)",
              color: "var(--warning-strong)",
            }}
          >
            <AlertTriangle size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
            <div className="min-w-0 space-y-1">
              <div className="font-semibold">
                {correlationDiagnostics.length === 1
                  ? "Row correlation warning"
                  : `${correlationDiagnostics.length} row correlation warnings`}
              </div>
              {correlationDiagnostics.map((diagnostic, index) => (
                <div
                  key={`${diagnostic.code}-${diagnostic.node_id ?? "node"}-${diagnostic.child_node_id ?? "child"}-${index}`}
                  className="break-words"
                  style={{ color: "var(--text-secondary)" }}
                >
                  {diagnostic.message}
                </div>
              ))}
            </div>
          </div>
        )}

        {!trace.column && (
          <div className="flex items-center gap-2 rounded px-2 py-1.5 text-[11px]" style={{ background: "var(--bg-elevated)", color: "var(--text-muted)" }}>
            <span>Result</span>
            <span className="font-mono font-semibold" style={{ color: "var(--accent)" }}>
              {formatTraceValue(trace.output_value)}
            </span>
          </div>
        )}

        {evidenceEntries.map((entry, entryIndex) => {
          if (isTraceOmission(entry)) {
            const diagnostic = trace.correlation_diagnostics[entry.diagnostic_index]
            return (
              <div
                key={`omission-${entry.node_id}-${entry.topological_rank}`}
                role="alert"
                data-testid={`trace-omission-${entry.node_id}`}
                className="rounded-lg px-3 py-2 text-[11px]"
                style={{
                  border: "1px dashed var(--warning-border-strong)",
                  background: "var(--warning-soft)",
                  color: "var(--text-secondary)",
                }}
              >
                <div className="flex items-center gap-2">
                  <AlertTriangle size={13} aria-hidden="true" style={{ color: "var(--warning-strong)" }} />
                  <span className="font-mono" style={{ color: "var(--text-muted)" }}>
                    {entry.topological_rank + 1}
                  </span>
                  <span className="font-semibold" style={{ color: "var(--text-primary)" }}>
                    {entry.node_name}
                  </span>
                  <span className="text-[9px] uppercase tracking-wide" style={{ color: "var(--warning-strong)" }}>
                    trace gap
                  </span>
                </div>
                <div className="mt-1">{omissionSummary(entry.reason)}</div>
                {diagnostic && (
                  <details className="mt-1">
                    <summary>Technical details</summary>
                    <div className="mt-1 whitespace-pre-wrap break-words font-mono">
                      {diagnostic.message}
                    </div>
                  </details>
                )}
              </div>
            )
          }
          if ("collapsed" in entry) {
            const hiddenCount = entry.collapsed.length
            return (
              <button
                key={`collapsed-${entryIndex}-${hiddenCount}`}
                data-testid="trace-hidden-toggle"
                onClick={() => setShowHidden(true)}
                className="trace-hidden-toggle w-full py-1.5 rounded text-[11px] transition-colors"
                style={{ color: "var(--text-muted)", border: "1px dashed var(--border)", fontStyle: "italic" }}
              >
                {hiddenCount} pass-through node{hiddenCount > 1 ? "s" : ""} hidden
              </button>
            )
          }

          const isTargetStep = targetStep?.node_id === entry.node_id
          return (
            <StepCard
              key={`${storyKey}-${entry.node_id}`}
              step={entry}
              index={stepIndexById.get(entry.node_id) ?? entryIndex}
              tracedColumn={trace.column}
              isTargetStep={isTargetStep}
              defaultExpanded={expandedStepIds.has(entry.node_id)}
              waterfall={isTargetStep ? trace.waterfall : undefined}
            />
          )
        })}
      </div>
    </PanelShell>
  )
}
