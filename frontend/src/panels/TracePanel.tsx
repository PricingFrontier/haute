import { useState, useMemo } from "react"
import { X, ChevronDown, ChevronRight, Scan, Copy, Check } from "lucide-react"
import type { TraceResult, TraceStep } from "../types/trace"
import { nodeTypeLabels, nodeTypeColors } from "../utils/nodeTypes"
import { formatValue as _formatValue } from "../utils/formatValue"
import { formatExpression } from "../utils/formatTrace"
import PanelShell from "./PanelShell"
import CalculationHero from "../trace/CalculationHero"
import { findTargetStep, collapsePassthroughs } from "./trace/traceGrouping"
import { traceToMarkdown } from "./trace/traceToMarkdown"

const formatValue = (v: unknown) => _formatValue(v, 2)

function NodeDetailBlock({ detail }: { detail: Record<string, unknown> }) {
  const detailType = detail.detail_type as string | undefined

  const labelStyle = { color: "var(--text-muted)", fontSize: "10px" }
  const valueStyle = { color: "var(--text-secondary)", fontSize: "11px", fontFamily: "var(--font-mono, monospace)" }

  if (detailType === "rate_table_lookup" || detailType === "rating_step") {
    const keys = detail.lookup_keys as Record<string, unknown> | undefined
    const matched = detail.matched_row
    const defaultUsed = detail.default_used as boolean | undefined
    return (
      <div className="my-2 space-y-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>
        <div style={labelStyle}>Rate Table Lookup</div>
        {keys && (
          <div className="flex flex-wrap gap-1">
            {Object.entries(keys).map(([k, v]) => (
              <span key={k} className="px-1 py-0.5 rounded font-mono" style={{ background: "rgba(255,255,255,.06)" }}>
                {k}: {String(v)}
              </span>
            ))}
          </div>
        )}
        {matched != null && <div style={valueStyle}>Matched row: {String(matched)}</div>}
        {defaultUsed && (
          <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-bold" style={{ background: "rgba(251,191,36,.15)", color: "#fbbf24" }}>
            default used
          </span>
        )}
      </div>
    )
  }

  if (detailType === "banding") {
    return (
      <div className="my-2 space-y-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>
        <div style={labelStyle}>Banding</div>
        <div style={valueStyle}>Input: {String(detail.input_value)}</div>
        <div style={valueStyle}>Matched band: {String(detail.matched_band)}</div>
        {detail.lower_bound != null && detail.upper_bound != null && (
          <div style={valueStyle}>Range: [{String(detail.lower_bound)}, {String(detail.upper_bound)}]</div>
        )}
      </div>
    )
  }

  if (detailType === "model_score") {
    const features = detail.features_used as string[] | undefined
    const shapValues = detail.shap_values as Array<{ feature: string; value: number }> | undefined
    return (
      <div className="my-2 space-y-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>
        <div style={labelStyle}>Model: {String(detail.model_type)}</div>
        <div style={valueStyle}>Prediction: {String(detail.prediction)}</div>
        {features && features.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {features.map((f) => (
              <span key={f} className="px-1 py-0.5 rounded font-mono text-[10px]" style={{ background: "rgba(255,255,255,.06)" }}>
                {f}
              </span>
            ))}
          </div>
        )}
        {shapValues && shapValues.length > 0 && (
          <div className="mt-1 space-y-0.5">
            <div style={labelStyle}>SHAP values</div>
            {shapValues.map((s) => (
              <div key={s.feature} className="flex gap-2 font-mono text-[10px]">
                <span>{s.feature}</span>
                <span style={{ color: s.value >= 0 ? "#4ade80" : "#f87171" }}>{s.value >= 0 ? "+" : ""}{s.value}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    )
  }

  if (detailType === "scenario_expander") {
    return (
      <div className="my-2 space-y-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>
        <div style={labelStyle}>Scenario Expander</div>
        <div style={valueStyle}>Step: {String(detail.step)}</div>
        <div style={valueStyle}>Multiplier: {String(detail.multiplier)}</div>
      </div>
    )
  }

  if (detailType === "live_switch") {
    return (
      <div className="my-2 space-y-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>
        <div style={labelStyle}>Branch Selection</div>
        <div style={valueStyle}>Selected: {String(detail.selected_branch)}</div>
      </div>
    )
  }

  // Default: render as JSON
  return (
    <div className="my-2 text-[10px] font-mono" style={{ color: "var(--text-muted)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
      <pre>{JSON.stringify(detail, null, 2)}</pre>
    </div>
  )
}

function StepCard({ step, index, tracedColumn, isTargetStep }: { step: TraceStep; index: number; tracedColumn: string | null; isTargetStep?: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const accent = nodeTypeColors[step.node_type] || "#06b6d4"
  const typeLabel = nodeTypeLabels[step.node_type] || "NODE"
  const relevant = step.column_relevant

  const { columns_added, columns_modified, columns_removed } = step.schema_diff

  // Key values to always show (collapsed): traced column or first added/modified
  const keyEntries: { col: string; val: unknown; tag: "added" | "modified" | "value" }[] = []
  if (tracedColumn && step.output_values[tracedColumn] !== undefined) {
    const tag = columns_added.includes(tracedColumn)
      ? "added"
      : columns_modified.includes(tracedColumn)
        ? "modified"
        : "value"
    keyEntries.push({ col: tracedColumn, val: step.output_values[tracedColumn], tag })
  } else {
    for (const col of columns_added.slice(0, 2)) {
      keyEntries.push({ col, val: step.output_values[col], tag: "added" })
    }
    for (const col of columns_modified.slice(0, 2)) {
      keyEntries.push({ col, val: step.output_values[col], tag: "modified" })
    }
  }

  const tagColors = {
    added: { bg: "rgba(34,197,94,.12)", color: "var(--color-added, #4ade80)", label: "+" },
    modified: { bg: "rgba(251,191,36,.12)", color: "var(--color-modified, #fbbf24)", label: "~" },
    value: { bg: "rgba(255,255,255,.06)", color: "var(--text-secondary)", label: "=" },
  }

  // All output columns for expanded view
  const allOutputCols = Object.keys(step.output_values)

  return (
    <div
      className="rounded-lg overflow-hidden transition-opacity"
      style={{
        border: relevant ? `1px solid ${accent}40` : "1px solid var(--border)",
        background: "var(--bg-elevated)",
        opacity: relevant ? 1 : 0.55,
      }}
    >
      {/* Collapsed header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left transition-colors"
        style={{ background: "transparent" }}
        onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
        onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
      >
        {expanded ? (
          <ChevronDown size={12} style={{ color: "var(--text-muted)" }} />
        ) : (
          <ChevronRight size={12} style={{ color: "var(--text-muted)" }} />
        )}
        <span
          className="text-[11px] font-mono font-bold shrink-0"
          style={{ color: "var(--text-muted)", minWidth: "1.2em" }}
        >
          {index + 1}
        </span>
        <span className="text-[13px] font-semibold truncate" style={{ color: "var(--text-primary)" }}>
          {step.node_name}
        </span>
        <span
          className="text-[9px] font-bold uppercase tracking-wider shrink-0 px-1.5 py-0.5 rounded"
          style={{ color: accent, background: `${accent}15` }}
        >
          {typeLabel}
        </span>
        {(() => {
          const badge = (() => {
            if (tracedColumn) {
              const diff = step.schema_diff
              if (diff.columns_added.includes(tracedColumn)) return "creates"
              if (diff.columns_modified.includes(tracedColumn)) return "modifies"
              if (diff.columns_passed.includes(tracedColumn)) return "passes"
              return null
            }
            return step.row_lineage_type || null
          })()
          return badge ? (
            <span
              className="text-[9px] font-medium shrink-0 px-1 py-0.5 rounded"
              style={{ color: "var(--text-muted)", background: "rgba(255,255,255,.06)" }}
            >
              {badge}
            </span>
          ) : null
        })()}
        <span className="ml-auto text-[10px] font-mono shrink-0" style={{ color: "var(--text-muted)" }}>
          {step.execution_ms.toFixed(1)}ms
        </span>
      </button>

      {/* Key values (always visible when there are entries) */}
      {keyEntries.length > 0 && !expanded && (
        <div className="px-3 pb-2 flex flex-wrap gap-1.5" style={{ paddingLeft: "2.8rem" }}>
          {keyEntries.map(({ col, val, tag }) => {
            const tc = tagColors[tag]
            return (
              <span
                key={col}
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-mono"
                style={{ background: tc.bg, color: tc.color }}
              >
                <span className="font-bold">{tc.label}</span>
                {col}: {formatValue(val)}
              </span>
            )
          })}
          {step.calculation && !isTargetStep && (
            <span
              className="inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-mono"
              style={{ background: "rgba(255,255,255,.06)", color: "var(--text-secondary)" }}
            >
              {step.calculation.substituted_text}
            </span>
          )}
        </div>
      )}

      {/* Expanded: full column list */}
      {expanded && (
        <div className="px-3 pb-3" style={{ borderTop: "1px solid var(--border)" }}>
          {/* Expression block */}
          {step.expression && step.expression.expression_type !== "opaque" && (
            <div
              className="my-2 px-2 py-1.5 rounded text-[11px] font-mono"
              style={{ background: "rgba(255,255,255,.04)", color: "var(--text-secondary)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}
            >
              {formatExpression(step.expression.expression_text, 200)}
            </div>
          )}
          {step.expression && step.expression.expression_type === "opaque" && (
            <div className="my-2 text-[11px]" style={{ color: "var(--text-muted)", fontStyle: "italic" }}>
              computed
            </div>
          )}

          {/* Calculation block */}
          {step.calculation && (
            <div
              className="my-2 px-2 py-1.5 rounded text-[12px] font-mono font-semibold"
              style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
            >
              {step.calculation.substituted_text}
            </div>
          )}

          {/* Node detail section */}
          {step.node_detail && (
            <NodeDetailBlock detail={step.node_detail} />
          )}

          {/* Schema changes summary */}
          <div className="flex flex-wrap gap-2 py-2 text-[10px]">
            {columns_added.length > 0 && (
              <span style={{ color: "var(--color-added, #4ade80)" }}>+{columns_added.length} added</span>
            )}
            {columns_modified.length > 0 && (
              <span style={{ color: "var(--color-modified, #fbbf24)" }}>~{columns_modified.length} modified</span>
            )}
            {columns_removed.length > 0 && (
              <span style={{ color: "var(--color-removed, #f87171)" }}>-{columns_removed.length} removed</span>
            )}
            <span style={{ color: "var(--text-muted)" }}>
              {step.schema_diff.columns_passed.length} passed through
            </span>
          </div>

          {/* Column values table (shown when no expression/calculation detail) */}
          {!step.expression && !step.calculation && <div className="space-y-0.5">
            {allOutputCols.map((col) => {
              const isAdded = columns_added.includes(col)
              const isModified = columns_modified.includes(col)
              const isRemoved = columns_removed.includes(col)
              const inputVal = step.input_values[col]
              const outputVal = step.output_values[col]
              const isTraced = col === tracedColumn

              let rowColor = "var(--text-secondary)"
              let prefix = ""
              if (isAdded) {
                rowColor = "var(--color-added, #4ade80)"
                prefix = "+"
              } else if (isModified) {
                rowColor = "var(--color-modified, #fbbf24)"
                prefix = "~"
              } else if (isRemoved) {
                rowColor = "var(--color-removed, #f87171)"
                prefix = "-"
              }

              return (
                <div
                  key={col}
                  className="flex items-center gap-2 px-2 py-0.5 rounded text-[11px] font-mono"
                  style={{
                    background: isTraced ? "var(--accent-soft)" : "transparent",
                    borderLeft: isTraced ? "2px solid var(--accent)" : "2px solid transparent",
                  }}
                >
                  <span className="font-bold w-3" style={{ color: rowColor }}>
                    {prefix}
                  </span>
                  <span className="truncate" style={{ color: rowColor, minWidth: "6em", maxWidth: "10em" }}>
                    {col}
                  </span>
                  {isModified && inputVal !== undefined && (
                    <>
                      <span style={{ color: "var(--text-muted)" }}>{formatValue(inputVal)}</span>
                      <span style={{ color: "var(--text-muted)" }}>&rarr;</span>
                    </>
                  )}
                  <span style={{ color: isAdded || isModified ? rowColor : "var(--text-secondary)" }}>
                    {formatValue(outputVal)}
                  </span>
                </div>
              )
            })}
          </div>}
        </div>
      )}
    </div>
  )
}

type TraceTab = "calculation" | "nodes"

interface TracePanelProps {
  trace: TraceResult
  onClose: () => void
}

type DetailLevel = "formula" | "sources" | "all"

export default function TracePanel({ trace, onClose }: TracePanelProps) {
  const [activeTab, setActiveTab] = useState<TraceTab>("calculation")
  const [detailLevel, setDetailLevel] = useState<DetailLevel>("sources")
  const [copied, setCopied] = useState(false)
  const [showHidden, setShowHidden] = useState(false)

  const targetStep = useMemo(() => findTargetStep(trace.steps, trace.column), [trace.steps, trace.column])
  // Build a set of node IDs that are collapsed (pass-through)
  const collapsedIds = useMemo(() => {
    if (!trace.column) return new Set<string>()
    const entries = collapsePassthroughs(trace.steps, trace.column)
    const ids = new Set<string>()
    for (const entry of entries) {
      if ("collapsed" in entry) {
        for (const step of entry.collapsed) {
          ids.add(step.node_id)
        }
      }
    }
    return ids
  }, [trace.steps, trace.column])

  const handleCopy = async () => {
    const md = traceToMarkdown(trace, targetStep)
    try {
      await navigator.clipboard.writeText(md)
    } catch {
      // Fallback for non-HTTPS contexts
      const ta = document.createElement("textarea")
      ta.value = md
      document.body.appendChild(ta)
      ta.select()
      document.execCommand("copy")
      document.body.removeChild(ta)
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  // Split steps into visible vs hidden
  const visibleSteps: TraceStep[] = []
  const hiddenSteps: TraceStep[] = []
  for (const step of trace.steps) {
    if (detailLevel === "all" || showHidden || !collapsedIds.has(step.node_id)) {
      visibleSteps.push(step)
    } else {
      hiddenSteps.push(step)
    }
  }

  return (
    <PanelShell>
      {/* Header */}
      <div
        className="px-4 py-3 flex items-center gap-2 shrink-0"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
        <Scan size={14} style={{ color: "var(--accent)" }} />
        <div className="flex-1 min-w-0">
          <div className="text-xs font-bold" style={{ color: "var(--text-primary)" }}>
            Trace{trace.column ? `: ${trace.column}` : ""}
          </div>
          <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>
            {trace.row_id_column && trace.row_id_value != null ? (
              <><span className="font-mono">{trace.row_id_column}</span> = <span className="font-mono font-medium" style={{ color: "var(--text-secondary)" }}>{formatValue(trace.row_id_value)}</span></>
            ) : (
              <>Row {trace.row_index}</>
            )}
            {" "}&middot; {trace.nodes_in_trace} of {trace.total_nodes_in_pipeline} nodes
          </div>
        </div>
        <button
          onClick={handleCopy}
          className="p-1 rounded transition-colors"
          style={{ color: copied ? "var(--color-added, #4ade80)" : "var(--text-muted)" }}
          title="Copy trace as markdown"
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
          onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </button>
        <button
          onClick={onClose}
          className="p-1 rounded transition-colors"
          style={{ color: "var(--text-muted)" }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
          onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
        >
          <X size={14} />
        </button>
      </div>

      {/* Tab selection: Calculation | Nodes */}
      <div className="shrink-0 px-3 py-1.5" style={{ borderBottom: "1px solid var(--border)" }}>
        <div style={{
          display: "flex", background: "rgba(0,0,0,.2)", borderRadius: 6,
          padding: 2, gap: 2, border: "1px solid rgba(255,255,255,.05)",
        }}>
          {(["calculation", "nodes"] as const).map((tab) => {
            const active = activeTab === tab
            return (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                style={{
                  flex: 1, padding: "6px 0", fontSize: 11, fontWeight: 600,
                  borderRadius: 4, border: "none", cursor: "pointer",
                  transition: "all 150ms ease",
                  background: active ? "rgba(59,130,246,.12)" : "transparent",
                  color: active ? "#60a5fa" : "rgba(255,255,255,.35)",
                  boxShadow: active ? "0 1px 3px rgba(0,0,0,.2)" : "none",
                }}
              >
                {tab.charAt(0).toUpperCase() + tab.slice(1)}
              </button>
            )
          })}
        </div>
      </div>

      {/* Tab content */}
      {activeTab === "calculation" ? (
        /* Calculation tab — full derivation */
        <div className="flex-1 overflow-y-auto" style={{
          background: "linear-gradient(180deg, rgba(59,130,246,.04) 0%, rgba(59,130,246,.01) 100%)",
        }}>
          {targetStep && trace.column ? (
            <CalculationHero
              column={trace.column}
              expression={targetStep.expression ?? null}
              calculation={targetStep.calculation ?? null}
              executionMs={trace.execution_ms}
              stepCount={trace.steps.length}
              nodeName={targetStep.node_name}
              waterfall={trace.waterfall}
            />
          ) : (
            <div className="px-4 py-4">
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-medium" style={{ color: "var(--text-muted)" }}>Result</span>
                <span className="px-2 py-0.5 rounded text-[13px] font-mono font-bold" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>
                  {formatValue(trace.output_value)}
                </span>
              </div>
            </div>
          )}
        </div>
      ) : (
        /* Nodes tab — pipeline node list */
        <div className="flex-1 overflow-hidden flex flex-col">
          {/* Detail level toggle */}
          <div className="px-3 py-1.5 shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
            <div style={{
              display: "flex", background: "rgba(0,0,0,.2)", borderRadius: 6,
              padding: 2, gap: 2, border: "1px solid rgba(255,255,255,.05)",
            }}>
              {(["formula", "sources", "all"] as const).map((level) => {
                const active = detailLevel === level
                return (
                  <button
                    key={level}
                    onClick={() => { setDetailLevel(level); setShowHidden(false) }}
                    style={{
                      flex: 1, padding: "6px 0", fontSize: 11, fontWeight: 600,
                      borderRadius: 4, border: "none", cursor: "pointer",
                      transition: "all 150ms ease",
                      background: active ? "rgba(59,130,246,.12)" : "transparent",
                      color: active ? "#60a5fa" : "rgba(255,255,255,.35)",
                      boxShadow: active ? "0 1px 3px rgba(0,0,0,.2)" : "none",
                    }}
                  >
                    {level.charAt(0).toUpperCase() + level.slice(1)}
                  </button>
                )
              })}
            </div>
          </div>
          {/* Steps list */}
          <div className="flex-1 overflow-y-auto p-3 space-y-2">
            {detailLevel === "formula" ? (
              targetStep && (
                <StepCard step={targetStep} index={trace.steps.indexOf(targetStep)} tracedColumn={trace.column} isTargetStep={true} />
              )
            ) : (
              <>
                {visibleSteps.map((step) => (
                  <StepCard key={step.node_id} step={step} index={trace.steps.indexOf(step)} tracedColumn={trace.column} isTargetStep={targetStep?.node_id === step.node_id} />
                ))}
                {hiddenSteps.length > 0 && !showHidden && (
                  <button
                    onClick={() => setShowHidden(true)}
                    className="w-full py-1.5 rounded text-[11px] transition-colors"
                    style={{ color: "var(--text-muted)", background: "rgba(255,255,255,.03)", border: "1px dashed var(--border)", fontStyle: "italic" }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "rgba(255,255,255,.03)")}
                  >
                    {hiddenSteps.length} pass-through node{hiddenSteps.length > 1 ? "s" : ""} hidden
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </PanelShell>
  )
}
