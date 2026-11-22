import type { NestedNumbers, TrainResponse } from "../api/types"
import { STAGE_LABELS, type ModelStage } from "./stageSettings"

export function evidenceLabel(result: TrainResponse): string {
  if (result.diagnostics_set === "final_test") return "Final test"
  if (result.staged?.diagnostics_basis === "out_of_fold") return "OOF validation"
  return result.diagnostics_set === "validation" ? "Validation" : "Development"
}

export function appliedChain(result?: TrainResponse): string {
  const selected = result?.staged?.selected_stage
  const recipe = selected && result?.staged?.stages[selected]?.recipe
  if (!Array.isArray(recipe)) return selected ? STAGE_LABELS[selected] : "No applied model"
  return ["Full GBM", ...recipe.map((step: { stage: ModelStage }) => STAGE_LABELS[step.stage])].join(" → ")
}

export const cellCount = (values: NestedNumbers[]): number => values.reduce<number>((sum, value) => sum + (Array.isArray(value) ? cellCount(value) : 1), 0)

export function tableSummary(result: TrainResponse) {
  if (result.table_summary) return result.table_summary
  const report = result.tboost_tables
  return report ? { factored: report.factored.length, tables: report.tables.map((table) => ({
    term: table.term, features: table.features, order: table.order, cells: cellCount(table.scores),
    shape_digest: JSON.stringify([table.axes, table.scores]),
  })) } : null
}

function comparisonSummary(value: TrainResponse, other: TrainResponse) {
  if (value.table_summary) return value.table_summary
  const stage = value.staged?.selected_stage
  const own = stage && value.staged?.stages[stage]
  const shared = stage && other.staged?.stages[stage]
  // Legacy dense results can borrow the summary of the exact same immutable snapshot.
  if (own?.revision && own.revision === shared?.revision && shared.table_summary) {
    return shared.table_summary as NonNullable<TrainResponse["table_summary"]>
  }
  return tableSummary(value)
}

export function comparison(before: TrainResponse, after: TrainResponse) {
  if (!before.staged?.evaluation_identity || before.staged.evaluation_identity !== after.staged?.evaluation_identity
    || before.diagnostics_set !== after.diagnostics_set) return null
  const oldTables = comparisonSummary(before, after)?.tables
  const newTables = comparisonSummary(after, before)?.tables
  const comparableShapes = oldTables && newTables && newTables.every((table) => {
    const old = oldTables.find((entry) => entry.term === table.term)
    return !old || /^[a-f0-9]{64}$/.test(old.shape_digest) === /^[a-f0-9]{64}$/.test(table.shape_digest)
  })
  const changed = comparableShapes ? newTables.filter((table) => {
    const previous = oldTables.find((entry) => entry.term === table.term)
    return !previous || previous.shape_digest !== table.shape_digest
  }) : []
  return {
    metrics: Object.keys(after.diagnostic_metrics).filter((key) => typeof before.diagnostic_metrics[key] === "number"
      && typeof after.diagnostic_metrics[key] === "number"),
    tables: oldTables && newTables ? [oldTables.length, newTables.length] : null,
    cells: oldTables && newTables ? [oldTables.reduce((n, t) => n + t.cells, 0), newTables.reduce((n, t) => n + t.cells, 0)] : null,
    changedTables: comparableShapes ? changed.length : null,
    factors: [...new Set([...changed, ...(oldTables?.filter((old) => newTables && !newTables.some((t) => t.term === old.term)) ?? [])].flatMap((table) => table.features))],
  }
}

