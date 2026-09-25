/**
 * Publish settings describe where a trained model or a solved optimisation is
 * published, not how it is trained or solved or what the pipeline computes.
 * They are kept out of the training/solve identity and out of the graph's
 * structural fingerprint, so editing them neither marks a result stale nor
 * re-requests the RAM or size estimate.
 */
export const MODELLING_NODE_TYPE = "modelling"
export const OPTIMISER_NODE_TYPE = "optimiser"

export const MODELLING_EXPORT_CONFIG_KEYS = [
  "mlflow_destination",
  "mlflow_experiment",
  "model_export_path",
] as const

export const OPTIMISER_EXPORT_CONFIG_KEYS = [
  "mlflow_destination",
  "mlflow_experiment",
  "result_export_path",
] as const

/** The publish-only config keys of a node type, or none. */
export function exportConfigKeysFor(nodeType: unknown): readonly string[] {
  if (nodeType === MODELLING_NODE_TYPE) return MODELLING_EXPORT_CONFIG_KEYS
  if (nodeType === OPTIMISER_NODE_TYPE) return OPTIMISER_EXPORT_CONFIG_KEYS
  return []
}

function withoutKeys(config: Record<string, unknown>, keys: readonly string[]): Record<string, unknown> {
  const identity = { ...config }
  for (const key of keys) delete identity[key]
  return identity
}

/** The node config minus its export settings: what a trained result depends on. */
export function trainingIdentityConfig(config: Record<string, unknown>): Record<string, unknown> {
  return withoutKeys(config, MODELLING_EXPORT_CONFIG_KEYS)
}

/** The optimiser config minus its export settings: what a solve result depends on. */
export function solveIdentityConfig(config: Record<string, unknown>): Record<string, unknown> {
  return withoutKeys(config, OPTIMISER_EXPORT_CONFIG_KEYS)
}
