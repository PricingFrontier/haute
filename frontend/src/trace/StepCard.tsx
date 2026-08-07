import { useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"
import type { TraceResult, TraceStep } from "../types/trace"
import {
  nodeTypeLabels,
  nodeTypeColors,
} from "../utils/nodeTypes"
import { formatExpression } from "../utils/formatTrace"
import { traceValuePresentation } from "./traceFormatting"
import CalculationHero from "./CalculationHero"
import WaterfallErrorAlert from "./WaterfallErrorAlert"
import { isTraceOriginStep } from "./traceOrigins"
import { CHART_COLORS } from "../theme/colors"
import { NodeDetailBlock } from "./NodeDetailBlock"
import { hasBandingSecondaryDetail, hasRenderableBandingRows } from "./bandingRows"
import { hasRichRatingStepDetail } from "./ratingStepHelpers"
import {
  hasPrimaryNodeDetail,
  hasRichBandingDetail,
} from "../panels/trace/traceStoryView"

function detailUsesDefault(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(detailUsesDefault)
  if (typeof value !== "object" || value === null) return false
  const detail = value as Record<string, unknown>
  if (
    detail.default_used === true ||
    detail.is_default === true ||
    detail.status === "default"
  ) return true
  return Object.values(detail).some(detailUsesDefault)
}

function isComputedPlaceholder(value: string | undefined): boolean {
  return value?.trim().toLowerCase() === "computed"
}

export function StepCard({
  step,
  index,
  tracedColumn,
  isTargetStep,
  defaultExpanded = false,
  waterfall,
}: {
  step: TraceStep
  index: number
  tracedColumn: string | null
  isTargetStep?: boolean
  defaultExpanded?: boolean
  waterfall?: TraceResult["waterfall"]
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const accent = nodeTypeColors[step.node_type] || CHART_COLORS.cyan
  const typeLabel = nodeTypeLabels[step.node_type] || "NODE"
  const relevant = step.column_relevant
  const defaultUsed = detailUsesDefault(step.node_detail)
  const waterfallError = waterfall && !Array.isArray(waterfall) && "error" in waterfall
    ? waterfall
    : null

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
    added: { bg: "var(--trace-added-bg)", color: "var(--trace-added-text)", label: "+" },
    modified: { bg: "var(--warning-bright-soft)", color: "var(--color-modified)", label: "~" },
    value: { bg: "rgba(255,255,255,.06)", color: "var(--text-secondary)", label: "=" },
  }

  // All output columns for expanded view
  const allOutputCols = Object.keys(step.output_values)
  const richNodeDetail = hasPrimaryNodeDetail(step)
  const isOriginStep = isTraceOriginStep(step, tracedColumn)
  const sourceCalculationIsPlaceholder = isComputedPlaceholder(step.calculation?.substituted_text)
  const showSourceOrigin = isOriginStep &&
    (step.expression?.expression_type === "opaque" || sourceCalculationIsPlaceholder)
  const showOpaqueComputed = step.expression?.expression_type === "opaque" && !richNodeDetail && !isOriginStep
  const rawCalculationBlockText = step.calculation != null &&
    !richNodeDetail &&
    !(isOriginStep && sourceCalculationIsPlaceholder)
    ? step.calculation.substituted_text
    : null
  const calculationBlockText = rawCalculationBlockText != null && rawCalculationBlockText.trim().length > 0
    ? rawCalculationBlockText
    : null
  const showCalculationHero = Boolean(
    isTargetStep &&
    !richNodeDetail &&
    (step.expression != null || step.calculation != null) &&
    tracedColumn,
  )
  const showSecondaryDetail = Boolean(
    step.node_detail &&
    (
      !showCalculationHero ||
      hasRichRatingStepDetail(step) ||
      (
        hasRichBandingDetail(step) &&
        (step.calculation == null || hasBandingSecondaryDetail(step.node_detail))
      )
    ),
  )
  const showColumnValuesTable = !step.expression &&
    !step.calculation &&
    !richNodeDetail &&
    !(hasRichBandingDetail(step) && hasRenderableBandingRows(step.node_detail))

  return (
    <div
      className="rounded-lg overflow-hidden transition-opacity"
      data-testid={`trace-step-card-${step.node_id}`}
      data-target-step={isTargetStep || undefined}
      data-relevance={relevant ? "relevant" : "irrelevant"}
      style={{
        border: relevant ? `1px solid ${accent}40` : "1px solid var(--border)",
        background: "var(--bg-elevated)",
        opacity: relevant ? 1 : 0.55,
      }}
    >
      {/* Collapsed header - hover bg driven by Tailwind.  The inline
          `background: transparent` is intentionally omitted so the
          Tailwind `hover:` rule can apply (inline > class specificity). */}
      <button
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        aria-controls={`trace-step-body-${step.node_id}`}
        className="w-full flex items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-[var(--bg-hover)]"
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
              if (diff.columns_passed.includes(tracedColumn)) return "rows unchanged"
              return null
            }
            return step.row_lineage_type === "passthrough"
              ? "rows unchanged"
              : step.row_lineage_type || null
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
        {defaultUsed && (
          <span
            className="ml-auto text-[9px] font-semibold shrink-0 px-1.5 py-0.5 rounded"
            style={{ color: "var(--warning-strong)", background: "var(--warning-soft)" }}
          >
            default used
          </span>
        )}
      </button>

      {/* Key values (always visible when there are entries) */}
      {keyEntries.length > 0 && !expanded && (
        <div className="px-3 pb-2 flex flex-wrap gap-1.5" style={{ paddingLeft: "2.8rem" }}>
          {keyEntries.map(({ col, val, tag }) => {
            const tc = tagColors[tag]
            const formattedValue = traceValuePresentation(val, col)
            return (
              <span
                key={col}
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-mono"
                title={formattedValue.title}
                aria-label={formattedValue.ariaLabel}
                style={{ background: tc.bg, color: tc.color }}
              >
                <span className="font-bold">{tc.label}</span>
                {col}: {formattedValue.display}
              </span>
            )
          })}
          {calculationBlockText != null && !isTargetStep && (
            <span
              className="inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-mono"
              style={{ background: "rgba(255,255,255,.06)", color: "var(--text-secondary)" }}
            >
              {calculationBlockText}
            </span>
          )}
        </div>
      )}

      {/* Expanded: full column list */}
      {expanded && (
        <div
          id={`trace-step-body-${step.node_id}`}
          data-testid={`trace-step-body-${step.node_id}`}
          className="px-3 pb-3"
          style={{ borderTop: "1px solid var(--border)" }}
        >
          {waterfallError && (
            <div className="pt-2">
              <WaterfallErrorAlert
                error={waterfallError.error}
                errorType={waterfallError.error_type}
              />
            </div>
          )}
          {showCalculationHero && tracedColumn && (
            <div className="pt-2">
              <CalculationHero
                column={tracedColumn}
                expression={step.expression ?? null}
                calculation={step.calculation ?? null}
                nodeName={step.node_name}
                nodeType={step.node_type}
                isSourceOrigin={isOriginStep}
                waterfall={waterfallError ? undefined : waterfall}
                frame={false}
              />
            </div>
          )}

          {/* Expression block */}
          {!showCalculationHero && !richNodeDetail && step.expression && step.expression.expression_type !== "opaque" && (
            <div
              className="my-2 px-2 py-1.5 rounded text-[11px] font-mono"
              style={{ background: "rgba(255,255,255,.04)", color: "var(--text-secondary)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}
            >
              {formatExpression(step.expression.expression_text, 200)}
            </div>
          )}
          {!showCalculationHero && showOpaqueComputed && (
            <div className="my-2 text-[11px]" style={{ color: "var(--text-muted)", fontStyle: "italic" }}>
              computed
            </div>
          )}
          {!showCalculationHero && showSourceOrigin && (
            <div className="my-2 flex items-baseline gap-1.5 text-[11px]" style={{ color: "var(--text-muted)" }}>
              <span>Source node</span>
              <span className="font-mono font-semibold" style={{ color: "var(--text-secondary)" }}>
                {step.node_name}
              </span>
            </div>
          )}

          {/* Calculation block */}
          {!showCalculationHero && calculationBlockText != null && (
            <div
              className="my-2 px-2 py-1.5 rounded text-[12px] font-mono font-semibold"
              style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
            >
              {calculationBlockText}
            </div>
          )}

          {/* Node detail section */}
          {showSecondaryDetail && step.node_detail && (
            <NodeDetailBlock detail={step.node_detail} tracedColumn={tracedColumn} />
          )}

          {/* Schema changes summary */}
          <div className="flex flex-wrap gap-2 py-2 text-[10px]">
            {columns_added.length > 0 && (
              <span style={{ color: "var(--trace-added-text)" }}>+{columns_added.length} added</span>
            )}
            {columns_modified.length > 0 && (
              <span style={{ color: "var(--color-modified)" }}>~{columns_modified.length} modified</span>
            )}
            {columns_removed.length > 0 && (
              <span style={{ color: "var(--color-removed)" }}>-{columns_removed.length} removed</span>
            )}
            <span style={{ color: "var(--text-muted)" }}>
              {step.schema_diff.columns_passed.length} passed through
            </span>
          </div>

          {/* Column values table (shown when no richer node-specific detail exists) */}
          {showColumnValuesTable && <div className="space-y-0.5">
            {allOutputCols.map((col) => {
              const isAdded = columns_added.includes(col)
              const isModified = columns_modified.includes(col)
              const isRemoved = columns_removed.includes(col)
              const inputVal = step.input_values[col]
              const outputVal = step.output_values[col]
              const isTraced = col === tracedColumn
              const formattedInputValue = traceValuePresentation(inputVal, `${col} input`)
              const formattedOutputValue = traceValuePresentation(outputVal, `${col} output`)

              let rowColor = "var(--text-secondary)"
              let prefix = ""
              if (isAdded) {
                rowColor = "var(--trace-added-text)"
                prefix = "+"
              } else if (isModified) {
                rowColor = "var(--color-modified)"
                prefix = "~"
              } else if (isRemoved) {
                rowColor = "var(--color-removed)"
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
                      <span
                        title={formattedInputValue.title}
                        aria-label={formattedInputValue.ariaLabel}
                        style={{ color: "var(--text-muted)" }}
                      >
                        {formattedInputValue.display}
                      </span>
                      <span style={{ color: "var(--text-muted)" }}>&rarr;</span>
                    </>
                  )}
                  <span
                    title={formattedOutputValue.title}
                    aria-label={formattedOutputValue.ariaLabel}
                    style={{ color: isAdded || isModified ? rowColor : "var(--text-secondary)" }}
                  >
                    {formattedOutputValue.display}
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
