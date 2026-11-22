import { useState } from "react"
import { CHART_COLORS } from "../../theme/colors"
import { IterationLinesChart, type IterationLineSeries } from "../IterationLinesChart"
import { ChartEmptyState } from "./ChartScaffold"
import type { ShownFit } from "./lossHistory"

/** Each bag keeps its own x coordinates while parallel fits report out of order. */
export function BagLossChart({ fit, width, height }: { fit: ShownFit; width: number; height: number }) {
  const [split, setSplit] = useState<"train" | "eval">("eval")
  const [hidden, setHidden] = useState<string[]>([])
  const keys = new Set(fit.history.flatMap(Object.keys))
  const bags = [...keys].filter((key) => /^bag_\d+_iteration$/.test(key))
    .map((key) => Number(key.split("_")[1])).sort((a, b) => a - b)
  const hasEval = bags.some((bag) => keys.has(`bag_${bag}_eval_deviance`))
  const chosen = hasEval ? split : "train"
  const lines: IterationLineSeries[] = bags.map((bag) => {
    const points = new Map<number, number>()
    for (const row of fit.history) {
      const round = row[`bag_${bag}_iteration`]
      const value = row[`bag_${bag}_${chosen}_deviance`]
      if (Number.isFinite(round) && Number.isFinite(value)) points.set(round, value)
    }
    const ordered = [...points].sort(([a], [b]) => a - b)
    return { label: `Bag ${bag}`, color: CHART_COLORS.optimiserSeries[(bag - 1) % CHART_COLORS.optimiserSeries.length],
      x: ordered.map(([round]) => round), values: ordered.map(([, value]) => value), opacity: 0.7 }
  })
  const mean: IterationLineSeries = { label: "Mean of bags", color: "var(--text-primary)", strokeWidth: 2.5,
    values: fit.history.map((row) => Number.isFinite(row[`${chosen}_deviance`]) ? row[`${chosen}_deviance`] : null) }
  const available = [...lines, ...(!fit.live ? [mean] : [])]
  const shown = available.filter((line) => !hidden.includes(line.label) && line.values.some((value) => value !== null))
  // The fastest bag may report its actual last round while the overall counter is capped.
  const x = fit.history.map((row) => row.iteration)
  const domain = [...x, ...lines.flatMap((line) => line.x ?? [])]
  const domainMin = Math.min(...domain)
  const domainMax = Math.max(...domain)
  const ticks = [...new Set(Array.from({ length: 5 }, (_, index) => Math.round(domainMin + (domainMax - domainMin) * index / 4)))]
  return <div className="space-y-3">
    <div className="flex items-center justify-between gap-3">
      <div role="group" aria-label="Loss data" className="flex gap-1 rounded-md p-1" style={{ background: "var(--bg-elevated)" }}>
        {(["train", ...(hasEval ? ["eval"] : [])] as const).map((value) => <button key={value} type="button"
          aria-pressed={chosen === value} onClick={() => setSplit(value as "train" | "eval")}
          className="rounded px-3 py-1 text-[12px] focus-ring"
          style={{ color: chosen === value ? "var(--text-primary)" : "var(--text-muted)", background: chosen === value ? "var(--bg-panel)" : undefined }}>
          {value === "train" ? "Training" : "Validation"}
        </button>)}
      </div>
      <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>{bags.length} bags · lower is better</span>
    </div>
    {shown.length ? <IterationLinesChart x={ticks} numericX series={shown.map((line) => ({ ...line, x: line.x ?? x }))}
      width={width} height={height} title="Bag loss by iteration" ariaLabel="Loss history chart"
      xLabel="Iteration" yLabel="Deviance" showLegend={false} /> : <ChartEmptyState>Select a curve below</ChartEmptyState>}
    <div role="group" aria-label="Visible curves" className="flex flex-wrap gap-x-4 gap-y-2">
      {available.map((line) => <label key={line.label} className="flex cursor-pointer items-center gap-1.5 text-[11px]" style={{ color: "var(--text-secondary)" }}>
        <input type="checkbox" checked={!hidden.includes(line.label)} style={{ accentColor: line.color }}
          onChange={(event) => setHidden((current) => event.target.checked ? current.filter((name) => name !== line.label) : [...current, line.label])} />
        <span aria-hidden="true" className="inline-block h-0.5 w-3 rounded" style={{ background: line.color }} />{line.label}
      </label>)}
    </div>
    <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
      {chosen === "eval" && "Validation uses each bag’s own held-back rows. "}
      {fit.live ? "Each line follows one bag at its own iteration. The mean appears when fitting finishes."
        : "Lines end when each bag stops. The mean keeps stopped bags at their final loss; it is not the ensemble’s prediction loss."}
    </p>
  </div>
}
