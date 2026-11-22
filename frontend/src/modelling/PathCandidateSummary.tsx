import { useState } from "react"
import type { TrainAvePerFeatureRow, TrainResponse } from "../api/types"
import { ChartLegend, ChartSvg, ResponsiveChart } from "../panels/modelling/ChartScaffold"
import { chartAxisLabel, chartDomain, chartLabelIndices, formatChartNumber, formatChartTicks } from "../utils/chartHelpers"
import { tableSummary, evidenceLabel } from "./stageEvidence"
import { stageResult } from "./stageResults"
import type { ModelStage } from "./stageSettings"

const BEFORE = "var(--text-muted)", AFTER = "var(--model-accent)"
const PAGE_SIZE = 6
const number = (value: number) => new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value)
function Legend() {
  return <ChartLegend compact items={[
    { label: "Before", color: BEFORE, dashArray: "4 3" }, { label: "Selected", color: AFTER },
  ]} />
}

function BandingSummary({ before, after }: { before: TrainResponse; after: TrainResponse }) {
  const previous = tableSummary(before), selected = tableSummary(after)
  if (!previous || !selected) return <p>Table diagnostics unavailable for this comparison.</p>
  const counts = [previous, selected].map((report) => report.tables.filter((table) => table.order > 1).map((table) => table.cells))
  const totals = counts.map((values) => values.reduce((sum, count) => sum + count, 0))
  const largest = Math.max(...counts.flat(), 0)
  const bands: { label: string; before: number; selected: number }[] = []
  for (let lower = 1, upper = 4; lower <= largest; lower = upper + 1, upper *= 4) {
    const frequency = counts.map((values) => values.filter((count) => count >= lower && count <= upper).length)
    bands.push({ label: `${number(lower)}–${number(upper)}`, before: frequency[0], selected: frequency[1] })
  }
  const maximum = Math.max(...bands.flatMap((band) => [band.before, band.selected]), 1)
  const change = totals[0] > 0 && totals[1] !== totals[0]
    ? ` · ${number(Math.abs(totals[1] - totals[0]) / totals[0] * 100)}% ${totals[1] < totals[0] ? "fewer" : "more"}` : ""
  return <div className="space-y-2">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div><h4 className="font-medium" style={{ color: "var(--text-primary)" }}>Interaction table sizes</h4>
        <p className="mt-1">{number(totals[0])} → {number(totals[1])} cells{change}</p></div><Legend />
    </div>
    {largest === 0 ? <p>No materialised interaction tables to compare.</p> : <ResponsiveChart>{(width) => {
      const left = 30, right = width - 16, top = 20, bottom = 120
      const step = (right - left) / bands.length, bar = Math.min(20, step / 3)
      const y = (value: number) => bottom - value / maximum * (bottom - top)
      return <ChartSvg width={width} height={162} ariaLabel="Interaction table cell counts">
        <text x={left} y={11} fontSize={10} fill={BEFORE}>Tables</text>
        <line x1={left} x2={right} y1={bottom} y2={bottom} stroke="var(--border)" />
        {bands.map((band, i) => {
          const x = left + (i + .5) * step
          return <g key={band.label} aria-label={`${band.label} cells: before ${band.before} tables, selected ${band.selected} tables`}>
            <title>{band.label} cells: before {band.before} tables, selected {band.selected} tables</title>
            <rect x={x - bar - 1} y={y(band.before)} width={bar} height={bottom - y(band.before)} fill={BEFORE} opacity={.35} rx={2} />
            <rect x={x + 1} y={y(band.selected)} width={bar} height={bottom - y(band.selected)} fill={AFTER} opacity={.8} rx={2} />
            <text x={x - bar / 2 - 1} y={y(band.before) - 4} textAnchor="middle" fontSize={10} fill={BEFORE}>{band.before}</text>
            <text x={x + bar / 2 + 1} y={y(band.selected) - 4} textAnchor="middle" fontSize={10} fill={AFTER}>{band.selected}</text>
            <text x={x} y={bottom + 16} textAnchor="middle" fontSize={10} fill={BEFORE}>{chartAxisLabel(band.label, step)}</text>
          </g>
        })}
        <text x={(left + right) / 2} y={158} textAnchor="middle" fontSize={10} fill={BEFORE}>Cells per interaction table</text>
      </ChartSvg>
    }}</ResponsiveChart>}
    <p>{counts[0].length} → {counts[1].length} materialised interaction tables · reference fit</p>
    {Boolean(previous.factored || selected.factored) && <p>Factored effects excluded: {previous.factored} before, {selected.factored} selected · no cell grid.</p>}
  </div>
}

function compatible(before: TrainAvePerFeatureRow, after: TrainAvePerFeatureRow): boolean {
  return before.type === after.type && before.bins.length === after.bins.length && before.bins.every((bin, i) => {
    const next = after.bins[i]
    return bin.label === next.label && bin.exposure === next.exposure && bin.avg_actual === next.avg_actual
  })
}
function FactorMiniChart({ before, after }: { before: TrainAvePerFeatureRow; after: TrainAvePerFeatureRow }) {
  return <figure className="min-w-0 rounded-lg border px-3 py-2" style={{ borderColor: "var(--border)" }}>
    <figcaption className="truncate font-medium" title={after.feature} style={{ color: "var(--text-primary)" }}>{after.feature}</figcaption>
    <ResponsiveChart>{(width) => {
      const left = 48, right = width - 14, top = 14, bottom = 98
      const [low, high] = chartDomain([...before.bins, ...after.bins].map((bin) => bin.avg_predicted))
      const limits = formatChartTicks([low, high])
      const step = Math.max(1, right - left) / after.bins.length
      const x = (i: number) => left + (i + .5) * step
      const y = (value: number) => bottom - (value - low) / (high - low) * (bottom - top)
      const labels = chartLabelIndices(after.bins.length, right - left, 70)
      const numeric = after.type === "numeric"
      const line = (row: TrainAvePerFeatureRow) => row.bins.map((bin, i) => bin.label === "Missing" ? ""
        : `${i === 0 || row.bins[i - 1].label === "Missing" ? "M" : "L"}${x(i)},${y(bin.avg_predicted)}`).join(" ")
      return <ChartSvg width={width} height={132} ariaLabel={`Average prediction for ${after.feature}`}>
        {[low, high].map((value, i) => <g key={value}>
          <text x={left - 6} y={y(value) + 3} textAnchor="end" fontSize={9} fill={BEFORE}>{limits[i]}</text>
          <line x1={left} x2={right} y1={y(value)} y2={y(value)} stroke="var(--border)" />
        </g>)}
        {numeric && <>
          <path data-series="before" d={line(before)} fill="none" stroke={BEFORE} strokeDasharray="4 3" strokeWidth={1.5} />
          <path data-series="selected" d={line(after)} fill="none" stroke={AFTER} strokeWidth={1.8} />
        </>}
        {after.bins.map((bin, i) => {
          const old = before.bins[i].avg_predicted
          const values = old === bin.avg_predicted ? [formatChartNumber(old), formatChartNumber(old)] : formatChartTicks([old, bin.avg_predicted])
          return <g key={bin.label}>
          <title>{bin.label}: before {values[0]}, selected {values[1]}</title>
          <circle cx={x(i)} cy={y(before.bins[i].avg_predicted)} r={3} fill="var(--bg-panel)" stroke={BEFORE} />
          <circle cx={x(i)} cy={y(bin.avg_predicted)} r={2} fill={AFTER} />
          {labels.has(i) && <text x={x(i)} y={bottom + 18} textAnchor="middle" fontSize={9} fill={BEFORE}>{chartAxisLabel(bin.label, Math.max(step, 65))}</text>}
        </g>})}
      </ChartSvg>
    }}</ResponsiveChart>
  </figure>
}

function SmoothingSummary({ before, after }: { before: TrainResponse; after: TrainResponse }) {
  const [page, setPage] = useState(0)
  const oldFactors = new Map(before.ave_per_feature.map((row) => [row.feature, row]))
  const newFactors = new Map(after.ave_per_feature.map((row) => [row.feature, row]))
  const rows = after.features.map((feature) => {
    const old = oldFactors.get(feature), next = newFactors.get(feature)
    if (!old || !next || !old.bins.length || !next.bins.length) return { feature, issue: "saved averages unavailable" }
    if (!compatible(old, next)) return { feature, issue: "before/selected bins differ" }
    return { feature, old, next, change: Math.max(...next.bins.map((bin, i) => Math.abs(bin.avg_predicted - old.bins[i].avg_predicted))) }
  }).sort((a, b) => (b.change ?? -1) - (a.change ?? -1) || a.feature.localeCompare(b.feature))
  const start = Math.min(page * PAGE_SIZE, Math.max(0, Math.ceil(rows.length / PAGE_SIZE) - 1) * PAGE_SIZE)
  return <div className="space-y-3">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div><h4 className="font-medium" style={{ color: "var(--text-primary)" }}>Average prediction by factor</h4>
        <p className="mt-1">{evidenceLabel(after)} averages · largest changes first</p></div><Legend />
    </div>
    <p>Grouped predictions, not PDPs · each factor has its own scale.</p>
    {!rows.length ? <p>No fitted factors to summarise.</p> : <>
      <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 230px), 1fr))" }}>
        {rows.slice(start, start + PAGE_SIZE).map((row) => row.old && row.next
          ? <FactorMiniChart key={row.feature} before={row.old} after={row.next} />
          : <p key={row.feature} className="rounded-lg border p-3" style={{ borderColor: "var(--border)" }}>{row.feature} · {row.issue}</p>)}
      </div>
      {rows.length > PAGE_SIZE && <div className="flex items-center justify-between gap-2">
        <span>{start + 1}–{Math.min(start + PAGE_SIZE, rows.length)} of {rows.length} factors</span>
        <div className="flex gap-2">{[-1, 1].map((direction) => <button key={direction} type="button"
          className="focus-ring hover-chrome rounded px-2 py-1 disabled:opacity-35"
          aria-label={direction < 0 ? "Previous factors" : "Next factors"}
          disabled={direction < 0 ? start === 0 : start + PAGE_SIZE >= rows.length}
          onClick={() => setPage(start / PAGE_SIZE + direction)}>{direction < 0 ? "Previous" : "Next"}</button>)}</div>
      </div>}
    </>}
  </div>
}

export default function PathCandidateSummary({ stage, parent, candidate }: {
  stage: "banded" | "smoothed"; parent: ModelStage; candidate: TrainResponse
}) {
  const before = stageResult(candidate, parent)
  return <section aria-label="Selected candidate summary" className="rounded-xl border p-4 text-[11px]" style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}>
    {!before ? <p>Predecessor diagnostics unavailable for this comparison.</p>
      : before.diagnostics_set !== candidate.diagnostics_set || before.staged?.evaluation_identity !== candidate.staged?.evaluation_identity
        ? <p>Before and selected diagnostics use different evaluation rows.</p>
        : stage === "banded" ? <BandingSummary before={before} after={candidate} /> : <SmoothingSummary before={before} after={candidate} />}
  </section>
}
