/**
 * EBM terms: the model itself, not a post-hoc explanation.
 *
 * An EBM predicts intercept + one additive score per term on the link scale.
 * Main effects are shape functions (a score per category or per value bin,
 * with the missing-value bin first); a pairwise interaction is one surface
 * over two features. Scores are additive term scores, never SHAP values.
 */
import { useState } from "react"
import type { EbmTerm, EbmTermAxis } from "../../api/types"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { CHART_COLORS } from "../../theme/colors"
import {
  ChartEmptyState,
  ChartSvg,
  ResponsiveChart,
  MODELLING_CHART_AXIS_FONT_SIZE as FONT,
  MODELLING_CHART_AXIS_TEXT_COLOR as TEXT,
  MODELLING_CHART_GRID_COLOR as GRID,
} from "./ChartScaffold"
import {
  chartAxisLabel,
  chartDomain,
  chartLabelIndices,
  chartTicks,
  formatChartNumber,
} from "../../utils/chartHelpers"
import { FeatureBrowser } from "./FeatureBrowser"

const SCORE_NOTE =
  "Additive term scores on the model's link scale (log for Poisson, Gamma and Tweedie; " +
  "log-odds for Logloss). The prediction is the intercept plus every term's score."

export function EBMTermsTab({ result }: { result: TrainResult }) {
  const terms = result.ebm_terms ?? []
  const [selected, setSelected] = useState<string | null>(null)
  const [search, setSearch] = useState("")
  if (!terms.length) return <ChartEmptyState>No EBM terms available</ChartEmptyState>
  const active = terms.find((term) => term.term === selected) ?? terms[0]
  return (
    <div className="validation-feature-layout">
      <FeatureBrowser
        features={terms.map((term) => ({ feature: term.term, importance: term.importance }))}
        selected={active.term}
        onSelect={setSelected}
        search={search}
        onSearch={setSearch}
      />
      <div className="min-w-0">
        <div className="validation-chart-title">
          <div>
            <h4 className="validation-feature-heading">{active.term}</h4>
            <p className="validation-chart-description">
              {active.kind === "interaction" ? "Pairwise interaction" : "Main effect"} · importance{" "}
              {formatChartNumber(active.importance)}
            </p>
          </div>
        </div>
        <p className="mb-2 text-xs" style={{ color: TEXT }}>{SCORE_NOTE}</p>
        {active.kind === "interaction" ? (
          <InteractionSurface key={active.term} term={active} />
        ) : active.axes[0].type === "nominal" ? (
          <NominalShape key={active.term} term={active} />
        ) : (
          <ContinuousShape key={active.term} term={active} />
        )}
      </div>
    </div>
  )
}

function mainScores(term: EbmTerm): number[] {
  return (term.scores as number[]).map(Number)
}

function NominalShape({ term }: { term: EbmTerm }) {
  const labels = term.axes[0].labels
  const scores = mainScores(term)
  return (
    <ResponsiveChart>
      {(width) => {
        const [low, high] = chartDomain(scores, true)
        const barWidth = Math.max(1, width * 0.6 - 76)
        const x = (value: number) => 4 + ((value - low) / (high - low)) * (barWidth - 8)
        return (
          <div role="img" aria-label={`Shape function for ${term.term}`}>
            {labels.map((label, i) => (
              <div
                key={label}
                className="grid items-center gap-3 py-2"
                style={{
                  gridTemplateColumns: "minmax(0, 2fr) minmax(0, 3fr)",
                  borderBottom: "1px solid var(--border)",
                }}
              >
                <span className="break-words text-[13px]" style={{ color: "var(--text-secondary)" }}>
                  {label}
                </span>
                <div className="flex min-w-0 items-center gap-2">
                  <svg width={barWidth} height={28} className="shrink-0" aria-hidden="true">
                    <line x1={x(0)} x2={x(0)} y1={2} y2={26} stroke={TEXT} />
                    <rect
                      x={Math.min(x(0), x(scores[i]))}
                      y={6}
                      width={Math.abs(x(scores[i]) - x(0))}
                      height={16}
                      rx={2}
                      fill={scores[i] >= 0 ? "var(--chart-above)" : "var(--chart-below)"}
                      opacity={0.8}
                    />
                  </svg>
                  <span className="text-xs tabular-nums" title={String(scores[i])}>
                    {formatChartNumber(scores[i])}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )
      }}
    </ResponsiveChart>
  )
}

function ContinuousShape({ term }: { term: EbmTerm }) {
  const [active, setActive] = useState<number | null>(null)
  const axis = term.axes[0]
  const all = mainScores(term)
  // The missing bin is shown on its own; the curve covers the value bins.
  const missing = all[0]
  const scores = all.slice(1)
  const labels = axis.labels.slice(1)
  return (
    <>
      <div className="mb-2 text-xs" style={{ color: TEXT }}>
        Missing values score {formatChartNumber(missing)}
      </div>
      <ResponsiveChart>
        {(width) => {
          const left = 68,
            right = 24,
            top = 24,
            bottom = 240,
            height = 280
          const plotWidth = Math.max(1, width - left - right)
          const [low, high] = chartDomain(scores, true)
          const step = plotWidth / Math.max(1, scores.length)
          const x = (index: number) => left + index * step
          const y = (value: number) => bottom - ((value - low) / (high - low)) * (bottom - top)
          const path = scores
            .map((score, i) => `${i ? "L" : "M"}${x(i)},${y(score)} L${x(i + 1)},${y(score)}`)
            .join(" ")
          const indices = chartLabelIndices(scores.length, plotWidth, 120)
          return (
            <ChartSvg width={width} height={height} ariaLabel={`Shape function for ${term.term}`}>
              {chartTicks(low, high).map((value) => (
                <g key={value}>
                  <line x1={left} x2={width - right} y1={y(value)} y2={y(value)} stroke={GRID} />
                  <text x={left - 8} y={y(value) + 4} textAnchor="end" fontSize={FONT} fill={TEXT}>
                    {formatChartNumber(value)}
                  </text>
                </g>
              ))}
              <line x1={left} x2={width - right} y1={y(0)} y2={y(0)} stroke={TEXT} strokeDasharray="3 3" />
              <path d={path} fill="none" stroke={CHART_COLORS.predicted} strokeWidth={2} />
              {scores.map((score, i) => (
                <rect
                  key={i}
                  x={x(i)}
                  y={top}
                  width={Math.max(1, step)}
                  height={bottom - top}
                  fill="transparent"
                  onMouseEnter={() => setActive(i)}
                >
                  <title>{`${labels[i]}: ${score}`}</title>
                </rect>
              ))}
              {[...indices].map((i) => (
                <text key={i} x={x(i) + step / 2} y={bottom + 20} textAnchor="middle" fontSize={FONT} fill={TEXT}>
                  {chartAxisLabel(labels[i], 120)}
                </text>
              ))}
            </ChartSvg>
          )
        }}
      </ResponsiveChart>
      <div className="validation-bin-detail" role="status" aria-live="polite">
        {active === null ? (
          <span>Hover a bin to inspect its score.</span>
        ) : (
          <>
            <strong>
              {term.term}: {labels[active]}
            </strong>
            <span>Score: {scores[active]}</span>
          </>
        )}
      </div>
    </>
  )
}

function surfaceColour(value: number, extent: number): string {
  const share = extent === 0 ? 0 : Math.min(1, Math.abs(value) / extent)
  const channel = value >= 0 ? "var(--chart-above)" : "var(--chart-below)"
  return `color-mix(in srgb, ${channel} ${Math.round(share * 100)}%, transparent)`
}

function InteractionSurface({ term }: { term: EbmTerm }) {
  const [first, second] = term.axes as [EbmTermAxis, EbmTermAxis]
  const raw = (term.scores as number[][]).map((row) => row.map(Number))
  // The longer axis runs down the table so a wide binning scrolls vertically.
  const transpose = second.labels.length > first.labels.length
  const rowsAxis = transpose ? second : first
  const columnsAxis = transpose ? first : second
  const grid = transpose ? second.labels.map((_, j) => raw.map((row) => row[j])) : raw
  const extent = Math.max(0, ...grid.flat().map(Math.abs))
  return (
    <div className="overflow-x-auto">
      <table
        className="validation-value-table"
        aria-label={`Interaction surface for ${term.term}`}
        style={{ fontSize: 11 }}
      >
        <thead>
          <tr>
            <th scope="col">
              {rowsAxis.feature} \ {columnsAxis.feature}
            </th>
            {columnsAxis.labels.map((label) => (
              <th key={label} scope="col" title={label}>
                {chartAxisLabel(label, 70)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {grid.map((row, i) => (
            <tr key={rowsAxis.labels[i]}>
              <th scope="row">{rowsAxis.labels[i]}</th>
              {row.map((value, j) => (
                <td
                  key={j}
                  title={`${rowsAxis.feature} ${rowsAxis.labels[i]}, ${columnsAxis.feature} ${columnsAxis.labels[j]}: ${value}`}
                  style={{ background: surfaceColour(value, extent), textAlign: "right" }}
                  className="tabular-nums"
                >
                  {formatChartNumber(value)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
