import { useEffect, useRef } from "react"
import { ApiError, getTrainStatus } from "../../api/client"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import useGraphStore from "../../stores/useGraphStore"
import useNodeResultsStore, { type CachedTrainResult } from "../../stores/useNodeResultsStore"
import { ask, newest } from "../../utils/answers"
import { useRequestKind } from "../../hooks/useRequestKind"
import {
  clearTrainedJobHandle,
  readTrainedJobHandle,
  trainingLineage,
  type TrainingLineageInput,
} from "../../utils/trainedJobHandles"

/**
 * After a browser reload, puts a node's remembered completed training result
 * back from the job status endpoint (the results store remembers it when the
 * job completes). The handle and the result are the ones kept under the node's
 * result key, `resultKey`: inside a submodel, its occurrence's own.
 *
 * This reads the status once; it is not a polling loop (running jobs are
 * polled by useBackgroundJobs). The restored result is current only when the
 * training lineage of the graph the editor would submit now matches the one
 * recorded from the submitted request; otherwise it is restored as stale.
 * A remembered result the server no longer holds is recorded in the results
 * store (`expiredTrainJobs`), so the Export pane can say so instead of simply
 * showing nothing. A saved version's editor (the comparison view, which runs
 * nothing) passes `enabled: false`: the remembered job is the open pipeline's.
 */
export function useTrainedJobRestore(
  resultKey: string,
  cachedResult: CachedTrainResult | undefined,
  hasTrainJob: boolean,
  graph: () => TrainingLineageInput,
  enabled: boolean,
): void {
  // The status read is async; compare against the graph as it is when it returns.
  const graphRef = useRef(graph)
  useEffect(() => {
    graphRef.current = graph
  }, [graph])
  const sourceFile = useDocumentStatusStore((state) => state.sourceFile)
  const restoreTrainResult = useNodeResultsStore((state) => state.restoreTrainResult)
  const markTrainResultExpired = useNodeResultsStore((state) => state.markTrainResultExpired)
  const hasCachedResult = Boolean(cachedResult)
  const reads = useRequestKind()
  useEffect(() => {
    if (!enabled || !sourceFile || !resultKey || hasCachedResult || hasTrainJob) return
    const handle = readTrainedJobHandle(sourceFile, resultKey)
    if (!handle) return
    const forget = () => {
      clearTrainedJobHandle(sourceFile, resultKey)
      markTrainResultExpired(resultKey, handle.jobId)
    }
    const asked = ask([newest(reads)])
    void asked.answer((signal) => getTrainStatus(handle.jobId, { signal }), {
      ok: (status) => {
        if (status.status === "completed" && status.result && status.result.status !== "error") {
          const currentLineage = trainingLineage(graphRef.current())
          restoreTrainResult(resultKey, {
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
      failed: (caught) => {
        // A job the server no longer holds is forgotten; a transient failure
        // keeps the handle so the next visit tries again.
        if (caught instanceof ApiError && caught.status === 404) forget()
      },
    })
    return () => asked.release()
  }, [enabled, sourceFile, resultKey, hasCachedResult, hasTrainJob, restoreTrainResult, markTrainResultExpired, reads])
}
