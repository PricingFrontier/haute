/**
 * Modelling export settings describe where a trained model is published, not
 * how it is trained or what the pipeline computes. They are kept out of the
 * training identity and out of the graph's structural fingerprint, so editing
 * them neither marks a trained result stale nor re-requests the RAM estimate.
 */
export const MODELLING_NODE_TYPE = "modelling"

export const MODELLING_EXPORT_CONFIG_KEYS = [
  "mlflow_destination",
  "mlflow_experiment",
  "model_export_path",
] as const

/** The node config minus its export settings: what a trained result depends on. */
export function trainingIdentityConfig(config: Record<string, unknown>): Record<string, unknown> {
  const identity = { ...config }
  for (const key of MODELLING_EXPORT_CONFIG_KEYS) delete identity[key]
  return identity
}
