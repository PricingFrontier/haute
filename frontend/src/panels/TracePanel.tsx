import { useState, useMemo } from "react"
import { AlertTriangle, X, Scan } from "lucide-react"
import type { TraceResult } from "../types/trace"
import { formatValue as _formatValue } from "../utils/formatValue"
import PanelShell from "./PanelShell"
import { StepCard } from "../trace/StepCard"
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

const formatValue = (v: unknown) => _formatValue(v, 2)

interface TracePanelProps {
  trace: TraceResult
  onClose: () => void
}

export default function TracePanel({ trace, onClose }: TracePanelProps) {
  const storyKey = traceStoryKey(trace)
  const [showHidden, setShowHidden] = useState(false)
  const correlationDiagnostics = trace.correlation_diagnostics ?? []

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
                style={{ color: "var(--accent)", fontVariantNumeric: "tabular-nums" }}
              >
                = {formatValue(trace.output_value)}
              </span>
            )}
          </div>
          <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>
            {trace.row_id_column && trace.row_id_value != null ? (
              <><span className="font-mono">{trace.row_id_column}</span> = <span className="font-mono font-medium" style={{ color: "var(--text-secondary)" }}>{formatValue(trace.row_id_value)}</span></>
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
              {formatValue(trace.output_value)}
            </span>
          </div>
        )}

        {storyEntries.map((entry, entryIndex) => {
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
              index={trace.steps.indexOf(entry)}
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
