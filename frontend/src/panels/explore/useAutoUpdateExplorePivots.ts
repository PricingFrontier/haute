import { useEffect, useRef } from "react"

import type { ExploreCacheReport } from "../../api/types"
import useNodeResultsStore, {
  explorePivotResultKey,
} from "../../stores/useNodeResultsStore"
import {
  isPivotResultFresh,
  pivotCalculationIdentity,
  type ExplorePivotConfig,
} from "./pivotConfig"

type UseAutoUpdateExplorePivotsInput = {
  nodeId: string
  pivots: readonly ExplorePivotConfig[]
  report: ExploreCacheReport | null
  submitting: Readonly<Record<string, boolean>>
  updatePivot: (
    pivot: ExplorePivotConfig,
    requestedDataframeCacheKey?: string | null,
    autoClaimToken?: number,
  ) => Promise<void>
}

function automaticAttemptKey(
  nodeId: string,
  pivotId: string,
  dataframeCacheKey: string,
  calculationIdentity: string,
): string {
  return JSON.stringify([
    nodeId,
    pivotId,
    dataframeCacheKey,
    calculationIdentity,
  ])
}

/**
 * Starts each calculation required by the mounted Pivot/Chart consumer once.
 * The execution hook remains the single owner of requests and persisted jobs.
 */
export default function useAutoUpdateExplorePivots({
  nodeId,
  pivots,
  report,
  submitting,
  updatePivot,
}: UseAutoUpdateExplorePivotsInput) {
  const pivotResults = useNodeResultsStore((state) => state.pivotResults)
  const pivotJobs = useNodeResultsStore((state) => state.pivotJobs)
  const claimAuto = useNodeResultsStore((state) => state.claimExplorePivotAuto)
  const attempted = useRef(new Set<string>())

  useEffect(() => {
    if (!report) {
      attempted.current.clear()
      return
    }

    const currentAttempts = new Set<string>()
    const seenPivotIds = new Set<string>()

    for (const pivot of pivots) {
      if (seenPivotIds.has(pivot.id)) continue
      seenPivotIds.add(pivot.id)
      if (pivot.values.length === 0) continue

      const resultKey = explorePivotResultKey(nodeId, pivot.id)
      const cached = pivotResults[resultKey]
      const calculationIdentity = pivotCalculationIdentity(pivot)
      const attemptKey = automaticAttemptKey(
        nodeId,
        pivot.id,
        report.dataframe_cache_key,
        calculationIdentity,
      )
      currentAttempts.add(attemptKey)

      const fresh = isPivotResultFresh(
        cached,
        report.dataframe_cache_key,
        calculationIdentity,
      )
      const failedCurrentAttempt = Boolean(
        cached?.error
          && cached.lastAttemptedCalculationIdentity === calculationIdentity
          && cached.lastAttemptedDataframeCacheKey
            === report.dataframe_cache_key,
      )
      if (fresh || failedCurrentAttempt || attempted.current.has(attemptKey)) {
        continue
      }

      // A running job or in-flight submission blocks a repeat of the same
      // target, but a held claim for a *different* target must be superseded
      // immediately: replacing the claim invalidates the old submission's
      // token so its outcome is discarded, and the backend's latest-wins
      // family key supersedes the older server-side job.
      const heldClaim =
        useNodeResultsStore.getState().pivotAutoClaims[resultKey]
      const supersedesHeldClaim =
        heldClaim !== undefined
        && (heldClaim.dataframeCacheKey !== report.dataframe_cache_key
          || heldClaim.calculationIdentity !== calculationIdentity)
      if (
        (pivotJobs[resultKey] || submitting[pivot.id])
        && !supersedesHeldClaim
      ) {
        continue
      }

      // Record before starting: updatePivot synchronously enters submitting
      // state, which reruns this effect before its request settles. The
      // per-instance set is only a fast path — the store claim is the
      // authority that serialises concurrently mounted consumers.
      attempted.current.add(attemptKey)
      const token = claimAuto(
        resultKey,
        report.dataframe_cache_key,
        calculationIdentity,
      )
      if (token === null) continue
      void updatePivot(pivot, report.dataframe_cache_key, token)
    }

    for (const attemptKey of attempted.current) {
      if (!currentAttempts.has(attemptKey)) {
        attempted.current.delete(attemptKey)
      }
    }
  }, [
    claimAuto,
    nodeId,
    pivotJobs,
    pivotResults,
    pivots,
    report,
    submitting,
    updatePivot,
  ])
}
