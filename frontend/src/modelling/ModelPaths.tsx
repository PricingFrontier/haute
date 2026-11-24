import { useCallback, useEffect } from "react"
import { ChartEmptyState, ChartSvg, ChartValueGrid, ChartYAxisTitle, ResponsiveChart } from "../panels/modelling/ChartScaffold"
import { chartDomain, chartTicks, formatChartNumber } from "../utils/chartHelpers"
import { useRequestScope } from "../panels/useGraph"
import { prepareCanvasRequest } from "../utils/requestGateway"
import useSettingsStore from "../stores/useSettingsStore"
import { useModelResultKey } from "./modelRuns"
import { useStagedTraining, useStagedViews } from "./useStagedTraining"
import { availablePaths, preparedPath, selectedCandidate, selectedPathPoint, isAppliedPathPoint } from "./pathSelection"
import { type PostFitStage } from "./stageSettings"
import type { StagePathPayload, StagePathPointPayload } from "../generated/api-contracts.generated"
import PathCandidateSummary from "./PathCandidateSummary"
import { PruningPath } from "./PruningPath"
import { StageButtons } from "./StageButtons"

const labels: Record<PostFitStage, string> = { pruned: "Pruning", banded: "Banding", smoothed: "Smoothing" }
function pointLabel(path: StagePathPayload, point: StagePathPointPayload): string {
  if (point.identity) return "Keep unchanged"
  if (path.stage === "pruned") return `${point.diagnostics.table_budget} tables`
  if (path.stage === "banded") return `${Number(point.diagnostics.budget_fraction) * 100}% of budget`
  return path.controls.alpha == null ? `${point.diagnostics.strength} × Auto` : `Alpha ${point.settings.alpha}`
}

function lossScale(path: StagePathPayload) {
  const baseline = path.points.find((point) => point.identity)!.loss
  const percent = Math.abs(baseline) > 1e-12
  const scale = percent ? 100 / Math.abs(baseline) : 1
  const best = Math.min(...path.points.filter((point) => point.status !== "rejected").map((point) => point.loss))
  const tolerance = Math.max(path.selection.absolute_tolerance,
    path.selection.relative_tolerance * Math.max(Math.abs(baseline), Math.abs(best)))
  return { percent, scale, ceiling: (Math.min(baseline, best) + tolerance - baseline) * scale }
}
function readable(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumSignificantDigits: 3 }).format(value)
}
function lossDescription(path: StagePathPayload, point: StagePathPointPayload): string {
  const { percent, scale } = lossScale(path)
  const change = point.loss_delta * scale
  return change === 0 ? "Loss unchanged" : `${readable(Math.abs(change))}${percent ? "%" : ""} ${change < 0 ? "lower" : "higher"} loss${percent ? "" : " (absolute)"}`
}

export function PathChart({ path, selected, applied, onSelect }: {
  path: StagePathPayload; selected?: string; applied?: string; onSelect: (point: string) => void
}) {
  const xValue = (point: StagePathPointPayload) => path.stage === "smoothed"
    ? point.identity ? 0 : Number(point.diagnostics.strength)
    : path.stage === "banded" ? point.identity ? 0 : Number(point.diagnostics.budget_fraction) * 100
    : Number(point.diagnostics.tables)
  const { percent, scale, ceiling } = lossScale(path)
  const value = (point: StagePathPointPayload) => point.loss_delta * scale
  const points = path.points
  return <ResponsiveChart>{(width) => {
    const left = 90, right = width - 22, top = 30, bottom = 238
    const [lo, hi] = chartDomain([...points.map(value), ceiling], true)
    const [xmin, xmax] = path.stage === "banded" ? [0, 100] : chartDomain(points.map(xValue))
    const x = (amount: number) => left + (amount - xmin) / (xmax - xmin) * Math.max(right - left, 1)
    const y = (amount: number) => bottom - (amount - lo) / (hi - lo) * (bottom - top)
    const ordered = [...points].sort((a, b) => xValue(a) - xValue(b))
    const line = ordered.filter((point, index) => index === 0
      || xValue(point) !== xValue(ordered[index - 1]) || point.loss_delta !== ordered[index - 1].loss_delta)
    return <ChartSvg width={width} height={292} ariaLabel={`${labels[path.stage]} path`}>
      <rect x={left} y={y(ceiling)} width={Math.max(0, right - left)} height={bottom - y(ceiling)} fill="var(--success)" opacity={.035} />
      <ChartValueGrid ticks={chartTicks(lo, hi)} left={left} right={right} y={y} labelGap={8} />
      <ChartYAxisTitle plotTop={top} plotHeight={bottom - top}>Loss change ({percent ? "%" : "absolute"})</ChartYAxisTitle>
      <line x1={left} x2={right} y1={y(0)} y2={y(0)} stroke="var(--text-muted)" strokeDasharray="3 4" opacity={.6} />
      <line x1={left} x2={right} y1={y(ceiling)} y2={y(ceiling)} stroke="var(--success)" strokeDasharray="5 4" opacity={.7} />
      <text x={right} y={18} textAnchor="end" fontSize={10} fill="var(--text-muted)">Selection ceiling: {readable(ceiling)}{percent ? "%" : " (absolute)"}</text>
      <path d={line.map((point, index) => `${index ? "L" : "M"}${x(xValue(point))},${y(value(point))}`).join(" ")}
        fill="none" stroke="var(--model-accent)" strokeWidth={1.8} opacity={.65} />
      {ordered.map((point) => <g key={point.id}>
        {point.id === path.suggested_point && <circle cx={x(xValue(point))} cy={y(value(point))} r={9} fill="none" stroke="var(--model-accent)" strokeDasharray="2 2" />}
        {point.id === applied && <circle cx={x(xValue(point))} cy={y(value(point))} r={12} fill="none" stroke="var(--success)" />}
        <circle cx={x(xValue(point))} cy={y(value(point))} r={point.id === selected ? 5 : 3.5}
          fill={point.status === "rejected" ? "var(--text-muted)" : point.id === selected ? "var(--text-primary)" : "var(--model-accent)"} />
        <circle cx={x(xValue(point))} cy={y(value(point))} r={14} fill="transparent" tabIndex={0} role="button"
          aria-label={`${pointLabel(path, point)}, ${lossDescription(path, point)}, ${point.status}`}
          onClick={() => onSelect(point.id)} onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(point.id) }
          }} style={{ cursor: "pointer" }} />
      </g>)}
      {(path.stage === "banded" ? [0, 25, 50, 75, 100] : chartTicks(xmin, xmax)).map((tick) => <text key={tick} x={x(tick)} y={bottom + 20} textAnchor="middle" fontSize={11} fill="var(--text-muted)">{formatChartNumber(tick)}</text>)}
      <text x={(left + right) / 2} y={285} textAnchor="middle" fontSize={11} fill="var(--text-muted)">{path.stage === "pruned" ? "Retained tables" : path.stage === "banded" ? "Banding budget (% of maximum)" : path.controls.alpha == null ? "Strength relative to each table’s Auto proposal" : "Multiplier of manual alpha"}</text>
    </ChartSvg>
  }}</ResponsiveChart>
}

export default function ModelPaths({ nodeId, modelId, readOnly }: {
  nodeId: string; modelId: string; readOnly: boolean
}) {
  const scope = useRequestScope()
  const source = useSettingsStore((state) => state.activeSource)
  const request = useCallback(() => {
    const target = prepareCanvasRequest(scope, nodeId)
    return { graph: target.graph, node_id: target.nodeId, model: modelId, source }
  }, [scope, nodeId, modelId, source])
  const resultKey = useModelResultKey(nodeId, modelId)
  const view = useStagedTraining({ enabled: false, resultKey, request })
  const paths = availablePaths(view)
  const current = view.state?.current?.result.response
  const appliedStage = current?.staged?.selected_stage
  const preferred = preparedPath(view)?.path.stage
  const stage = view.pathStage ?? preferred ?? (appliedStage && appliedStage !== "full" ? appliedStage : "pruned")
  const available = paths[stage]
  const path = available?.path
  const selection = selectedPathPoint(view)
  const selectedId = selection?.available.path.id === path?.id ? selection?.pointId : path?.selected_point
  const selected = path?.points.find((point) => point.id === selectedId)
  const selectedApplied = isAppliedPathPoint(current, path, selectedId)
  const candidate = selectedCandidate(view)
  const currentSnapshot = current?.staged?.stages[stage]
  const applied = path?.points.find((point) => point.revision === currentSnapshot?.revision)?.id
  const busy = view.busy || view.state?.job?.status === "running" || view.state?.next_preview?.status === "queued"
  const inspect = view.inspect
  useEffect(() => {
    if (!busy && available && selection?.available.path.id !== available.path.id) {
      void inspect(available, available.path.selected_point)
    }
  }, [busy, available, selection?.available.path.id, inspect])
  const select = (point: string) => { if (available) void view.inspect(available, point) }
  const activate = (value: PostFitStage) => {
    useStagedViews.getState().update(view.key, { pathStage: value })
    const next = paths[value]
    if (next) void view.inspect(next, next.path.selected_point)
  }
  return <div className="space-y-5">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <StageButtons label="Model paths" value={stage} onChange={activate}
        options={(Object.keys(labels) as PostFitStage[]).map((key) => ({ key, label: labels[key] }))} />
      {path && <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>{path.evidence === "out_of_fold" ? "OOF validation" : path.evidence === "validation" ? "Validation" : "Development rows"} · {path.metric.replaceAll("_", " ")} · lower is better</span>}
    </div>
    {!path ? <ChartEmptyState>{current ? `Run the preceding enabled stage to prepare ${labels[stage].toLowerCase()}.` : "Train the GBM to explore its paths."}</ChartEmptyState> : <>
      <PathChart path={path} selected={selectedId} applied={applied} onSelect={select} />
      <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>Zero means unchanged loss; below zero is better. The ceiling marks your chosen trade-off, not statistical significance.</p>
      {stage === "banded" && <p className="text-[11px]" style={{ color: "var(--text-secondary)" }}>Higher budgets allow more simplification, up to the noise tolerance and deviance cap in Parameters.</p>}
      <div className="flex flex-wrap gap-2" aria-label="Path points">{path.points.map((point) => <button key={point.id} type="button"
        className="focus-ring rounded-lg border px-3 py-2 text-left text-[11px] transition-colors" onClick={() => select(point.id)} aria-pressed={point.id === selectedId}
        style={{ borderColor: point.id === selectedId ? "var(--model-accent)" : "var(--border)", background: point.id === selectedId ? "var(--model-accent-soft)" : "transparent" }}>
        <span className="block font-medium">{pointLabel(path, point)}</span>
        <span className="mt-1 block" style={{ color: "var(--text-muted)" }}>{point.id === path.suggested_point ? "Suggested · " : ""}{point.id === applied ? "Applied · " : ""}{point.id === selectedId ? "Selected · " : ""}{point.status === "rejected" ? "Rejected" : point.status === "unchanged" ? "No change" : lossDescription(path, point)}</span>
      </button>)}</div>
      <div className="flex flex-wrap items-start justify-between gap-4 rounded-xl border p-4" style={{ borderColor: "var(--border)", background: "var(--bg-panel)" }}>
        <div className="space-y-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>
          <p className="font-medium">{selected ? pointLabel(path, selected) : "Select a candidate"}</p>
          {selected && <p>{lossDescription(path, selected)}</p>}
          <p>{view.inspection?.loading ? "Loading selected result…" : selected?.status === "rejected" ? "The training guard rejected this change in at least one retained fit. Rejected fits keep their predecessor; the chart includes all retained fits." : selected?.status === "unchanged" ? "This setting produced no change." : selected?.identity ? "Keep the predecessor and disable this phase." : "Inspect this candidate in Factor or Tables, then apply it."}</p>
          {typeof selected?.diagnostics.equivalent_to === "string" && <p>Same outcome as {pointLabel(path, path.points.find((point) => point.id === selected.diagnostics.equivalent_to)!)} · applying keeps this setting’s own refit policy.</p>}
          {path.evidence === "development" ? <p>Training rows only · no validation recommendation.</p> : <p>Suggested within {path.selection.relative_tolerance * 100}% loss tolerance of both the best candidate and the unchanged model.</p>}
          {view.inspection?.error && <p role="status">{view.inspection.error}</p>}
          {view.error && <p role="status">{view.error}</p>}
        </div>
        <button type="button" className="focus-ring rounded-lg px-4 py-2 text-[11px] font-medium disabled:opacity-35"
          style={{ background: "var(--model-accent-soft)", color: "var(--model-accent)" }}
          disabled={readOnly || busy || Boolean(view.state?.stale_reason) || view.inspection?.loading || Boolean(view.inspection?.error)
            || selectedApplied || !selected || selected.status === "rejected" || selection?.available.path.id !== path.id}
          onClick={() => void view.applyPoint()}>{selectedApplied ? "Applied" : selected?.identity ? "Keep unchanged" : "Apply selected"}</button>
      </div>
      {stage !== "pruned" && candidate && !view.inspection?.loading && !view.inspection?.error && <PathCandidateSummary
        key={`${path.id}:${selectedId}`} stage={stage} parent={path.parent} candidate={candidate} />}
      {stage === "pruned" && <details className="text-[11px]"><summary className="cursor-pointer">Training OOB evidence · before finalisation</summary><div className="pt-4"><PruningPath result={candidate ?? current} selected={selected?.identity ? null : Number(selected?.diagnostics.table_budget)} applied={null} onSelect={() => {}} readOnly /></div></details>}
    </>}
  </div>
}
