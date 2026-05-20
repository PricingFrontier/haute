export const OVERVIEW_CARD_DEFINITIONS = [
  {
    key: "dataset_snapshot",
    label: "Dataset Snapshot",
    description: "Rows, source, upstream node, and cached time.",
  },
  {
    key: "schema",
    label: "Schema",
    description: "Field-level types, nulls, distinct counts, min, and max.",
  },
  {
    key: "numeric_summary",
    label: "Numeric Summary",
    description: "Numeric fields only, with distribution, spread, missingness, zeros, and negatives.",
  },
  {
    key: "categorical_summary",
    label: "Categorical Summary",
    description: "Non-numeric fields, distinct counts, and bounded value-count expansion.",
  },
  {
    key: "data_quality",
    label: "Data Quality",
    description: "Missing, constant, negative, and mostly-zero signals.",
  },
] as const

export type OverviewCardDefinition = (typeof OVERVIEW_CARD_DEFINITIONS)[number]
export type OverviewCardKey = OverviewCardDefinition["key"]
export type OverviewConfigKey = OverviewCardKey
export type OverviewConfig = Partial<Record<OverviewConfigKey, boolean>>

export const OVERVIEW_CONFIG_KEYS: readonly OverviewConfigKey[] =
  OVERVIEW_CARD_DEFINITIONS.map((definition) => definition.key)

export function isOverviewCardEnabled(
  overview: OverviewConfig,
  definition: OverviewCardDefinition,
): boolean {
  return overview[definition.key] ?? false
}
