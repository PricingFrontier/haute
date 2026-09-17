import { configField } from "../../utils/configField"

export type ModellingColumn = { name: string; dtype: string }

/**
 * Columns with an active modelling role, mapped to that role. Role columns
 * cannot also be trainable features or GLM terms. Mirrors the backend
 * `_training_metadata_reasons` precedence.
 */
export function roleColumnReasons(config: Record<string, unknown>): Map<string, string> {
  const reasons = new Map<string, string>()
  const add = (value: unknown, role: string) => {
    if (typeof value === "string" && value !== "" && !reasons.has(value)) reasons.set(value, role)
  }
  add(config.target, "target")
  add(config.weight, "weight")
  add(config.offset, "offset")
  add(config.fold_column, "fold")
  for (const column of Array.isArray(config.id_columns) ? config.id_columns : []) add(column, "identifier")
  const evaluation = configField<Record<string, unknown>>(config, "evaluation", {})
  const strategy = configField(evaluation, "strategy", "random")
  if (strategy === "temporal") add(evaluation.date_column, "evaluation")
  if (strategy === "group") add(evaluation.group_column, "evaluation")
  return reasons
}

/** Columns with an active modelling role cannot also be trainable features. */
export function roleColumns(config: Record<string, unknown>): Set<string> {
  return new Set(roleColumnReasons(config).keys())
}

/** CatBoost's final feature selection: eligible columns that are not excluded. */
export function finalSelectedFeatureNames(
  config: Record<string, unknown>,
  eligible: readonly ModellingColumn[],
): Set<string> {
  const excluded = new Set(configField<string[]>(config, "exclude", []))
  return new Set(eligible.filter((column) => !excluded.has(column.name)).map((column) => column.name))
}
