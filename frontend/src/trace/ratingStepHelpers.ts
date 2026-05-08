import type {
  RatingStepCombinedOutputDetail,
  RatingStepTableDetail,
  TraceNodeDetail,
  TraceStep,
} from "../types/trace"

export function asRatingStepTables(detail: TraceNodeDetail): RatingStepTableDetail[] {
  return Array.isArray(detail.tables) ? detail.tables as RatingStepTableDetail[] : []
}

export function asRatingStepCombinedOutputs(detail: TraceNodeDetail): RatingStepCombinedOutputDetail[] {
  return Array.isArray(detail.combined_outputs) ? detail.combined_outputs as RatingStepCombinedOutputDetail[] : []
}

export function ratingTableStatus(table: RatingStepTableDetail): string | undefined {
  if (typeof table.status === "string" && table.status.length > 0) return table.status
  if (table.default_used) return "default"
  if (table.matched === false) return "no_match"
  if (table.matched === true) return "matched"
  return undefined
}

export function formatRatingStatus(status: string): string {
  return status.replace(/_/g, " ")
}

export function hasRichRatingStepDetail(step: TraceStep | null | undefined): boolean {
  const detail = step?.node_detail
  return Boolean(
    detail?.detail_type === "rating_step" &&
    (Array.isArray(detail.tables) || Array.isArray(detail.combined_outputs)),
  )
}
