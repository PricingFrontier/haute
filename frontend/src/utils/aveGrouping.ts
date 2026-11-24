import type { AveGrouping, TrainAvePerFeatureRow } from "../api/types"

export const AVE_GROUPINGS: readonly AveGrouping[] = ["each value", "equal exposure", "in order", "A–Z", "by exposure"]

/**
 * The factor chart's caption: the grouping drawn, counted without Missing and Other.
 * A run saved before groupings were reported falls back to the factor's type.
 */
export function aveGroupingCaption(row: Pick<TrainAvePerFeatureRow, "type" | "grouping" | "bins">): string {
  const groups = row.bins.filter((bin) => bin.label !== "Missing" && !bin.label.startsWith("Other (")).length
  switch (row.grouping) {
    case "each value": return `Each value (${groups})`
    case "equal exposure": return `${groups} groups · equal exposure`
    case "in order": return `${groups} levels · in order`
    case "A–Z": return `${groups} levels · A–Z`
    case "by exposure": return `Top ${groups} levels · by exposure`
    case undefined: return row.type
  }
}
