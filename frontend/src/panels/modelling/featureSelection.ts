import { configField } from "../../utils/configField"
import { modelMembership, type InteractionSpec, type Terms } from "./glmTerms"

export type ModellingColumn = { name: string; dtype: string }
export type ModellingAlgorithm = "catboost" | "glm"

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
  const eligibleNames = new Set(eligible.map((column) => column.name))
  if (algorithm === "glm") {
    const terms = configField<Terms>(config, "terms", {})
    const interactions = configField<InteractionSpec[]>(config, "interactions", [])
    return modelMembership(terms, interactions, eligibleNames).inModel
  }
  const excluded = new Set(configField<string[]>(config, "exclude", []))
  return new Set(
    eligible
      .filter((column) => !excluded.has(column.name))
      .map((column) => column.name),
  )
}
