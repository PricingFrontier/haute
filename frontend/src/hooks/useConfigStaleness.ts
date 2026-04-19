/**
 * Shared staleness detection for config panels.
 *
 * Both ModellingConfig and OptimiserConfig need to decide whether a cached
 * result (training or solve) is still valid for the *current* node config.
 * The derivation was identical in both panels:
 *
 *     const currentConfigHash = useMemo(() => hashConfig(config), [config])
 *     const isStale =
 *       !!cachedResult && cachedResult.configHash !== currentConfigHash
 *
 * This hook centralises that derivation and also gives `useConfigEstimate`
 * a stable hash to watch — so a config change drives both the staleness
 * banner and the RAM-estimate refetch through one computation.
 */
import { useMemo } from "react"
import { hashConfig } from "../stores/useNodeResultsStore"

export interface UseConfigStalenessResult {
  /** A stable hash of the config (for passing to `useConfigEstimate` and
   *  for writing alongside future cached results). */
  configHash: string
  /** True when a cached result exists but its hash no longer matches the
   *  current config — i.e. the user has edited something that invalidates
   *  the cached run. */
  isStale: boolean
}

/**
 * Derive the current config hash and compare it against any cached result.
 *
 * @param config        The full config object for the current node.
 * @param cachedResult  The store-backed cached result for this node, if any.
 *                      Only the `configHash` field is consulted — callers
 *                      pass their full result shape and we don't require a
 *                      common type across panels.
 */
export function useConfigStaleness(
  config: Record<string, unknown>,
  cachedResult: { configHash: string } | null | undefined,
): UseConfigStalenessResult {
  const configHash = useMemo(() => hashConfig(config), [config])
  const isStale = !!cachedResult && cachedResult.configHash !== configHash
  return { configHash, isStale }
}
