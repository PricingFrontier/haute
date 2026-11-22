import type { TrainResponse } from "../api/types"
import { formatMetricValue, metricLabel } from "../panels/modelling/metricOptions"
import { comparison, evidenceLabel, tableSummary } from "./stageEvidence"
import { stageResult } from "./stageResults"
import { STAGE_LABELS, type ModelStage } from "./stageSettings"

const linkClass = "hover-chrome focus-ring rounded px-1 py-1 text-[11px] underline underline-offset-4 disabled:opacity-35"
export function StageDecision({ current, candidate, stage, metric, onCompare, onFactor }: {
  current: TrainResponse; candidate: TrainResponse; stage: ModelStage; metric?: string
  onCompare?: (stage: ModelStage, parent: ModelStage) => void; onFactor?: (factor: string) => void
}) {
  const snapshot = candidate.staged?.stages[stage]
  const parent = snapshot?.parent as ModelStage | undefined
  const before = parent && stageResult(current, parent)
  const after = stageResult(candidate, stage)
  if (!before || !after || !parent) return null
  const delta = comparison(before, after)
  if (!delta) return <p className="text-[11px]">These results use different evaluation rows; a numerical comparison is unavailable.</p>
  const primary = metric && delta.metrics.includes(metric) ? metric : delta.metrics[0]
  const row = (key: string) => {
    const a = before.diagnostic_metrics[key] as number, b = after.diagnostic_metrics[key] as number
    return <div key={key} className="space-y-1">
      <div>{metricLabel(key)} <span style={{ color: "var(--text-muted)" }}>· {["auc", "gini", "r2"].includes(key) ? "higher" : "lower"} is better</span></div>
      <div className="font-mono">{formatMetricValue(a)} → {formatMetricValue(b)} <span style={{ color: "var(--text-muted)" }}>({b - a >= 0 ? "+" : ""}{(b - a).toPrecision(3)})</span></div>
    </div>
  }
  return <div className="space-y-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
    <p>{evidenceLabel(after)} · compared with {STAGE_LABELS[parent]}</p>
    {primary && row(primary)}
    {delta.metrics.length > 1 && <details><summary className="cursor-pointer">Other metrics</summary><div className="space-y-2 pt-2">{delta.metrics.filter((key) => key !== primary).map(row)}</div></details>}
    {delta.tables && <p>Reported tables {delta.tables[0]} → {delta.tables[1]} · Cells {delta.cells![0]} → {delta.cells![1]}</p>}
    {stage === "smoothed" && delta.tables && <p>{delta.changedTables === null ? "Shape comparison unavailable for this older result" : `${delta.changedTables} table shapes changed`}</p>}
    {(tableSummary(after)?.factored ?? 0) > 0 && <p style={{ color: "var(--text-muted)" }}>Factored effects are not included in these cell counts.</p>}
    {after.evaluation && <details><summary className="cursor-pointer">Evaluation details</summary><p className="pt-2">{after.evaluation.strategy} split · {after.evaluation.development_rows.toLocaleString()} development rows · {after.evaluation.validation_fit_count} validation fits</p><p>Held-out rows per fit: {after.evaluation.selection_fits.map((fit) => fit.validation_rows.toLocaleString()).join(", ") || "none"}</p></details>}
    {after.staged?.reference_fit === "development" && <p style={{ color: "var(--text-muted)" }}>Complexity describes the development reference fit.</p>}
    <div className="flex flex-wrap gap-x-3">
      {onCompare && <button type="button" className={linkClass} onClick={() => onCompare(stage, parent)}>Compare with parent</button>}
      {onCompare && parent !== "full" && <button type="button" className={linkClass} onClick={() => onCompare(stage, "full")}>Compare with full</button>}
    </div>
    {onFactor && delta.factors.length > 0 && <details><summary className="cursor-pointer">Inspect changed factors</summary><div className="flex flex-wrap gap-2 pt-2">{delta.factors.map((factor) => <button key={factor} type="button" className={linkClass} onClick={() => onFactor(factor)}>{factor}</button>)}</div></details>}
  </div>
}
