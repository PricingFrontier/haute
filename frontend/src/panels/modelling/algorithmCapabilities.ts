// Family capabilities come from the backend algorithm descriptors. The JSON is
// generated from ``haute.modelling._descriptors.capability_fixture`` and a
// backend test fails when the two differ, so the UI cannot offer a loss,
// task, or tuning option the backend would reject.
import capabilities from "./algorithmCapabilities.json"

export type ModellingTask = "regression" | "classification"

export type AlgorithmCapability = {
  label: string
  tasks: ModellingTask[]
  losses: Partial<Record<ModellingTask, string[]>>
  feature_controls: string[]
  refit_policy: "validation_weighted_rounds" | "fixed_budget" | "none"
  /** ``null`` keeps the family's own parameter contract (CatBoost, the GLM). */
  allowed_params: string[] | null
  reserved_params: string[]
  round_key: string | null
  round_key_aliases: string[]
  validation_only_params: string[]
  suffix: string
  supports_tuning: boolean
  /** Losses whose native objective refuses monotone constraints. */
  monotone_unsupported_losses: string[]
  /** The family can train on a CUDA GPU (``device: "gpu"``, XGBoost). */
  gpu_device: boolean
}

export const ALGORITHM_CAPABILITIES = capabilities as Record<string, AlgorithmCapability>

export type ModellingAlgorithm = keyof typeof capabilities

export function algorithmCapability(algorithm: string): AlgorithmCapability | null {
  return ALGORITHM_CAPABILITIES[algorithm.toLowerCase()] ?? null
}

export function isKnownAlgorithm(algorithm: string): algorithm is ModellingAlgorithm {
  return algorithmCapability(algorithm) !== null
}

/**
 * The round-refitting family whose round key a final-parameter set carries,
 * mirroring the backend's ``refit_descriptor``: a projection writes exactly
 * one family's round key and drops every other spelling.
 */
export function refitCapability(finalParams: Record<string, unknown>): AlgorithmCapability | null {
  const matches = Object.values(ALGORITHM_CAPABILITIES).filter(
    (capability) =>
      capability.refit_policy === "validation_weighted_rounds"
      && capability.round_key !== null
      && capability.round_key in finalParams,
  )
  return matches.length === 1 ? matches[0] : null
}

/**
 * The fixed-budget family (EBM) whose round key a final-parameter set carries:
 * its study refits with the winning parameters unchanged, mirroring the
 * backend's ``tuning_family``.
 */
export function fixedBudgetCapability(finalParams: Record<string, unknown>): AlgorithmCapability | null {
  const matches = Object.values(ALGORITHM_CAPABILITIES).filter(
    (capability) =>
      capability.refit_policy === "fixed_budget"
      && capability.round_key !== null
      && capability.round_key in finalParams,
  )
  return matches.length === 1 ? matches[0] : null
}

/**
 * Every family except the GLM shares the Target / Features / Parameters panes
 * and the JSON params editor; the GLM configures terms and a family instead.
 */
export function usesSharedPanes(algorithm: string): boolean {
  return algorithmCapability(algorithm) !== null && algorithm.toLowerCase() !== "glm"
}

/**
 * Whether the node trains on a GPU: CatBoost through its ``task_type`` param,
 * a GPU-capable family (XGBoost) through the node's ``device``.
 */
export function trainsOnGpu(config: Record<string, unknown>, params: Record<string, unknown>): boolean {
  const algorithm = String(config.algorithm ?? "").toLowerCase()
  if (algorithm === "catboost") return String(params.task_type ?? "").toUpperCase() === "GPU"
  return algorithmCapability(algorithm)?.gpu_device === true && config.device === "gpu"
}

/** Every Haute loss the family supports, across its tasks. */
export function supportedLosses(algorithm: string): Set<string> {
  const capability = algorithmCapability(algorithm)
  return new Set(Object.values(capability?.losses ?? {}).flat())
}
