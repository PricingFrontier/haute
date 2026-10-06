import { useEffect, useRef } from "react"


import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import useNodeResultsStore, {
  explorePivotResultKey,
} from "../../stores/useNodeResultsStore"
import type { ExploreDataView } from "./exploreDataView"
import {
  isPivotResultFresh,
  isPivotConfigured,
  pivotCalculationIdentity,
  type ExplorePivotConfig,
} from "./pivotConfig"

type UseAutoUpdateExplorePivotsInput = {
  nodeId: string
  pivots: readonly ExplorePivotConfig[]
  report: ExploreDataView | null
  submitting: Readonly<Record<string, boolean>>
  updatePivot: (
    pivot: ExplorePivotConfig,
    requestedDataVersion?: string | null,
    autoClaimToken?: number,
  ) => Promise<void>
}

function automaticAttemptKey(
  nodeId: string,
  pivotId: string,
  dataVersion: string,
  calculationIdentity: string,
  executionGeneration: number,
): string {
  return JSON.stringify([
    nodeId,
    pivotId,
    dataVersion,
    calculationIdentity,
    executionGeneration,
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
  const pivotStartClaims = useNodeResultsStore((state) => state.pivotStartClaims)
  const claimAuto = useNodeResultsStore((state) => state.claimExplorePivotAuto)
  const executionGeneration = useDocumentStatusStore((state) => state.executionGeneration)
  const canExecute = useDocumentStatusStore((state) =>
    state.loadStatus === null || (state.capabilities?.can_execute === true && state.graphSynchronized),
  )
  const attempted = useRef(new Set<string>())

  useEffect(() => {
    if (!report || !canExecute) {
      attempted.current.clear()
      return
    }

    const currentAttempts = new Set<string>()
    const seenPivotIds = new Set<string>()

    for (const pivot of pivots) {
      if (seenPivotIds.has(pivot.id)) continue
      seenPivotIds.add(pivot.id)
      if (!isPivotConfigured(pivot)) continue

      const resultKey = explorePivotResultKey(nodeId, pivot.id)
      const cached = pivotResults[resultKey]
      const calculationIdentity = pivotCalculationIdentity(pivot)
      const attemptKey = automaticAttemptKey(
        nodeId,
        pivot.id,
        report.data_version,
        calculationIdentity,
        executionGeneration,
      )
      currentAttempts.add(attemptKey)

      const fresh = isPivotResultFresh(
        cached,
        report.data_version,
        calculationIdentity,
      )
      const failedCurrentAttempt = Boolean(
        cached?.error
          && cached.lastAttemptedCalculationIdentity === calculationIdentity
          && cached.lastAttemptedDataframeCacheKey
            === report.data_version,
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
        useNodeResultsStore.getState().pivotStartClaims[resultKey]
      const supersedesHeldClaim =
        heldClaim !== undefined
        && (heldClaim.dataVersion !== report.data_version
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
      const token = claimAuto(
        resultKey,
        nodeId,
        report.data_version,
        calculationIdentity,
      )
      if (token === null) continue
      attempted.current.add(attemptKey)
      void updatePivot(pivot, report.data_version, token)
    }

    for (const attemptKey of attempted.current) {
      if (!currentAttempts.has(attemptKey)) {
        attempted.current.delete(attemptKey)
      }
    }
  }, [
    canExecute,
    claimAuto,
    executionGeneration,
    nodeId,
    pivotJobs,
    pivotResults,
    pivotStartClaims,
    pivots,
    report,
    submitting,
    updatePivot,
  ])
}
