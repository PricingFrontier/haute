import { useEffect, useRef, useState } from "react"
import { ApiError, getTrainStatus } from "../../api/client"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import useGraphStore from "../../stores/useGraphStore"
import useNodeResultsStore, { type CachedTrainResult } from "../../stores/useNodeResultsStore"
import {
  clearTrainedJobHandle,
  readTrainedJobHandle,
  trainingLineage,
  type TrainingLineageInput,
} from "../../utils/trainedJobHandles"

/**
 * After a browser reload, puts a node's remembered completed training result
 * back from the job status endpoint (the results store remembers it when the
 * job completes).
 *
 * This reads the status once; it is not a polling loop (running jobs are
 * polled by useBackgroundJobs). The restored result is current only when the
 * training lineage of the graph the editor would submit now matches the one
 * recorded from the submitted request; otherwise it is restored as stale.
 * Returns true when a remembered result is no longer held by the server, so
 * the Export pane can say so instead of simply showing nothing to export.
 */
export function useTrainedJobRestore(
  nodeId: string,
  cachedResult: CachedTrainResult | undefined,
  hasTrainJob: boolean,
  graph: () => TrainingLineageInput,
): boolean {
  // The status read is async; compare against the graph as it is when it returns.
  const graphRef = useRef(graph)
  useEffect(() => {
    graphRef.current = graph
  }, [graph])
  const sourceFile = useDocumentStatusStore((state) => state.sourceFile)
  const restoreTrainResult = useNodeResultsStore((state) => state.restoreTrainResult)
  const [expiredJobId, setExpiredJobId] = useState<string | null>(null)
  const hasCachedResult = Boolean(cachedResult)
  useEffect(() => {
    if (!sourceFile || !nodeId || hasCachedResult || hasTrainJob) return
    const handle = readTrainedJobHandle(sourceFile, nodeId)
    if (!handle) return
    const controller = new AbortController()
    const forget = () => {
      clearTrainedJobHandle(sourceFile, nodeId)
      setExpiredJobId(handle.jobId)
    }
    getTrainStatus(handle.jobId, { signal: controller.signal }).then(
      (status) => {
        if (controller.signal.aborted) return
        if (status.status === "completed" && status.result && status.result.status !== "error") {
          const currentLineage = trainingLineage(graphRef.current())
          restoreTrainResult(nodeId, {
            result: status.result,
            terminalStatus: status,
            jobId: handle.jobId,
            configHash: handle.configHash,
            source: handle.source,
            // -1 never matches a real structural version, so a result trained
            // against a different graph reads as stale.
            structuralVersion:
              currentLineage === handle.lineage
                ? useGraphStore.getState().structuralVersion
                : -1,
          })
        } else {
          forget()
        }
      },
      (caught: unknown) => {
        if (controller.signal.aborted) return
        // A job the server no longer holds is forgotten; a transient failure
        // keeps the handle so the next visit tries again.
        if (caught instanceof ApiError && caught.status === 404) forget()
      },
    )
    return () => controller.abort()
  }, [sourceFile, nodeId, hasCachedResult, hasTrainJob, restoreTrainResult])
  return expiredJobId !== null && !cachedResult
}
