/** Project saved stage diagnostics onto the existing chart result contract, once per snapshot. */
import type { TrainResponse } from "../api/types"
import { parseTrainResponse } from "../types/trainGuards"
import type { ModelStage } from "./stageSettings"

const parsed = new WeakMap<object, TrainResponse>()
export function stageResult(result: TrainResponse, stage: ModelStage): TrainResponse | null {
  const snapshot = result.staged?.stages[stage]
  if (!snapshot) return null
  const cached = parsed.get(snapshot)
  if (cached) return cached
  const next = parseTrainResponse({
    ...result,
    staged: { ...result.staged, selected_stage: stage },
    diagnostic_metrics: snapshot.metrics,
    feature_importance: snapshot.feature_importance,
    features: snapshot.features,
    cat_features: snapshot.cat_features,
    diagnostics_set: snapshot.diagnostics_set,
    ave_per_feature: snapshot.ave_per_feature,
    ave_left_out: snapshot.ave_left_out,
    tboost_tables: snapshot.tboost_tables,
    table_summary: snapshot.table_summary,
    loss_history: snapshot.loss_history,
    fit_evidence: snapshot.fit_evidence,
    diagnostics_errors: snapshot.diagnostics_errors,
  })
  next.loss_history_truncated = next.loss_history.length < (next.loss_history.at(-1)?.iteration ?? 0)
  parsed.set(snapshot, next)
  return next
}

export function defaultVisibleStages(result: TrainResponse | undefined): ModelStage[] {
  const selected = result?.staged?.selected_stage
  return selected && selected !== "full" ? ["full", selected] : ["full"]
}
export const STAGE_DASHES: Record<ModelStage, string | undefined> = {
  full: undefined, pruned: "7 4", banded: "2 3", smoothed: "9 3 2 3",
}
