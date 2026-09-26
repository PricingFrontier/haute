/**
 * A diverging-around-1.0 bar list: one row per item, in the caller's order,
 * with the bar running right of the centre line above 1.0 and left of it
 * below. GLM relativities and the optimiser's ratebook rates share it.
 */
import type { KeyboardEvent, ReactNode } from "react"
import { chartLabelIndices } from "../utils/chartHelpers"
import { formatFixed } from "../utils/formatValue"

export const RELATIVITY_ABOVE_COLOR = "var(--chart-above)"
export const RELATIVITY_BELOW_COLOR = "var(--chart-below)"
const TRACK_COLOR = "var(--chrome-hover)"
const BASELINE_COLOR = "var(--text-secondary)"
const WHISKER_COLOR = "var(--text-primary)"

/** The smallest half-scale, so near-flat values do not fill the track. */
const MIN_HALF_SCALE = 0.1
/** Compact rows are this tall; a thinned label needs this much room. */
const COMPACT_ROW_HEIGHT = 8
const COMPACT_LABEL_SPACING = 18

export type RelativityBar = {
  key: string
  label: string
  value: number
  ciLower?: number | null
  ciUpper?: number | null
}

export type RelativityBarsInteraction = {
  activeKey: string | null
  onActivate: (key: string) => void
  /** The row's accessible name: every value the row stands for. */
  describe: (bar: RelativityBar) => string
}

export type RelativityBarsAside = {
  header: ReactNode
  width: number
  render: (bar: RelativityBar, active: boolean) => ReactNode
}

function hasInterval(bar: RelativityBar): bar is RelativityBar & { ciLower: number; ciUpper: number } {
  return bar.ciLower != null && bar.ciUpper != null && Number.isFinite(bar.ciLower) && Number.isFinite(bar.ciUpper)
}

function halfScale(bars: readonly RelativityBar[]): number {
  let largest = MIN_HALF_SCALE
  for (const bar of bars) {
    if (!Number.isFinite(bar.value)) throw new Error(`${bar.label} has a non-finite value ${bar.value}`)
    largest = Math.max(largest, Math.abs(bar.value - 1))
    if (hasInterval(bar)) {
      largest = Math.max(largest, Math.abs(bar.ciLower - 1), Math.abs(bar.ciUpper - 1))
    }
  }
  return largest
}

export function RelativityBars({
  bars,
  ariaLabel,
  formatValue = (value) => formatFixed(value, 3),
  labelWidth = 140,
  maxHeight = 480,
  compactFrom,
  interaction,
  aside,
}: {
  bars: readonly RelativityBar[]
  ariaLabel: string
  formatValue?: (value: number) => string
  labelWidth?: number
  maxHeight?: number
  /** From this many bars, rows compact and labels thin (use only for an ordered axis). */
  compactFrom?: number
  interaction?: RelativityBarsInteraction
  aside?: RelativityBarsAside
}) {
  const scale = halfScale(bars)
  const compact = compactFrom !== undefined && bars.length >= compactFrom
  const labelled = compact
    ? chartLabelIndices(bars.length, bars.length * COMPACT_ROW_HEIGHT, COMPACT_LABEL_SPACING)
    : null
  const position = (value: number) => 50 + ((value - 1) / scale) * 50

  return (
    <div className="overflow-y-auto" style={{ maxHeight }} role="group" aria-label={ariaLabel}>
      {aside && (
        <div className="flex items-center gap-2 pb-1 text-[11px]" style={{ color: "var(--text-muted)" }}>
          <span className="shrink-0" style={{ width: labelWidth }} />
          <span className="flex-1" />
          <span className="w-14 shrink-0" />
          <span className="shrink-0" style={{ width: aside.width }}>
            {aside.header}
          </span>
        </div>
      )}
      <div className={compact ? undefined : "space-y-0.5"}>
        {bars.map((bar, index) => {
          const deviation = bar.value - 1
          const isAbove = deviation >= 0
          const width = (Math.abs(deviation) / scale) * 50
          const color = isAbove ? RELATIVITY_ABOVE_COLOR : RELATIVITY_BELOW_COLOR
          const active = interaction?.activeKey === bar.key
          const showText = labelled === null || labelled.has(index)
          const activate = () => interaction?.onActivate(bar.key)
          return (
            <div
              key={bar.key}
              data-testid="relativity-row"
              data-key={bar.key}
              className={`flex items-center gap-2 font-mono ${compact ? "text-[10px]" : "text-xs"}${interaction ? " focus-ring rounded-sm" : ""}`}
              style={compact ? { height: COMPACT_ROW_HEIGHT } : undefined}
              {...(interaction && {
                role: "button",
                tabIndex: 0,
                "aria-label": interaction.describe(bar),
                "aria-pressed": active,
                onFocus: activate,
                onMouseEnter: activate,
                onClick: activate,
                onKeyDown: (event: KeyboardEvent) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault()
                    activate()
                  }
                },
              })}
            >
              <span
                data-testid="relativity-label"
                className="truncate shrink-0 text-right"
                style={{
                  color: active ? "var(--text-primary)" : "var(--text-secondary)",
                  width: labelWidth,
                  // A thinned label overhangs its compact row; hover belongs to the row under the pointer.
                  pointerEvents: compact ? "none" : undefined,
                }}
                title={bar.label}
              >
                {showText ? bar.label : ""}
              </span>

              <div
                className="flex-1 relative"
                style={{ background: TRACK_COLOR, borderRadius: 3, height: compact ? COMPACT_ROW_HEIGHT - 2 : 16 }}
              >
                <div
                  data-relativity-baseline
                  className="absolute top-0 bottom-0 w-px"
                  style={{ left: "50%", background: BASELINE_COLOR, opacity: 0.5 }}
                />
                <div
                  data-relativity-bar
                  className={`absolute rounded-sm ${compact ? "top-0 bottom-0" : "top-0.5 bottom-0.5"}`}
                  style={{
                    left: `${isAbove ? 50 : 50 - width}%`,
                    width: `${width}%`,
                    background: color,
                    opacity: active ? 1 : 0.7,
                  }}
                />
                {hasInterval(bar) && (
                  <div
                    data-relativity-whisker
                    className="absolute top-1/2 h-px"
                    style={{
                      left: `${position(bar.ciLower)}%`,
                      width: `${((bar.ciUpper - bar.ciLower) / scale) * 50}%`,
                      background: WHISKER_COLOR,
                      opacity: 0.4,
                      transform: "translateY(-50%)",
                    }}
                  />
                )}
              </div>

              <span className="w-14 text-right shrink-0 tabular-nums" style={{ color }}>
                {showText ? formatValue(bar.value) : ""}
              </span>

              {aside && (
                <span className="shrink-0" style={{ width: aside.width }}>
                  {aside.render(bar, active)}
                </span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
