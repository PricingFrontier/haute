import type { CSSProperties } from "react"
import { formatValueCompact } from "../utils/formatValue"

export interface NodeCanvasFlagFields {
  _traceActive?: boolean
  _traceDimmed?: boolean
  _hoverDimmed?: boolean
  _traceFocused?: boolean
  _changeFocused?: boolean
  _traceValue?: unknown
  _traceMotionDisabled?: boolean
  _diffStatus?: "added" | "removed" | "changed" | "moved"
}

export interface DerivedNodeCanvasFlags {
  traceActive: boolean
  dimmed: boolean
  focusShadow: string | null
  diffShadow: string | null
  diffOutline: CSSProperties
  traceValue: unknown
  showTraceValue: boolean
  traceMotionDisabled: boolean
  containerStyle: CSSProperties
}

// Shared by the three canvas node renderers alongside NodeTraceValuePill.
// eslint-disable-next-line react-refresh/only-export-components
export function deriveNodeCanvasFlags(
  nodeData: NodeCanvasFlagFields,
  options: {
    accent: string
    selected?: boolean
    restBorder: string
    overrideBorder?: string | null
  },
): DerivedNodeCanvasFlags {
  const traceActive = !!nodeData._traceActive
  const dimmed = !!nodeData._traceDimmed || !!nodeData._hoverDimmed
  const focusShadow =
    nodeData._traceFocused || nodeData._changeFocused
      ? "0 0 0 3px var(--accent), 0 0 18px 4px color-mix(in srgb, var(--accent) 55%, transparent)"
      : null
  const diffStatus = nodeData._diffStatus
  const diffVar = diffStatus ? `var(--diff-${diffStatus})` : null
  const diffShadow =
    diffVar && diffStatus !== "moved"
      ? `0 0 0 2px ${diffVar}, 0 0 14px 2px color-mix(in srgb, ${diffVar} 45%, transparent)`
      : null
  const diffOutline: CSSProperties =
    diffStatus === "moved"
      ? { outline: "2px dashed var(--diff-moved)", outlineOffset: "3px" }
      : {}
  const traceValue = nodeData._traceValue
  const showTraceValue = traceActive && traceValue !== undefined
  const traceMotionDisabled = !!nodeData._traceMotionDisabled

  const border =
    options.overrideBorder ??
    (traceActive || options.selected
      ? `3px solid ${options.accent}`
      : options.restBorder)
  const shadow = traceActive
    ? `0 0 12px ${options.accent}40, var(--node-shadow)`
    : "var(--node-shadow)"
  const containerStyle: CSSProperties = {
    border,
    boxShadow: [focusShadow, diffShadow, shadow].filter(Boolean).join(", "),
    ...diffOutline,
    opacity: dimmed ? 0.25 : 1,
    transition: traceMotionDisabled
      ? "none"
      : "border-color 0.15s ease, opacity 0.2s ease, box-shadow 0.2s ease",
  }

  return {
    traceActive,
    dimmed,
    focusShadow,
    diffShadow,
    diffOutline,
    traceValue,
    showTraceValue,
    traceMotionDisabled,
    containerStyle,
  }
}

export function NodeTraceValuePill({
  accent,
  value,
  className = "",
}: {
  accent: string
  value: unknown
  className?: string
}) {
  return (
    <div
      data-testid="node-trace-value"
      className={`${className ? `${className} ` : ""}px-1.5 py-0.5 rounded text-[11px] font-mono truncate`}
      style={{
        background: `${accent}18`,
        color: accent,
        border: `1px solid ${accent}30`,
        maxWidth: "100%",
      }}
    >
      {formatValueCompact(value)}
    </div>
  )
}
