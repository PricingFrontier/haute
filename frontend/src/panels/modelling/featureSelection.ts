import { configField } from "../../utils/configField"

export type ModellingColumn = { name: string; dtype: string }
export type ModellingAlgorithm = "catboost" | "glm"

type Interaction = { factors?: string[]; [key: string]: unknown }

/** Columns with an active modelling role cannot also be trainable features. */
export function roleColumns(config: Record<string, unknown>): Set<string> {
  const evaluation = configField<Record<string, unknown>>(
    config,
    "evaluation",
    {},
  )
  const evaluationStrategy = configField(
    evaluation,
    "strategy",
    "random",
  )
  const values: unknown[] = [
    config.target,
    config.weight,
    config.offset,
    config.fold_column,
    ...(Array.isArray(config.id_columns) ? config.id_columns : []),
  ]

  if (evaluationStrategy === "temporal") values.push(evaluation.date_column)
  if (evaluationStrategy === "group") values.push(evaluation.group_column)

  return new Set(
    values.filter(
      (value): value is string => typeof value === "string" && value !== "",
    ),
  )
}

export function finalSelectedFeatureNames(
  config: Record<string, unknown>,
  eligible: readonly ModellingColumn[],
  algorithm: ModellingAlgorithm,
): Set<string> {
  const excluded = new Set(configField<string[]>(config, "exclude", []))
  const terms = configField<Record<string, unknown>>(config, "terms", {})
  const allFactors = configField(config, "all_factors", false)

  return new Set(
    eligible
      .filter((column) => {
        if (excluded.has(column.name)) return false
        if (algorithm === "catboost" || allFactors) return true
        return Object.hasOwn(terms, column.name)
      })
      .map((column) => column.name),
  )
}

/**
 * Return only the dependent fields changed by removing selected features.
 * The caller merges this object into the same atomic config update.
 */
export function cleanupFeatureDependencies(
  config: Record<string, unknown>,
  removed: readonly string[],
): Record<string, unknown> {
  const removedSet = new Set(removed)
  if (removedSet.size === 0) return {}

  const update: Record<string, unknown> = {}

  const monotone = configField<Record<string, number>>(
    config,
    "monotone_constraints",
    {},
  )
  if (Object.keys(monotone).some((name) => removedSet.has(name))) {
    const nextMonotone = Object.fromEntries(
      Object.entries(monotone).filter(([name]) => !removedSet.has(name)),
    )
    update.monotone_constraints =
      Object.keys(nextMonotone).length > 0 ? nextMonotone : null
  }

  const terms = configField<Record<string, unknown>>(config, "terms", {})
  if (Object.keys(terms).some((name) => removedSet.has(name))) {
    update.terms = Object.fromEntries(
      Object.entries(terms).filter(([name]) => !removedSet.has(name)),
    )
  }

  const interactions = configField<Interaction[]>(config, "interactions", [])
  const nextInteractions = interactions.filter(
    (interaction) =>
      !interaction.factors?.some((factor) => removedSet.has(factor)),
  )
  if (nextInteractions.length !== interactions.length) {
    update.interactions = nextInteractions
  }

  return update
}
