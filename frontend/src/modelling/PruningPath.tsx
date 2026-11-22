import type { TrainResponse } from "../api/types"
import { ChartEmptyState, ChartSvg, ChartValueGrid, ResponsiveChart } from "../panels/modelling/ChartScaffold"
import { chartDomain, chartTicks, formatChartNumber } from "../utils/chartHelpers"

/** OOB evidence has its own labelled chart; it is never validation or reserved-test loss. */
export function PruningPath({ result, selected, applied, onSelect, readOnly }: {
  result: TrainResponse | undefined
  selected: number | null
  applied: number | null
  onSelect: (tables: number) => void
  readOnly: boolean
}) {
  const saved = result?.staged
  const path = saved?.pruning_path
  if (!path) return <ChartEmptyState>{saved?.path_unavailable_reason ?? "Build the GBM to inspect its pruning path"}</ChartEmptyState>
  const counts = path.table_counts as number[]
  const losses = path.oob_deviance as number[]
  const minimum = path.minimum_step as number
  const recommendation = saved?.recommendation?.path as { best: number | null } | undefined
  if (!Array.isArray(counts) || !counts.length || !Array.isArray(losses) || losses.length !== counts.length
    || counts.some((value) => !Number.isInteger(value) || value < 0)
    || losses.some((value) => !Number.isFinite(value))
    || !Number.isInteger(minimum) || minimum < 0 || minimum >= counts.length) {
    throw new Error("The saved pruning path is malformed. Rebuild the GBM.")
  }
  const recommended = recommendation?.best == null ? null : counts[recommendation.best]
  const marks = [
    { label: "OOB minimum", count: counts[minimum], color: "var(--text-secondary)" },
    { label: "Recommended", count: recommended, color: "var(--model-accent)" },
    { label: "Selected", count: selected, color: "var(--warning)" },
    { label: "Applied", count: applied, color: "var(--success)" },
  ]
  return <div className="space-y-4">
    <div className="validation-chart-title"><div><h4 className="validation-feature-heading">Pruning path</h4>
      <p className="validation-chart-description">Training OOB deviance · {String(path.oob_rows)} rows · lower is better{saved?.reference_fit === "development" && saved.diagnostics_basis === "out_of_fold" ? " · development reference fit" : ""}</p></div></div>
    <div className="grid grid-cols-2 gap-3 text-[11px] xl:grid-cols-4">{marks.map((mark) => <div key={mark.label} className="rounded-lg border px-3 py-2" style={{ borderColor: "var(--border)" }}>
      <div style={{ color: "var(--text-muted)" }}>{mark.label}</div><div className="mt-1 font-medium">{mark.count === null ? mark.label === "Selected" ? "Automatic" : "—" : `${mark.count} tables`}</div>
    </div>)}</div>
    <ResponsiveChart>{(width) => {
      const left = 76, right = width - 24, top = 28, bottom = 290
      const [low, high] = chartDomain(losses)
      const max = Math.max(...counts, 1)
      const x = (count: number) => left + count / max * Math.max(right - left, 1)
      const y = (loss: number) => bottom - (loss - low) / (high - low) * (bottom - top)
      const line = losses.map((loss, index) => `${index ? "L" : "M"}${x(counts[index])},${y(loss)}`).join(" ")
      return <ChartSvg width={width} height={340} ariaLabel="Training OOB pruning path">
        <ChartValueGrid ticks={chartTicks(low, high)} left={left} right={right} y={y} labelGap={8} />
        <path d={line} fill="none" stroke="var(--model-accent)" strokeWidth={2} />
        {marks.filter((mark) => mark.count !== null).map((mark, index) => <line key={mark.label}
          x1={x(mark.count!)} x2={x(mark.count!)} y1={top + index * 7} y2={bottom}
          stroke={mark.color} strokeDasharray={`${index + 2} 4`} opacity={.65}><title>{mark.label}: {mark.count} tables</title></line>)}
        {counts.map((count, index) => <g key={count}>
          <circle cx={x(count)} cy={y(losses[index])} r={count === selected ? 5 : 3} fill={count === selected ? "var(--text-primary)" : "var(--model-accent)"} />
          <circle cx={x(count)} cy={y(losses[index])} r={10} fill="transparent" role="button" tabIndex={readOnly ? -1 : 0}
            aria-disabled={readOnly} aria-label={`${count} tables, OOB deviance ${formatChartNumber(losses[index])}`}
            onClick={() => { if (!readOnly) onSelect(count) }} onKeyDown={(event) => {
              if (!readOnly && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); onSelect(count) }
            }} style={{ cursor: readOnly ? "default" : "pointer" }} />
        </g>)}
        {chartTicks(0, max).map((value) => <text key={value} x={x(value)} y={bottom + 20} textAnchor="middle" fontSize={12} fill="var(--text-muted)">{formatChartNumber(value)}</text>)}
        <text x={(left + right) / 2} y={334} textAnchor="middle" fontSize={12} fill="var(--text-muted)">Retained tables</text>
      </ChartSvg>
    }}</ResponsiveChart>
    <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>These OOB scores describe the ranked pruning prefixes before finalisation. The rightmost point retains every table.</p>
  </div>
}
