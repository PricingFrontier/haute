import { useMemo } from "react"
import {
  formatFactorLevel,
  numericQuoteCount,
  numericRate,
  type FactorTables,
} from "./ratebookFactorTables"

interface RatebookImpactBeeswarmProps {
  factorTables: FactorTables | null | undefined
}

type ImpactDot = {
  factor: string
  level: string
  rate: number
  effect: number
  weight: number
  featureValue: number | null
  valuePosition: number | null
}

type FactorImpact = {
  factor: string
  importance: number
  dots: ImpactDot[]
}

type EffectDirection = "decreasing" | "neutral" | "increasing"

// ── Layout (pixels) ──────────────────────────────────────────────
const WIDTH = 720
const MARGIN_LEFT = 154
const MARGIN_RIGHT = 92
const MARGIN_TOP = 34
const MARGIN_BOTTOM = 44
const ROW_GAP = 38

// ── Dot rendering & jitter ───────────────────────────────────────
const DOT_RADIUS = 3.4
const DOT_X_BUCKET = 10
const STACK_STEP = 4.2
const STACK_MAX_OFFSET = 16
const STACK_PATTERN = [0, -1, 1, -2, 2, -3, 3, -4, 4]

// ── Data caps ────────────────────────────────────────────────────
const MAX_FACTORS = 8

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
const COLOR_BAR_X = WIDTH - 40

function parseImpacts(factorTables: FactorTables | null | undefined): FactorImpact[] {
  if (!factorTables) return []

  return Object.entries(factorTables)
    .map(([factor, rows]) => {
      const rawDots = Array.isArray(rows)
        ? rows.flatMap((row, index) => {
            const rate = numericRate(row)
            if (rate == null || rate <= 0) return []
            const level = formatFactorLevel(row, index)
            return [{
              factor,
              level,
              rate,
              effect: Math.log(rate),
              weight: numericQuoteCount(row) ?? 1,
              featureValue: numericFeatureValue(row[factor]) ?? numericFeatureValue(level),
              valuePosition: null,
            }]
          })
        : []
      const numericValues = rawDots.flatMap((dot) => (
        dot.featureValue == null ? [] : [dot.featureValue]
      ))
      const minValue = numericValues.length > 0 ? Math.min(...numericValues) : null
      const maxValue = numericValues.length > 0 ? Math.max(...numericValues) : null
      const hasValueRange = minValue != null && maxValue != null && maxValue > minValue
      const dots = rawDots.map((dot) => ({
        ...dot,
        valuePosition: hasValueRange && dot.featureValue != null
          ? (dot.featureValue - minValue) / (maxValue - minValue)
          : null,
      }))
      const totalWeight = dots.reduce((total, dot) => total + dot.weight, 0)
      const importance = dots.length === 0 || totalWeight <= 0
        ? 0
        : dots.reduce((total, dot) => total + Math.abs(dot.effect) * dot.weight, 0) / totalWeight
      return { factor, importance, dots }
    })
    .filter((impact) => impact.dots.length > 0)
    .sort((a, b) => b.importance - a.importance || a.factor.localeCompare(b.factor))
    .slice(0, MAX_FACTORS)
}

function numericFeatureValue(value: unknown): number | null {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null
  }
  if (typeof value !== "string") return null

  const trimmed = value.trim()
  if (!trimmed) return null

  const normalisedText = trimmed.replace(/\u2212(?=\d)/g, "-")
  const exactText = normalisedText.replace(/[$,]/g, "")
  const exactMatch = exactText.match(/^[<>]=?\s*([+-]?\d+(?:\.\d+)?)%?\+?$/)
  if (exactMatch) {
    const parsed = Number(exactMatch[1])
    return Number.isFinite(parsed) ? parsed : null
  }

  const rangeText = normalisedText
    .replace(/([+-]?\d+(?:\.\d+)?)\s*[-\u2010\u2011\u2012\u2013\u2014\u2212]\s*([+-]?\d+(?:\.\d+)?)/g, "$1 to $2")
    .replace(/[$,]/g, "")
  if (!/\b(?:to|through|up to)\b/i.test(rangeText)) return null

  const rangeValues = [...rangeText.matchAll(/[+-]?\d+(?:\.\d+)?/g)]
    .map((match) => Number(match[0]))
    .filter(Number.isFinite)
  if (rangeValues.length < 2) return null

  return (rangeValues[0] + rangeValues[rangeValues.length - 1]) / 2
}

function signedPercent(rate: number): string {
  const pct = (rate - 1) * 100
  const sign = pct > 0 ? "+" : ""
  return `${sign}${pct.toFixed(1)}%`
}

function effectTickLabel(effect: number): string {
  const pct = (Math.exp(effect) - 1) * 100
  const sign = pct > 0 ? "+" : ""
  return `${sign}${pct.toFixed(0)}%`
}

function clippedText(value: string, maxLength = 22): string {
  return value.length <= maxLength ? value : `${value.slice(0, maxLength - 1)}...`
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

export default function RatebookImpactBeeswarm({
  factorTables,
}: RatebookImpactBeeswarmProps) {
  const factors = useMemo(() => parseImpacts(factorTables), [factorTables])
  if (factors.length === 0) return null

  const allEffects = factors.flatMap((factor) => factor.dots.map((dot) => dot.effect))
  const rawMaxAbs = Math.max(...allEffects.map((effect) => Math.abs(effect)))
  const maxAbs = Math.max(rawMaxAbs * 1.15, 0.05)
  const chartW = WIDTH - MARGIN_LEFT - MARGIN_RIGHT
  const height = MARGIN_TOP + MARGIN_BOTTOM + Math.max(1, factors.length - 1) * ROW_GAP
  const xScale = (effect: number) => MARGIN_LEFT + ((effect + maxAbs) / (maxAbs * 2)) * chartW
  const zeroX = xScale(0)
  const ticks = [-maxAbs, -maxAbs / 2, 0, maxAbs / 2, maxAbs]

  return (
    <section className="min-w-[520px] flex-1" aria-label="Mechanical Price Effect">
      <div className="flex items-center justify-between gap-3">
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
          Mechanical Price Effect
        </label>
        <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
          {factors.length} factors
        </span>
      </div>

      <svg
        data-testid="ratebook-impact-beeswarm"
        width={WIDTH}
        height={height}
        className="mt-1 max-w-full"
        viewBox={`0 0 ${WIDTH} ${height}`}
        style={{ background: PLOT_BG, borderRadius: 4, border: "1px solid var(--border)" }}
      >
        <defs>
          <linearGradient id={VALUE_GRADIENT_ID} x1="0" y1="1" x2="0" y2="0">
            <stop offset="0%" stopColor={VALUE_LOW_COLOR} />
            <stop offset="100%" stopColor={VALUE_HIGH_COLOR} />
          </linearGradient>
        </defs>
        <rect x={0} y={0} width={WIDTH} height={height} fill={PLOT_BG} rx={4} />
        <line
          x1={zeroX}
          y1={12}
          x2={zeroX}
          y2={height - MARGIN_BOTTOM + 10}
          stroke={AXIS_COLOR}
          strokeWidth={2}
          opacity={0.85}
        />

        {ticks.map((tick) => {
          const x = xScale(tick)
          return (
            <text
              key={tick}
              x={x}
              y={height - 16}
              textAnchor="middle"
              fontSize={11}
              fill={MUTED_COLOR}
            >
              {effectTickLabel(tick)}
            </text>
          )
        })}

        {factors.map((factor, rowIndex) => {
          const y = MARGIN_TOP + rowIndex * ROW_GAP
          const bucketCounts = new Map<number, number>()
          const sortedDots = [...factor.dots].sort((a, b) => a.effect - b.effect)
          return (
            <g key={factor.factor}>
              <line
                x1={MARGIN_LEFT}
                y1={y}
                x2={WIDTH - MARGIN_RIGHT}
                y2={y}
                stroke={GRID_COLOR}
                strokeWidth={1}
                strokeDasharray="1,5"
                opacity={0.85}
              />
              <text
                data-testid="ratebook-impact-factor"
                x={MARGIN_LEFT - 16}
                y={y + 5}
                textAnchor="end"
                fontSize={13}
                fontWeight={600}
                fill={LABEL_COLOR}
              >
                {clippedText(factor.factor)}
              </text>
              {sortedDots.map((dot, index) => {
                const x = xScale(dot.effect)
                const cy = y + stackOffset(bucketCounts, x)
                const direction = directionForRate(dot.rate)
                const label = `${dot.factor} ${dot.level}: ${signedPercent(dot.rate)}`
                return (
                  <circle
                    key={`${dot.factor}-${dot.level}-${index}`}
                    role="img"
                    aria-label={label}
                    data-impact-direction={direction}
                    data-factor-value-position={valuePositionLabel(dot.valuePosition)}
                    cx={x}
                    cy={cy}
                    r={DOT_RADIUS}
                    fill={colorForValuePosition(dot.valuePosition)}
                    opacity={0.88}
                  >
                    <title>{label}</title>
                  </circle>
                )
              })}
            </g>
          )
        })}

        <text
          x={MARGIN_LEFT + chartW / 2}
          y={height - 2}
          textAnchor="middle"
          fontSize={12}
          fill={MUTED_COLOR}
        >
          Log rate effect
        </text>

        <rect
          x={COLOR_BAR_X}
          y={MARGIN_TOP - 14}
          width={COLOR_BAR_WIDTH}
          height={height - MARGIN_TOP - MARGIN_BOTTOM + 28}
          fill={`url(#${VALUE_GRADIENT_ID})`}
        />
        <text
          x={COLOR_BAR_X + COLOR_BAR_WIDTH + 8}
          y={MARGIN_TOP - 8}
          fontSize={10}
          fill={MUTED_COLOR}
        >
          High
        </text>
        <text
          x={COLOR_BAR_X + COLOR_BAR_WIDTH + 8}
          y={height - MARGIN_BOTTOM + 18}
          fontSize={10}
          fill={MUTED_COLOR}
        >
          Low
        </text>
        <text
          x={COLOR_BAR_X + COLOR_BAR_WIDTH + 28}
          y={MARGIN_TOP + Math.max(0, factors.length - 1) * ROW_GAP / 2}
          fontSize={10}
          fill={MUTED_COLOR}
          textAnchor="middle"
          transform={`rotate(90 ${COLOR_BAR_X + COLOR_BAR_WIDTH + 28} ${MARGIN_TOP + Math.max(0, factors.length - 1) * ROW_GAP / 2})`}
        >
          Factor value
        </text>
      </svg>
    </section>
  )
}
