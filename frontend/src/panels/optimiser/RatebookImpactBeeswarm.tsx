/**
 * The Summary tab's ratebook beeswarm: every level of the factors that move
 * rates most, placed by its log rate effect and coloured by the level's value
 * where its label has one. It draws at the pane's pixel width, says when it
 * shows only the top factors, and names categorical levels in words.
 */
import { useMemo, useState } from "react"
import { ChartLegend, ChartSvg, ChartValuesTable, ResponsiveChart } from "../modelling/ChartScaffold"
import {
  factorRateSpread,
  formatRate,
  formatVsNeutral,
  logRate,
  type FactorTables,
} from "./ratebookFactorTables"

interface RatebookImpactBeeswarmProps {
  factorTables: FactorTables | null | undefined
  /** A fixed pixel width; the pane's measured width when omitted. */
  width?: number
}

type ImpactDot = {
  key: string
  factor: string
  level: string
  rate: number
  effect: number
  quotes: number
  valuePosition: number | null
}

type FactorImpact = {
  factor: string
  dots: ImpactDot[]
}

type EffectDirection = "decreasing" | "neutral" | "increasing"

// ── Layout (pixels) ──────────────────────────────────────────────
const MARGIN_TOP = 34
const MARGIN_BOTTOM = 44
const ROW_GAP = 38
const LABEL_GAP = 16
/** The factor labels take this share of the width, within these bounds. */
const LABEL_AREA_SHARE = 0.24
const MIN_LABEL_AREA = 88
const MAX_LABEL_AREA = 154
/** Space right of the plot for the colour bar and its captions. */
const NARROW_BELOW = 480
const MARGIN_RIGHT_NARROW = 72
const MARGIN_RIGHT_WIDE = 92
/** The approximate advance of a 13px semibold factor label character. */
const LABEL_CHAR_WIDTH = 7.5

// ── Dot rendering & jitter ───────────────────────────────────────
const DOT_RADIUS = 3.4
const DOT_X_BUCKET = 10
const STACK_STEP = 4.2
const STACK_MAX_OFFSET = 16
const STACK_PATTERN = [0, -1, 1, -2, 2, -3, 3, -4, 4]

// ── Data caps ────────────────────────────────────────────────────
const TOP_FACTORS = 8

// ── Theme tokens (resolved from CSS variables) ───────────────────
const VALUE_HIGH_COLOR = "var(--chart-impact-value-high)"
const VALUE_LOW_COLOR = "var(--chart-impact-value-low)"
const VALUE_NEUTRAL_COLOR = "var(--chart-impact-value-neutral)"
const PLOT_BG = "var(--chart-impact-plot-bg)"
const LABEL_COLOR = "var(--chart-impact-label)"
const MUTED_COLOR = "var(--chart-impact-muted)"
const GRID_COLOR = "var(--chart-impact-grid)"
const AXIS_COLOR = "var(--chart-impact-axis)"

// ── DOM identifiers ──────────────────────────────────────────────
const VALUE_GRADIENT_ID = "ratebook-impact-value-gradient"

// ── Colour bar ───────────────────────────────────────────────────
const COLOR_BAR_WIDTH = 8
const COLOR_BAR_INSET = 40

const CATEGORICAL_NOTE = "No value order (categorical level)"

function parseImpacts(factorTables: FactorTables | null | undefined): FactorImpact[] {
  if (!factorTables) return []

  return Object.entries(factorTables)
    .filter(([, rows]) => rows.length > 0)
    .map(([factor, rows]) => {
      const values = rows.map((row) => numericFeatureValue(row.__factor_group__))
      const numericValues = values.filter((value): value is number => value != null)
      const minValue = numericValues.length > 0 ? Math.min(...numericValues) : null
      const maxValue = numericValues.length > 0 ? Math.max(...numericValues) : null
      const hasValueRange = minValue != null && maxValue != null && maxValue > minValue
      const dots = rows.map((row, index) => {
        const value = values[index]
        return {
          key: `${factor}\u0000${row.__factor_group__}`,
          factor,
          level: row.__factor_group__,
          rate: row.optimal_scenario_value,
          effect: logRate(factor, row),
          quotes: row.quote_count,
          valuePosition: hasValueRange && value != null ? (value - minValue) / (maxValue - minValue) : null,
        }
      })
      return { factor, spread: factorRateSpread(factor, rows), dots }
    })
    .sort((a, b) => b.spread - a.spread || a.factor.localeCompare(b.factor))
    .map(({ factor, dots }) => ({ factor, dots }))
}

function numericFeatureValue(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) return null

  const normalisedText = trimmed.replace(/−(?=\d)/g, "-")
  const exactText = normalisedText.replace(/[$,]/g, "")
  const exactMatch = exactText.match(/^[<>]=?\s*([+-]?\d+(?:\.\d+)?)%?\+?$/)
  if (exactMatch) {
    const parsed = Number(exactMatch[1])
    return Number.isFinite(parsed) ? parsed : null
  }

  const rangeText = normalisedText
    .replace(/([+-]?\d+(?:\.\d+)?)\s*[-‐‑‒–—−]\s*([+-]?\d+(?:\.\d+)?)/g, "$1 to $2")
    .replace(/[$,]/g, "")
  if (!/\b(?:to|through|up to)\b/i.test(rangeText)) return null

  const rangeValues = [...rangeText.matchAll(/[+-]?\d+(?:\.\d+)?/g)]
    .map((match) => Number(match[0]))
    .filter(Number.isFinite)
  if (rangeValues.length < 2) return null

  return (rangeValues[0] + rangeValues[rangeValues.length - 1]) / 2
}

function effectTickLabel(effect: number): string {
  const pct = (Math.exp(effect) - 1) * 100
  const sign = pct > 0 ? "+" : ""
  return `${sign}${pct.toFixed(0)}%`
}

function clippedText(value: string, maxLength: number): string {
  return value.length <= maxLength ? value : `${value.slice(0, maxLength - 1)}…`
}

function directionForRate(rate: number): EffectDirection {
  if (rate > 1) return "increasing"
  if (rate < 1) return "decreasing"
  return "neutral"
}

function colorForValuePosition(valuePosition: number | null): string {
  if (valuePosition == null) return VALUE_NEUTRAL_COLOR

  const highPct = Math.round(valuePosition * 100)
  const lowPct = 100 - highPct
  return `color-mix(in srgb, ${VALUE_LOW_COLOR} ${lowPct}%, ${VALUE_HIGH_COLOR} ${highPct}%)`
}

function valuePositionLabel(valuePosition: number | null): string {
  return valuePosition == null ? "unknown" : valuePosition.toFixed(2)
}

/** What a dot's colour says, in words. */
function colourMeaning(valuePosition: number | null): string {
  if (valuePosition == null) return CATEGORICAL_NOTE.toLowerCase()
  const end = valuePosition < 1 / 3 ? "low end" : valuePosition > 2 / 3 ? "high end" : "middle"
  return `factor value, ${end} (${valuePosition.toFixed(2)} of low 0 to high 1)`
}

function stackOffset(bucketCounts: Map<number, number>, x: number): number {
  const bucket = Math.round(x / DOT_X_BUCKET)
  const count = bucketCounts.get(bucket) ?? 0
  bucketCounts.set(bucket, count + 1)
  const lane = STACK_PATTERN[count % STACK_PATTERN.length]
  const spill = Math.floor(count / STACK_PATTERN.length) * 0.55
  const direction = lane === 0 ? 0 : Math.sign(lane)
  const offset = (lane + direction * spill) * STACK_STEP
  return Math.max(-STACK_MAX_OFFSET, Math.min(STACK_MAX_OFFSET, offset))
}

export default function RatebookImpactBeeswarm({ factorTables, width }: RatebookImpactBeeswarmProps) {
  const allFactors = useMemo(() => parseImpacts(factorTables), [factorTables])
  const [showAll, setShowAll] = useState(false)
  const [activeKey, setActiveKey] = useState<string | null>(null)
  if (allFactors.length === 0) return null

  const truncates = allFactors.length > TOP_FACTORS
  const factors = showAll ? allFactors : allFactors.slice(0, TOP_FACTORS)
  const shownDots = factors.flatMap((factor) => factor.dots)
  const active = shownDots.find((dot) => dot.key === activeKey) ?? null
  const hasCategorical = shownDots.some((dot) => dot.valuePosition == null)

  return (
    <section className="min-w-0 flex-1 basis-80" aria-label="Mechanical Price Effect">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <h3 className="m-0 text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
          Mechanical Price Effect
        </h3>
        {truncates ? (
          <div className="flex items-center gap-2 text-[10px]" style={{ color: "var(--text-muted)" }}>
            <span>
              {showAll
                ? `Showing all ${allFactors.length} factors`
                : `Showing top ${TOP_FACTORS} of ${allFactors.length} factors`}
            </span>
            <div className="flex gap-1" role="group" aria-label="Factors shown">
              {([["Top 8", false], ["All", true]] as const).map(([label, value]) => (
                <button
                  key={label}
                  type="button"
                  aria-pressed={showAll === value}
                  onClick={() => setShowAll(value)}
                  className="px-2 py-0.5 rounded font-medium focus-ring"
                  style={{
                    background: showAll === value ? "var(--accent-soft)" : "var(--chrome-hover)",
                    color: showAll === value ? "var(--accent)" : "var(--text-muted)",
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
            {allFactors.length} factors
          </span>
        )}
      </div>

      <ResponsiveChart width={width} className="mt-1">
        {(chartWidth) => (
          <BeeswarmSvg factors={factors} width={chartWidth} activeKey={activeKey} onActivate={setActiveKey} />
        )}
      </ResponsiveChart>

      {hasCategorical && (
        <ChartLegend compact items={[{ label: CATEGORICAL_NOTE, color: VALUE_NEUTRAL_COLOR, swatch: "bar" }]} />
      )}

      <div className="validation-bin-detail" role="status" aria-live="polite">
        {active ? (
          <>
            <strong>{`${active.factor} ${active.level}`}</strong>
            <span>Rate: {formatRate(active.rate)}</span>
            <span>vs neutral 1.0: {formatVsNeutral(active.rate)}</span>
            <span>Quotes: {active.quotes.toLocaleString()}</span>
            <span>Colour: {colourMeaning(active.valuePosition)}</span>
          </>
        ) : (
          <span>Hover or focus a dot to inspect its level.</span>
        )}
      </div>

      <ChartValuesTable
        summary="View effect values"
        ariaLabel="Mechanical price effect values"
        headers={["Factor", "Level", "Rate", "vs neutral 1.0 (%)", "Quotes"]}
        rows={shownDots.map((dot) => [
          dot.factor,
          dot.level,
          formatRate(dot.rate),
          formatVsNeutral(dot.rate),
          dot.quotes.toLocaleString(),
        ])}
      />
    </section>
  )
}

function BeeswarmSvg({
  factors,
  width,
  activeKey,
  onActivate,
}: {
  factors: FactorImpact[]
  width: number
  activeKey: string | null
  onActivate: (key: string) => void
}) {
  const allEffects = factors.flatMap((factor) => factor.dots.map((dot) => dot.effect))
  const rawMaxAbs = Math.max(...allEffects.map((effect) => Math.abs(effect)))
  const maxAbs = Math.max(rawMaxAbs * 1.15, 0.05)
  const marginLeft = Math.min(MAX_LABEL_AREA, Math.max(MIN_LABEL_AREA, width * LABEL_AREA_SHARE))
  const marginRight = width < NARROW_BELOW ? MARGIN_RIGHT_NARROW : MARGIN_RIGHT_WIDE
  const chartW = Math.max(1, width - marginLeft - marginRight)
  const labelChars = Math.max(4, Math.floor((marginLeft - LABEL_GAP) / LABEL_CHAR_WIDTH))
  const height = MARGIN_TOP + MARGIN_BOTTOM + Math.max(1, factors.length - 1) * ROW_GAP
  const xScale = (effect: number) => marginLeft + ((effect + maxAbs) / (maxAbs * 2)) * chartW
  const zeroX = xScale(0)
  const ticks = [-maxAbs, -maxAbs / 2, 0, maxAbs / 2, maxAbs]
  const colorBarX = width - COLOR_BAR_INSET
  const captionX = colorBarX + COLOR_BAR_WIDTH + 8
  const rotatedX = colorBarX + COLOR_BAR_WIDTH + 28
  const rotatedY = MARGIN_TOP + (Math.max(0, factors.length - 1) * ROW_GAP) / 2

  return (
    <ChartSvg
      data-testid="ratebook-impact-beeswarm"
      width={width}
      height={height}
      style={{ background: PLOT_BG, borderRadius: 4, border: "1px solid var(--border)" }}
    >
      <defs>
        <linearGradient id={VALUE_GRADIENT_ID} x1="0" y1="1" x2="0" y2="0">
          <stop offset="0%" stopColor={VALUE_LOW_COLOR} />
          <stop offset="100%" stopColor={VALUE_HIGH_COLOR} />
        </linearGradient>
      </defs>
      <line
        x1={zeroX}
        y1={12}
        x2={zeroX}
        y2={height - MARGIN_BOTTOM + 10}
        stroke={AXIS_COLOR}
        strokeWidth={2}
        opacity={0.85}
      />

      {ticks.map((tick) => (
        <text key={tick} x={xScale(tick)} y={height - 16} textAnchor="middle" fontSize={11} fill={MUTED_COLOR}>
          {effectTickLabel(tick)}
        </text>
      ))}

      {factors.map((factor, rowIndex) => {
        const y = MARGIN_TOP + rowIndex * ROW_GAP
        const bucketCounts = new Map<number, number>()
        const sortedDots = [...factor.dots].sort((a, b) => a.effect - b.effect)
        return (
          <g key={factor.factor}>
            <line
              x1={marginLeft}
              y1={y}
              x2={width - marginRight}
              y2={y}
              stroke={GRID_COLOR}
              strokeWidth={1}
              strokeDasharray="1,5"
              opacity={0.85}
            />
            <g>
              <title>{factor.factor}</title>
              <text
                data-testid="ratebook-impact-factor"
                x={marginLeft - LABEL_GAP}
                y={y + 5}
                textAnchor="end"
                fontSize={13}
                fontWeight={600}
                fill={LABEL_COLOR}
              >
                {clippedText(factor.factor, labelChars)}
              </text>
            </g>
            {sortedDots.map((dot) => {
              const x = xScale(dot.effect)
              const cy = y + stackOffset(bucketCounts, x)
              const label = `${dot.factor} ${dot.level}: ${formatVsNeutral(dot.rate)}`
              const activate = () => onActivate(dot.key)
              return (
                <circle
                  key={dot.key}
                  role="button"
                  tabIndex={0}
                  aria-label={label}
                  aria-pressed={dot.key === activeKey}
                  data-impact-direction={directionForRate(dot.rate)}
                  data-factor-value-position={valuePositionLabel(dot.valuePosition)}
                  cx={x}
                  cy={cy}
                  r={dot.key === activeKey ? DOT_RADIUS + 1.6 : DOT_RADIUS}
                  fill={colorForValuePosition(dot.valuePosition)}
                  stroke={dot.key === activeKey ? LABEL_COLOR : undefined}
                  opacity={0.88}
                  className="focus-ring"
                  onFocus={activate}
                  onMouseEnter={activate}
                  onClick={activate}
                >
                  <title>{label}</title>
                </circle>
              )
            })}
          </g>
        )
      })}

      <text x={marginLeft + chartW / 2} y={height - 2} textAnchor="middle" fontSize={12} fill={MUTED_COLOR}>
        Log rate effect
      </text>

      <rect
        data-testid="ratebook-impact-colour-bar"
        x={colorBarX}
        y={MARGIN_TOP - 14}
        width={COLOR_BAR_WIDTH}
        height={height - MARGIN_TOP - MARGIN_BOTTOM + 28}
        fill={`url(#${VALUE_GRADIENT_ID})`}
      />
      <text x={captionX} y={MARGIN_TOP - 8} fontSize={10} fill={MUTED_COLOR}>
        High
      </text>
      <text x={captionX} y={height - MARGIN_BOTTOM + 18} fontSize={10} fill={MUTED_COLOR}>
        Low
      </text>
      <text
        x={rotatedX}
        y={rotatedY}
        fontSize={10}
        fill={MUTED_COLOR}
        textAnchor="middle"
        transform={`rotate(90 ${rotatedX} ${rotatedY})`}
      >
        Factor value
      </text>
    </ChartSvg>
  )
}
